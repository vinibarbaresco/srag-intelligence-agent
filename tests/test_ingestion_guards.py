"""Guardas estruturais da carga e a verificacao ponta a ponta dos artefatos.

Tres classes de garantia que nenhum teste de regra isolada alcanca:

1. **Fronteira de minimizacao** -- uma coluna da denylist que chegue ao fim do
   pipeline ou ao banco interrompe a carga, em vez de ser gravada;
2. **Contrato com a camada analitica** -- um Parquet sem as colunas semanticas
   derivadas falha na carga, com a causa explicita, e nao na primeira consulta;
3. **Imutabilidade do dado bruto** -- a execucao do pipeline nao toca o arquivo
   de origem. Verificado por hash e por data de modificacao, e nao por leitura
   do codigo.

E, ao final, a carga completa sobre um CSV sintetico: Parquet, relatorio de
qualidade, historico de ingestao e linha de base de esquema, com as secoes que
o enunciado exige.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.data.cleaning import clean_chunk
from src.data.quality import QualityReport
from src.data.schema import (
    ALLOWED_COLUMNS,
    DENIED_COLUMNS,
    DERIVED_SEMANTIC_COLUMNS,
    EPIWEEK_DERIVED_COLUMNS,
    assert_no_denied_columns,
)

# =============================================================================
# CSV sintetico e carga isolada
# =============================================================================


def _linha(**override: str) -> str:
    valores = {coluna: "" for coluna in ALLOWED_COLUMNS}
    valores.update(
        {
            "DT_SIN_PRI": "2026-05-08",
            "DT_DIGITA": "2026-05-20",
            "SEM_PRI": "19",
            "SG_UF_NOT": "SP",
            "SG_UF": "SP",
            "NU_IDADE_N": "45",
            "TP_IDADE": "3",
            "EVOLUCAO": "1",
            "DT_EVOLUCA": "2026-05-12",
            "CLASSI_FIN": "5",
            "CRITERIO": "1",
            "HOSPITAL": "1",
            "UTI": "2",
            "SUPORT_VEN": "3",
            "NOSOCOMIAL": "2",
            "VACINA_COV": "1",
            "VACINA": "2",
            "CS_SEXO": "F",
        }
    )
    valores.update(override)
    return ";".join(f'"{valores[coluna]}"' for coluna in ALLOWED_COLUMNS)


def _escreve_csv(path: Path, linhas: list[str]) -> Path:
    cabecalho = ";".join(f'"{coluna}"' for coluna in ALLOWED_COLUMNS)
    path.write_text("\n".join([cabecalho, *linhas]) + "\n", encoding="utf-8")
    return path


def _escreve_manifesto(path: Path, csv_path: Path, ano: int = 2026) -> None:
    path.write_text(
        json.dumps(
            {
                str(ano): {
                    "year": ano,
                    "filename": csv_path.name,
                    "path": str(csv_path),
                    "url": None,
                    "origin": "arquivo local de teste",
                    "size_bytes": csv_path.stat().st_size,
                    "sha256": "0" * 64,
                    "downloaded_at": "2026-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def carga_isolada(tmp_path, monkeypatch):
    """Raiz de dados propria, para nao tocar a base sintetica da sessao."""
    from src.config import Settings

    settings = Settings(data_root=tmp_path, reporting_lag_days=21)
    settings.ensure_directories()
    monkeypatch.setattr("src.data.preprocess.get_settings", lambda: settings)
    return settings


# =============================================================================
# Guardas estruturais
# =============================================================================


class TestFronteiraDeMinimizacao:
    """A denylist so cumpre sua funcao se a violacao interromper alguma coisa."""

    @pytest.mark.parametrize("proibida", ["NU_NOTIFIC", "DT_NASC", "CS_RACA", "LOTE_1_COV"])
    def test_guarda_falha_para_cada_classe_de_coluna_proibida(self, proibida):
        """Identificador, quase-identificador, dado sensivel e rastro vacinal."""
        with pytest.raises(ValueError, match="dados pessoais"):
            assert_no_denied_columns(["DT_SIN_PRI", proibida])

    def test_mensagem_da_guarda_nomeia_a_coluna_e_o_motivo(self):
        """Quem investiga a falha precisa saber qual coluna e por que ela e proibida."""
        with pytest.raises(ValueError) as erro:
            assert_no_denied_columns(["DT_NASC"])

        mensagem = str(erro.value)
        assert "DT_NASC" in mensagem
        assert DENIED_COLUMNS["DT_NASC"] in mensagem

    def test_pipeline_interrompe_quando_coluna_proibida_atravessa(self):
        """A guarda roda no fim de `clean_chunk`, sobre o que seria gravado."""
        base = {coluna: [""] for coluna in ALLOWED_COLUMNS}
        base.update(
            {
                "DT_SIN_PRI": ["2026-05-08"],
                "DT_DIGITA": ["2026-05-20"],
                "NU_IDADE_N": ["45"],
                "TP_IDADE": ["3"],
                "DT_NASC": ["1981-02-03"],  # coluna proibida injetada no bloco
            }
        )
        chunk = pd.DataFrame(base, dtype="string")

        with pytest.raises(ValueError, match="DT_NASC"):
            clean_chunk(chunk, 2026, QualityReport())

    def test_guarda_lista_todas_as_violacoes_de_uma_vez(self):
        """Corrigir uma por execucao esconderia o tamanho do problema."""
        with pytest.raises(ValueError) as erro:
            assert_no_denied_columns(["NU_NOTIFIC", "DT_NASC", "DT_SIN_PRI"])

        assert "NU_NOTIFIC" in str(erro.value)
        assert "DT_NASC" in str(erro.value)

    def test_toda_coluna_da_denylist_e_rejeitada(self):
        """Nenhuma entrada da lista pode ser decorativa."""
        for proibida in DENIED_COLUMNS:
            with pytest.raises(ValueError):
                assert_no_denied_columns([proibida])


class TestContratoComACamadaAnalitica:
    """A carga do banco falha cedo, com a causa, em vez de tarde, na consulta."""

    def _parquet(self, tmp_path: Path, frame: pd.DataFrame) -> Path:
        destino = tmp_path / "srag_cases.parquet"
        frame.to_parquet(destino, index=False)
        return destino

    def test_carga_falha_se_faltar_coluna_semantica_derivada(self, tmp_path):
        from src.data.load_database import load_database

        frame = clean_chunk(
            pd.DataFrame(
                {
                    **{coluna: [""] for coluna in ALLOWED_COLUMNS},
                    "DT_SIN_PRI": ["2026-05-08"],
                    "DT_DIGITA": ["2026-05-20"],
                    "NU_IDADE_N": ["45"],
                    "TP_IDADE": ["3"],
                },
                dtype="string",
            ),
            2026,
            QualityReport(),
        )
        mutilado = frame.drop(columns=["eh_obito_srag"])

        with pytest.raises(ValueError, match="eh_obito_srag"):
            load_database(self._parquet(tmp_path, mutilado), tmp_path / "srag.duckdb")

    def test_carga_falha_se_faltar_coluna_de_semana_epidemiologica(self, tmp_path):
        from src.data.load_database import load_database

        frame = clean_chunk(
            pd.DataFrame(
                {
                    **{coluna: [""] for coluna in ALLOWED_COLUMNS},
                    "DT_SIN_PRI": ["2026-05-08"],
                    "DT_DIGITA": ["2026-05-20"],
                },
                dtype="string",
            ),
            2026,
            QualityReport(),
        )
        # `ano_sintomas` e a coluna de semana epidemiologica que a view nao
        # referencia por nome: e por ela que a guarda explicita da carga se
        # manifesta, em vez do erro de binder do SQL.
        mutilado = frame.drop(columns=["ano_sintomas"])

        with pytest.raises(ValueError, match="ano_sintomas"):
            load_database(self._parquet(tmp_path, mutilado), tmp_path / "srag.duckdb")

    def test_mensagem_de_falha_diz_o_que_reexecutar(self, tmp_path):
        from src.data.load_database import load_database

        frame = clean_chunk(
            pd.DataFrame(
                {
                    **{coluna: [""] for coluna in ALLOWED_COLUMNS},
                    "DT_SIN_PRI": ["2026-05-08"],
                    "DT_DIGITA": ["2026-05-20"],
                },
                dtype="string",
            ),
            2026,
            QualityReport(),
        )

        with pytest.raises(ValueError) as erro:
            load_database(
                self._parquet(tmp_path, frame.drop(columns=["status_caso"])),
                tmp_path / "srag.duckdb",
            )
        assert "src.data.preprocess" in str(erro.value)

    def test_carga_falha_se_coluna_proibida_alcancar_o_banco(self, tmp_path):
        """Segunda barreira, no banco: a do pipeline pode ser contornada por um Parquet."""
        from src.data.load_database import load_database

        frame = clean_chunk(
            pd.DataFrame(
                {
                    **{coluna: [""] for coluna in ALLOWED_COLUMNS},
                    "DT_SIN_PRI": ["2026-05-08"],
                    "DT_DIGITA": ["2026-05-20"],
                },
                dtype="string",
            ),
            2026,
            QualityReport(),
        )
        frame["NU_NOTIFIC"] = ["123456"]

        with pytest.raises(ValueError, match="NU_NOTIFIC"):
            load_database(self._parquet(tmp_path, frame), tmp_path / "srag.duckdb")

    def test_todas_as_colunas_exigidas_estao_declaradas(self):
        """A lista verificada na carga e a mesma que o tratamento promete produzir."""
        exigidas = set(DERIVED_SEMANTIC_COLUMNS) | set(EPIWEEK_DERIVED_COLUMNS)
        produzidas = set(
            clean_chunk(
                pd.DataFrame(
                    {
                        **{coluna: [""] for coluna in ALLOWED_COLUMNS},
                        "DT_SIN_PRI": ["2026-05-08"],
                        "DT_DIGITA": ["2026-05-20"],
                    },
                    dtype="string",
                ),
                2026,
                QualityReport(),
            ).columns
        )
        assert exigidas <= produzidas


class TestDadoBrutoNaoEModificado:
    """A camada raw e imutavel: a carga le, nunca escreve."""

    def test_csv_de_origem_tem_o_mesmo_hash_depois_da_carga(self, carga_isolada, tmp_path):
        from src.data.preprocess import preprocess

        csv_path = _escreve_csv(tmp_path / "INFLUD26-teste.csv", [_linha(), _linha(UTI="1")])
        _escreve_manifesto(carga_isolada.raw_manifest_path, csv_path)

        antes_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        antes_mtime = csv_path.stat().st_mtime_ns
        antes_tamanho = csv_path.stat().st_size

        preprocess([2026])

        assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == antes_hash
        assert csv_path.stat().st_mtime_ns == antes_mtime
        assert csv_path.stat().st_size == antes_tamanho

    def test_carga_nao_cria_nem_remove_arquivos_no_diretorio_bruto(self, carga_isolada, tmp_path):
        """Nada de arquivo temporario, backup ou reescrita ao lado do original."""
        from src.data.preprocess import preprocess

        bruto = tmp_path / "bruto"
        bruto.mkdir()
        csv_path = _escreve_csv(bruto / "INFLUD26-teste.csv", [_linha()])
        _escreve_manifesto(carga_isolada.raw_manifest_path, csv_path)

        antes = sorted(p.name for p in bruto.iterdir())
        preprocess([2026])
        assert sorted(p.name for p in bruto.iterdir()) == antes

    def test_reexecutar_a_carga_nao_altera_o_bruto(self, carga_isolada, tmp_path):
        """Idempotencia do lado da leitura: duas cargas, o mesmo arquivo de origem."""
        from src.data.preprocess import preprocess

        csv_path = _escreve_csv(tmp_path / "INFLUD26-teste.csv", [_linha()])
        _escreve_manifesto(carga_isolada.raw_manifest_path, csv_path)

        preprocess([2026])
        primeiro = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        preprocess([2026])

        assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == primeiro


# =============================================================================
# Carga ponta a ponta
# =============================================================================


class TestCargaCompletaProduzOsArtefatos:
    """Uma execucao real sobre CSV pequeno, com todos os artefatos verificados."""

    @pytest.fixture
    def carga(self, carga_isolada, tmp_path):
        from src.data.preprocess import preprocess

        linhas = [
            _linha(),
            _linha(),  # identica: exercita a contagem de duplicidade
            _linha(EVOLUCAO="2", UTI="1", DT_ENTUTI="2026-05-09", DT_SAIDUTI="2026-05-11"),
            _linha(EVOLUCAO="9", CLASSI_FIN="", UTI="9"),  # ausencia declarada
            _linha(EVOLUCAO="7"),  # codigo fora do dominio
            _linha(NU_IDADE_N="200"),  # ajuste: idade anulada
            _linha(SG_UF_NOT="ZZ"),  # ajuste: uf anulada
            _linha(DT_SIN_PRI="", DT_EVOLUCA=""),  # sem eixo temporal
        ]
        csv_path = _escreve_csv(tmp_path / "INFLUD26-teste.csv", linhas)
        _escreve_manifesto(carga_isolada.raw_manifest_path, csv_path)

        parquet = preprocess([2026])
        relatorio = json.loads(carga_isolada.quality_report_path.read_text(encoding="utf-8"))
        return carga_isolada, parquet, relatorio

    def test_os_quatro_artefatos_sao_gravados(self, carga):
        settings, parquet, _ = carga

        assert parquet.exists()
        assert settings.quality_report_path.exists()
        assert settings.ingestion_history_path.exists()
        assert settings.schema_baseline_path.exists()

    def test_parquet_respeita_a_minimizacao_e_traz_as_derivadas(self, carga):
        _, parquet, _ = carga
        frame = pd.read_parquet(parquet)

        assert len(frame) == 8
        assert not set(frame.columns) & set(DENIED_COLUMNS)
        assert not {"idade_anos", "NU_IDADE_N", "TP_IDADE"} & set(frame.columns)
        assert set(DERIVED_SEMANTIC_COLUMNS) <= set(frame.columns)
        assert "faixa_etaria" in frame.columns

    def test_relatorio_traz_a_secao_raw(self, carga):
        """Arquivos de origem, encoding detectado e proveniencia por arquivo."""
        _, _, relatorio = carga

        assert relatorio["source_files"] == ["INFLUD26-teste.csv"]
        assert relatorio["source"]
        proveniencia = relatorio["provenance"][0]
        assert proveniencia["year"] == 2026
        assert proveniencia["encoding"] == "utf-8"
        assert proveniencia["origin"] == "arquivo local de teste"
        assert proveniencia["sha256"]

    def test_relatorio_traz_a_secao_processed(self, carga):
        """Quantas linhas entraram, quantas sairam e quantas foram ajustadas."""
        _, _, relatorio = carga

        assert relatorio["rows_read"] == 8
        assert relatorio["rows_written"] == 8
        assert relatorio["rows_dropped"] == 0  # nenhum registro e descartado
        assert relatorio["rows_adjusted"] == 2  # idade anulada e uf anulada
        assert relatorio["run_id"]
        assert relatorio["generated_at"]

    def test_relatorio_traz_a_secao_data_quality(self, carga):
        _, _, relatorio = carga
        regras = relatorio["rules"]

        for chave in (
            "datas_nao_parseaveis_por_coluna",
            "valores_nulos_por_coluna",
            "codigo_9_ignorado_por_coluna",
            "codigo_fora_do_dominio_por_coluna",
            "codigo_ilegivel_por_coluna",
            "completude_por_coluna_categorica",
            "linhas_identicas_nas_colunas_persistidas",
            "uf_fora_do_dominio",
            "idade_fora_do_intervalo_plausivel",
            "semana_epidemiologica",
        ):
            assert chave in regras, chave

        assert regras["uf_fora_do_dominio"] == 1
        assert regras["idade_fora_do_intervalo_plausivel"] == 1
        assert regras["codigo_fora_do_dominio_por_coluna"]["EVOLUCAO"] == 1
        assert regras["codigo_9_ignorado_por_coluna"]["UTI"] == 1
        assert regras["linhas_identicas_nas_colunas_persistidas"] >= 1

    def test_relatorio_traz_a_secao_transformations(self, carga):
        """As regras aplicadas, na ordem, vindas da mesma estrutura que executa."""
        from src.data.cleaning import CLEANING_PIPELINE

        _, _, relatorio = carga
        pipeline = relatorio["pipeline"]

        assert len(pipeline) == len(CLEANING_PIPELINE)
        assert [etapa["name"] for etapa in pipeline] == [r.name for r in CLEANING_PIPELINE]
        assert all(etapa["description"] for etapa in pipeline)
        assert [etapa["ordem"] for etapa in pipeline] == [
            str(n) for n in range(1, len(CLEANING_PIPELINE) + 1)
        ]

    def test_relatorio_traz_a_secao_schema_com_warnings_e_errors(self, carga):
        """`schema` e a comparacao contra a safra anterior do mesmo ano."""
        _, _, relatorio = carga
        schema = relatorio["schema"]

        assert "errors" in schema and "warnings" in schema
        assert isinstance(schema["errors"], list)
        assert isinstance(schema["warnings"], list)
        # Primeira carga do ano: nao ha safra anterior, entao nada a comparar.
        assert schema["errors"] == []
        assert schema["baseline_estabelecida_para"] == [2026]
        assert schema["limiares"]
        assert schema["resumo"]

    def test_relatorio_descreve_cada_exclusao_com_regra_motivo_volume_e_percentual(self, carga):
        """O que exclui registro da camada analitica e declarado por inteiro.

        Regra (nome da flag), motivo (o significado), registros afetados e
        percentual sobre o lido. Sem o percentual, "1 registro" nao diz se e
        ruido ou se e um terco da base.
        """
        _, _, relatorio = carga
        flags = relatorio["coherence_flags"]

        for nome, detalhe in flags.items():
            assert detalhe["significado"].strip(), nome
            assert isinstance(detalhe["registros"], int), nome
            assert isinstance(detalhe["percentual"], float), nome

        excluem = [n for n, d in flags.items() if d["exclui_da_view_analitica"]]
        assert excluem == ["flag_data_invalida"]
        # O registro sem DT_SIN_PRI: 1 de 8 lidos.
        assert flags["flag_data_invalida"]["registros"] == 1
        assert flags["flag_data_invalida"]["percentual"] == pytest.approx(12.5)

    def test_relatorio_descreve_cada_ajuste_de_valor(self, carga):
        """Alteracao de valor nao e exclusao, e tem o seu proprio inventario."""
        _, _, relatorio = carga
        ajustes = relatorio["adjustments"]

        assert ajustes["por_codigo"]["idade_anulada"] == 1
        assert ajustes["por_codigo"]["uf_anulada:SG_UF_NOT"] == 1
        assert ajustes["como_localizar"]
        assert all(motivo.strip() for motivo in ajustes["significado"].values())

    def test_historico_de_ingestao_e_uma_linha_json_por_carga(self, carga):
        settings, _, relatorio = carga
        linhas = settings.ingestion_history_path.read_text(encoding="utf-8").splitlines()

        assert len(linhas) == 1
        registro = json.loads(linhas[0])
        assert registro["run_id"] == relatorio["run_id"]
        assert registro["rows_read"] == 8
        assert registro["source_files"] == ["INFLUD26-teste.csv"]
        assert registro["provenance"][0]["encoding"] == "utf-8"

    def test_segunda_carga_acrescenta_linha_sem_apagar_a_anterior(self, carga):
        """O historico existe para comparar cargas: reescrever perderia a comparacao."""
        from src.data.preprocess import preprocess

        settings, _, _ = carga
        preprocess([2026])

        linhas = settings.ingestion_history_path.read_text(encoding="utf-8").splitlines()
        assert len(linhas) == 2
        assert json.loads(linhas[0])["run_id"] != json.loads(linhas[1])["run_id"]

    def test_linha_de_base_de_esquema_guarda_o_retrato_do_arquivo_bruto(self, carga):
        settings, _, _ = carga
        baseline = json.loads(settings.schema_baseline_path.read_text(encoding="utf-8"))

        assert "2026" in baseline["years"]
        retrato = baseline["years"]["2026"]
        assert retrato["filename"] == "INFLUD26-teste.csv"
        assert retrato["record_count"] == 8
        # O cabecalho inteiro, e nao so a allowlist: e assim que uma coluna nova
        # ou sumida na fonte e percebida.
        assert set(ALLOWED_COLUMNS) <= set(retrato["raw_columns"])

    def test_relatorio_declara_o_que_nao_faz(self, carga):
        """As notas sao parte da entrega: elas fixam o que o pipeline promete."""
        _, _, relatorio = carga
        notas = " ".join(relatorio["notes"])

        assert "Nenhum registro e excluido" in notas
        assert "deduplicadas" in notas
        assert "dados pessoais" in notas


class TestPrincipalTrataSchemaDriftError:
    """`main.py --setup` nao deve deixar `SchemaDriftError` escapar como traceback.

    Antes desta correcao, `SchemaDriftError` (subclasse de `RuntimeError`, nao
    de `ValueError`) nao estava na clausula `except` de `_run_setup`, e o
    parser principal nao tinha `--accept-drift` -- so o modulo isolado
    (`python -m src.data.preprocess`) tinha o caminho de recuperacao completo.
    Uma mudanca de esquema classificada como ERROR no comando que o README
    ensina (`python main.py --setup`) terminava em traceback nao tratado, sem
    o caminho de destravamento que a mensagem do proprio erro recomenda.
    """

    @pytest.fixture
    def _isolar_data_root(self, tmp_path, monkeypatch):
        """DATA_ROOT proprio para os quatro modulos que `_run_setup` toca.

        Diferente de `carga_isolada` (que so troca `get_settings` dentro de
        `src.data.preprocess`), aqui `_run_setup` tambem passa por
        `src.data.download`, `src.data.load_database` e `src.news.ingest` --
        cada um resolve `get_settings()` no proprio modulo. A unica forma de
        isolar os quatro ao mesmo tempo e o mecanismo global de `DATA_ROOT` +
        `reset_settings_cache()`, o mesmo que os demais testes de referencia
        usam.
        """
        from src.config import reset_settings_cache

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))
        reset_settings_cache()
        try:
            yield tmp_path
        finally:
            monkeypatch.undo()
            reset_settings_cache()

    @staticmethod
    def _escreve_csv_sem_coluna(path: Path, coluna_removida: str, linha_valores: dict) -> Path:
        """Mesmo formato de `_escreve_csv`, mas com uma coluna inteira ausente
        do cabecalho -- e assim que `compare()` detecta `missing_columns`.
        """
        colunas = [c for c in ALLOWED_COLUMNS if c != coluna_removida]
        cabecalho = ";".join(f'"{c}"' for c in colunas)
        linha = ";".join(f'"{linha_valores[c]}"' for c in colunas)
        path.write_text("\n".join([cabecalho, linha]) + "\n", encoding="utf-8")
        return path

    def _valores_padrao(self) -> dict:
        valores = {coluna: "" for coluna in ALLOWED_COLUMNS}
        valores.update(
            {
                "DT_SIN_PRI": "2026-05-08",
                "DT_DIGITA": "2026-05-20",
                "SEM_PRI": "19",
                "SG_UF_NOT": "SP",
                "SG_UF": "SP",
                "NU_IDADE_N": "45",
                "TP_IDADE": "3",
                "EVOLUCAO": "1",
                "DT_EVOLUCA": "2026-05-12",
                "CLASSI_FIN": "5",
                "CRITERIO": "1",
                "HOSPITAL": "1",
                "UTI": "2",
                "SUPORT_VEN": "3",
                "NOSOCOMIAL": "2",
                "VACINA_COV": "1",
                "VACINA": "2",
                "CS_SEXO": "F",
            }
        )
        return valores

    def test_drift_error_vira_mensagem_controlada_e_accept_drift_destrava(
        self, _isolar_data_root, monkeypatch
    ):
        import main as entrypoint

        # `ingest_news` e importado localmente dentro de `_run_setup`; para
        # que o monkeypatch valha, precisa alterar a funcao na origem
        # (`src.news.ingest`), nao um atributo de `main`.
        monkeypatch.setattr(
            "src.news.ingest.ingest_news",
            lambda trail=None: {
                "noticias_coletadas": 0,
                "noticias_gravadas": 0,
                "feeds_com_falha": [],
            },
        )

        tmp_path = _isolar_data_root
        valores = self._valores_padrao()

        # Primeira carga: estabelece a linha de base, sem comparacao possivel.
        csv_completo = _escreve_csv(tmp_path / "INFLUD26-completo.csv", [_linha()])
        status = entrypoint._run_setup(csv_paths=[csv_completo])
        assert status == 0

        # Segunda safra do MESMO ano, sem DT_SIN_PRI no cabecalho -- coluna da
        # allowlist ausente e ERROR em compare() (src/data/drift.py).
        csv_incompleto = self._escreve_csv_sem_coluna(
            tmp_path / "INFLUD26-incompleto.csv", "DT_SIN_PRI", valores
        )

        status = entrypoint._run_setup(csv_paths=[csv_incompleto])

        # Nao pode propagar excecao (a asserção acima ja e a prova: se
        # SchemaDriftError escapasse, o teste teria parado com um traceback
        # em vez de chegar aqui). O contrato e retorno controlado, codigo 1.
        assert status == 1

        # Com --accept-drift, a mesma safra incompleta destrava a carga.
        status = entrypoint._run_setup(csv_paths=[csv_incompleto], accept_drift=True)
        assert status == 0

    def test_accept_drift_esta_disponivel_no_parser_principal(self):
        import main as entrypoint

        parser = entrypoint.build_parser()
        args = parser.parse_args(["--setup", "--accept-drift"])
        assert args.accept_drift is True

        args = parser.parse_args(["--setup"])
        assert args.accept_drift is False
