"""Testes da camada de banco analitico.

Verificam que a view compartilhada pelas metricas aplica o mesmo recorte para
todos os indicadores, que o banco nao contem colunas com dado pessoal e que a
conexao usada pelas tools e, de fato, somente leitura.
"""

from __future__ import annotations

import duckdb
import pytest

from src.data.load_database import TABLE_AUDIT, TABLE_CASES, VIEW_ANALYTICS, connect
from src.data.schema import DENIED_COLUMNS


class TestEstruturaDoBanco:
    def test_tabela_e_views_existem(self, connection):
        tabelas = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert {TABLE_CASES, VIEW_ANALYTICS, TABLE_AUDIT} <= tabelas

    def test_banco_nao_contem_coluna_com_dado_pessoal(self, connection):
        colunas = {row[0] for row in connection.execute(f"DESCRIBE {TABLE_CASES}").fetchall()}
        assert not colunas & set(DENIED_COLUMNS)

    def test_tabela_de_auditoria_esta_pronta_para_consulta(self, connection):
        colunas = {row[0] for row in connection.execute(f"DESCRIBE {TABLE_AUDIT}").fetchall()}
        assert {"run_id", "node", "tool", "status", "duration_ms", "error"} <= colunas


class TestViewAnalitica:
    def test_exclui_registros_inconsistentes_mas_preserva_na_tabela(self, connection):
        total = connection.execute(f"SELECT count(*) FROM {TABLE_CASES}").fetchone()[0]
        analitico = connection.execute(f"SELECT count(*) FROM {VIEW_ANALYTICS}").fetchone()[0]
        assert analitico < total  # a base sintetica tem 1 registro marcado

    def test_exclui_registros_sem_data_de_sintomas(self, connection):
        nulos = connection.execute(
            f"SELECT count(*) FROM {VIEW_ANALYTICS} WHERE data_sintomas IS NULL"
        ).fetchone()[0]
        assert nulos == 0

    def test_flags_derivadas_seguem_o_dicionario_de_dados(self, connection):
        row = connection.execute(
            f"""
            SELECT
                count(*) FILTER (WHERE eh_obito_srag AND EVOLUCAO <> 2),
                count(*) FILTER (WHERE caso_encerrado AND EVOLUCAO NOT IN (1,2,3)),
                count(*) FILTER (WHERE teve_admissao_uti AND UTI <> 1),
                count(*) FILTER (WHERE uti_informado AND UTI NOT IN (1,2)),
                count(*) FILTER (WHERE vacinado_covid AND VACINA_COV <> 1)
            FROM {VIEW_ANALYTICS}
            """
        ).fetchone()
        assert row == (0, 0, 0, 0, 0)

    def test_codigo_9_nunca_entra_nas_flags_afirmativas(self, connection):
        row = connection.execute(
            f"""
            SELECT count(*) FROM {VIEW_ANALYTICS}
            WHERE (UTI = 9 AND (teve_admissao_uti OR uti_informado))
               OR (EVOLUCAO = 9 AND (eh_obito_srag OR caso_encerrado))
               OR (VACINA_COV = 9 AND (vacinado_covid OR vacina_covid_informada))
            """
        ).fetchone()[0]
        assert row == 0


class TestConexaoSomenteLeitura:
    def test_conexao_padrao_recusa_escrita(self, connection):
        with pytest.raises(duckdb.Error):
            connection.execute(f"DELETE FROM {TABLE_CASES}")

    def test_conexao_padrao_recusa_criacao_de_objeto(self, connection):
        with pytest.raises(duckdb.Error):
            connection.execute("CREATE TABLE intruso (id INTEGER)")

    def test_banco_inexistente_orienta_a_carga(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="load_database"):
            with connect(read_only=True, path=tmp_path / "ausente.duckdb"):
                pass


class TestParametrizacaoDeConsulta:
    def test_valor_de_filtro_e_tratado_como_dado_e_nao_como_sql(self, connection):
        # Um valor malicioso chega por binding e nao altera a estrutura da query:
        # o resultado e simplesmente vazio.
        total = connection.execute(
            f"SELECT count(*) FROM {VIEW_ANALYTICS} WHERE SG_UF_NOT = ?",
            ["'; DROP TABLE srag_cases; --"],
        ).fetchone()[0]
        assert total == 0
        assert connection.execute(f"SELECT count(*) FROM {TABLE_CASES}").fetchone()[0] > 0


class TestRegistroDeArquivoLocal:
    """Ingestao de um CSV ja presente em disco, sem download."""

    def test_ano_e_deduzido_do_nome_do_arquivo(self):
        from src.data.download import infer_year

        assert infer_year("INFLUD25_DATASUS-Versao26-06-2025.csv") == 2025
        assert infer_year("INFLUD26-24-08-2026.csv") == 2026
        assert infer_year("planilha_qualquer.csv") is None

    def test_manifesto_registra_proveniencia_sem_copiar_o_arquivo(self, tmp_path):
        import json

        from src.config import get_settings
        from src.data.download import register_local_file

        origem = tmp_path / "INFLUD25-teste.csv"
        origem.write_text("DT_SIN_PRI\n2025-01-01\n", encoding="latin-1")

        year, path = register_local_file(origem)
        assert year == 2025
        assert path == origem.resolve()  # o arquivo permanece onde estava

        entrada = json.loads(get_settings().raw_manifest_path.read_text(encoding="utf-8"))["2025"]
        assert entrada["path"] == str(origem.resolve())
        assert entrada["origin"].startswith("arquivo local")
        assert len(entrada["sha256"]) == 64
        assert entrada["url"] is None

    def test_arquivo_inexistente_e_recusado(self, tmp_path):
        from src.data.download import DownloadError, register_local_file

        with pytest.raises(DownloadError, match="nao encontrado"):
            register_local_file(tmp_path / "ausente.csv")

    def test_ano_indeduzivel_exige_parametro_explicito(self, tmp_path):
        from src.data.download import DownloadError, register_local_file

        arquivo = tmp_path / "dados.csv"
        arquivo.write_text("x\n", encoding="latin-1")

        with pytest.raises(DownloadError, match="deduzir o ano"):
            register_local_file(arquivo)

        year, _ = register_local_file(arquivo, year=2024)
        assert year == 2024


class TestGuardaDeColunaDerivada:
    """A carga deve falhar com a causa explicita, nao com erro cru do DuckDB.

    A view analitica nomeia varias colunas derivadas. Enquanto a verificacao
    rodava depois de cria-la, a ausencia de uma dessas colunas estourava antes
    como `BinderException` -- e so as colunas nao citadas na view chegavam a
    mensagem que explica o que fazer.
    """

    @pytest.mark.parametrize(
        "coluna",
        [
            "semana_epi",  # nomeada explicitamente na view
            "flag_data_invalida",  # usada no WHERE da view
            "ano_sintomas",  # so existe no SELECT *
        ],
    )
    def test_coluna_derivada_ausente_falha_com_causa_explicita(
        self, coluna, synthetic_database, tmp_path
    ):
        import pandas as pd

        from src.config import get_settings
        from src.data.load_database import load_database

        completo = pd.read_parquet(get_settings().processed_parquet_path)
        incompleto = tmp_path / "sem_coluna.parquet"
        completo.drop(columns=[coluna]).to_parquet(incompleto, index=False)

        with pytest.raises(ValueError, match="colunas semanticas derivadas"):
            load_database(parquet_path=incompleto, database_path=tmp_path / "srag.duckdb")
