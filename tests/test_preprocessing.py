"""Testes das regras de transformacao do CSV bruto.

Cobrem o contrato de colunas (minimizacao de dados pessoais), o parse de datas
nos dois formatos ja publicados pelo DATASUS, a normalizacao de idade e a
marcacao -- e nao exclusao -- de registros inconsistentes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaning import clean_chunk
from src.data.cleaning.dates import parse_dates
from src.data.cleaning.demographics import age_in_years
from src.data.quality import QualityReport
from src.data.schema import (
    ADJUSTMENT_CODES,
    ADJUSTMENT_COLUMN,
    ALLOWED_COLUMNS,
    DENIED_COLUMNS,
    MISSING_CODES,
    assert_no_denied_columns,
)


def _raw_chunk(**overrides) -> pd.DataFrame:
    """Bloco bruto minimo, com todas as colunas permitidas preenchidas."""
    base = {column: [""] for column in ALLOWED_COLUMNS}
    base.update(
        {
            "DT_NOTIFIC": ["2026-05-10T00:00:00.000Z"],
            "DT_SIN_PRI": ["2026-05-08T00:00:00.000Z"],
            "DT_DIGITA": ["2026-05-20T00:00:00.000Z"],
            "NU_IDADE_N": ["45"],
            "TP_IDADE": ["3"],
            "SG_UF_NOT": ["SP"],
            "SG_UF": ["SP"],
            "EVOLUCAO": ["1"],
            "CLASSI_FIN": ["5"],
            "HOSPITAL": ["1"],
            "UTI": ["2"],
            "VACINA_COV": ["1"],
            "VACINA": ["2"],
            "CS_SEXO": ["f"],
        }
    )
    base.update({key: [value] for key, value in overrides.items()})
    return pd.DataFrame(base, dtype="string")


class TestContratoDeColunas:
    def test_allowlist_e_denylist_sao_disjuntas(self):
        assert not set(ALLOWED_COLUMNS) & set(DENIED_COLUMNS)

    def test_colunas_com_dado_pessoal_sao_rejeitadas(self):
        with pytest.raises(ValueError, match="dados pessoais"):
            assert_no_denied_columns(["DT_SIN_PRI", "NU_NOTIFIC"])

    def test_colunas_permitidas_passam(self):
        assert_no_denied_columns(list(ALLOWED_COLUMNS))

    def test_denylist_documenta_o_motivo_de_cada_exclusao(self):
        assert all(reason.strip() for reason in DENIED_COLUMNS.values())

    def test_saida_transformada_nao_contem_coluna_proibida(self):
        frame = clean_chunk(_raw_chunk(), 2026, QualityReport())
        assert not set(frame.columns) & set(DENIED_COLUMNS)

    def test_idade_exata_e_acompanhada_de_faixa_agregada(self):
        frame = clean_chunk(_raw_chunk(), 2026, QualityReport())
        assert frame["faixa_etaria"].iloc[0] == "40-49"


class TestParseDeDatas:
    """A fonte ja publicou o mesmo campo em tres formatos distintos."""

    def test_formato_iso_com_sufixo_z(self):
        parsed = parse_dates(pd.Series(["2026-04-30T00:00:00.000Z"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2026-04-30")

    def test_formato_iso_simples(self):
        # Formato do INFLUD25 versao 26-06-2025, distribuido com o enunciado.
        parsed = parse_dates(pd.Series(["2024-12-29"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2024-12-29")

    def test_formato_brasileiro_como_fallback(self):
        parsed = parse_dates(pd.Series(["30/04/2026"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2026-04-30")

    def test_data_invalida_vira_nulo_e_nao_excecao(self):
        parsed = parse_dates(pd.Series(["31/02/2026", "texto", ""], dtype="string"))
        assert parsed.isna().all()

    def test_data_invalida_e_contabilizada(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(DT_EVOLUCA="data-quebrada"), 2026, report)
        assert report.invalid_dates["DT_EVOLUCA"] == 1


class TestNormalizacaoDeIdade:
    @pytest.mark.parametrize(
        ("valor", "unidade", "esperado"),
        [("45", 3, 45.0), ("18", 2, 1.5), ("365", 1, 0.999), ("0", 3, 0.0)],
    )
    def test_converte_para_anos(self, valor, unidade, esperado):
        years = age_in_years(pd.Series([valor]), pd.Series([unidade]))
        assert years.iloc[0] == pytest.approx(esperado, abs=0.01)

    def test_idade_implausivel_e_anulada_e_contabilizada(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N="200"), 2026, report)
        assert pd.isna(frame["faixa_etaria"].iloc[0])
        assert "idade_anos" not in frame.columns  # idade exata nao e persistida
        assert report.age_out_of_range == 1


class TestTratamentoDeAusencia:
    def test_codigo_9_e_preservado_e_contabilizado(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(UTI="9"), 2026, report)
        assert frame["UTI"].iloc[0] == 9
        assert report.ignored_code_counts["UTI"] == 1

    def test_codigo_9_nao_vira_nao_nem_zero(self):
        frame = clean_chunk(_raw_chunk(EVOLUCAO="9"), 2026, QualityReport())
        assert frame["EVOLUCAO"].iloc[0] == 9
        assert 9 in MISSING_CODES

    def test_uf_fora_do_dominio_e_anulada_e_contabilizada(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(SG_UF_NOT="ZZ"), 2026, report)
        assert pd.isna(frame["SG_UF_NOT"].iloc[0])
        assert report.unknown_uf == 1


class TestRegistrosInconsistentes:
    def test_sintomas_depois_da_digitacao_e_marcado_nao_removido(self):
        report = QualityReport()
        frame = clean_chunk(
            _raw_chunk(
                DT_SIN_PRI="2026-05-25T00:00:00.000Z",
                DT_DIGITA="2026-05-20T00:00:00.000Z",
            ),
            2026,
            report,
        )
        assert len(frame) == 1  # o registro permanece na base
        assert bool(frame["flag_data_invalida"].iloc[0]) is True
        assert report.coherence_flags["flag_data_invalida"] == 1

    def test_saida_de_uti_antes_da_entrada_marca_apenas_a_dimensao_uti(self):
        """O eixo temporal segue valido: o caso continua contando como caso."""
        frame = clean_chunk(
            _raw_chunk(
                DT_ENTUTI="2026-05-15T00:00:00.000Z",
                DT_SAIDUTI="2026-05-12T00:00:00.000Z",
            ),
            2026,
            QualityReport(),
        )
        assert bool(frame["flag_uti_inconsistente"].iloc[0]) is True
        assert bool(frame["flag_data_invalida"].iloc[0]) is False

    def test_internacao_antes_dos_sintomas_marca_apenas_a_internacao(self):
        frame = clean_chunk(
            _raw_chunk(
                DT_SIN_PRI="2026-05-10T00:00:00.000Z",
                DT_INTERNA="2026-05-05T00:00:00.000Z",
            ),
            2026,
            QualityReport(),
        )
        assert bool(frame["flag_internacao_inconsistente"].iloc[0]) is True
        assert bool(frame["flag_data_invalida"].iloc[0]) is False

    def test_caso_encerrado_sem_data_de_evolucao_e_marcado(self):
        frame = clean_chunk(_raw_chunk(EVOLUCAO="2", DT_EVOLUCA=""), 2026, QualityReport())
        assert bool(frame["flag_evolucao_inconsistente"].iloc[0]) is True
        assert bool(frame["flag_data_invalida"].iloc[0]) is False

    def test_sem_data_de_sintomas_invalida_o_eixo_temporal(self):
        frame = clean_chunk(_raw_chunk(DT_SIN_PRI=""), 2026, QualityReport())
        assert bool(frame["flag_data_invalida"].iloc[0]) is True

    def test_registro_valido_nao_e_marcado(self):
        frame = clean_chunk(_raw_chunk(), 2026, QualityReport())
        assert bool(frame["flag_data_invalida"].iloc[0]) is False

    def test_nenhum_registro_e_descartado(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(DT_SIN_PRI="lixo"), 2026, report)
        assert report.rows_read == report.rows_written


class TestRelatorioDeQualidade:
    def test_documenta_regras_e_contagens(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(UTI="9"), 2026, report)
        payload = report.to_dict(source_files=["INFLUD26.csv"])

        assert payload["rows_read"] == 1
        assert payload["rows_dropped"] == 0
        assert payload["rules"]["codigo_9_ignorado_por_coluna"]["UTI"] == 1
        assert any("Nenhum registro e excluido" in note for note in payload["notes"])

    def test_cada_flag_e_reportada_com_volume_e_significado(self):
        from src.data.schema import COHERENCE_FLAGS

        report = QualityReport()
        clean_chunk(_raw_chunk(), 2026, report)
        flags = report.to_dict(source_files=["INFLUD26.csv"])["coherence_flags"]

        assert set(flags) == set(COHERENCE_FLAGS)
        assert all(d["significado"] for d in flags.values())
        # Apenas a flag do eixo temporal exclui o registro da view analitica.
        excluem = [n for n, d in flags.items() if d["exclui_da_view_analitica"]]
        assert excluem == ["flag_data_invalida"]


class TestRastreamentoDeAjustes:
    """Toda alteracao de valor deve ser localizavel no proprio registro."""

    def test_registro_intacto_nao_recebe_marcacao(self):
        frame = clean_chunk(_raw_chunk(), 2026, QualityReport())
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""

    def test_idade_anulada_e_marcada_no_registro(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N="200"), 2026, report)

        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "idade_anulada"
        assert report.adjusted_rows == 1
        assert report.adjustments["idade_anulada"] == 1

    def test_uf_anulada_identifica_a_coluna_afetada(self):
        frame = clean_chunk(_raw_chunk(SG_UF_NOT="ZZ"), 2026, QualityReport())
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "uf_anulada:SG_UF_NOT"

    def test_data_ilegivel_identifica_a_coluna_afetada(self):
        frame = clean_chunk(_raw_chunk(DT_EVOLUCA="nao-e-data"), 2026, QualityReport())
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "data_ilegivel:DT_EVOLUCA"

    def test_ajustes_multiplos_sao_acumulados_no_mesmo_registro(self):
        report = QualityReport()
        frame = clean_chunk(
            _raw_chunk(NU_IDADE_N="200", SG_UF_NOT="ZZ", DT_EVOLUCA="lixo"),
            2026,
            report,
        )

        codigos = set(frame[ADJUSTMENT_COLUMN].iloc[0].split(","))
        assert codigos == {"idade_anulada", "uf_anulada:SG_UF_NOT", "data_ilegivel:DT_EVOLUCA"}
        # Um registro com tres ajustes conta como UM registro alterado.
        assert report.adjusted_rows == 1

    def test_valor_anulado_e_distinguivel_de_valor_ausente_na_origem(self):
        """A razao de existir do rastreamento por registro."""
        anulado = clean_chunk(_raw_chunk(SG_UF_NOT="ZZ"), 2026, QualityReport())
        vazio = clean_chunk(_raw_chunk(SG_UF_NOT=""), 2026, QualityReport())

        # Ambos terminam com SG_UF_NOT nulo...
        assert pd.isna(anulado["SG_UF_NOT"].iloc[0])
        assert pd.isna(vazio["SG_UF_NOT"].iloc[0])
        # ...mas apenas um foi alterado pelo pipeline, e isso e recuperavel.
        assert anulado[ADJUSTMENT_COLUMN].iloc[0] == "uf_anulada:SG_UF_NOT"
        assert vazio[ADJUSTMENT_COLUMN].iloc[0] == ""

    def test_relatorio_declara_como_localizar_os_registros(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(NU_IDADE_N="200"), 2026, report)
        payload = report.to_dict(source_files=["INFLUD26.csv"], run_id="run-1")

        assert payload["run_id"] == "run-1"
        assert payload["rows_adjusted"] == 1
        assert "ajustes_aplicados" in payload["adjustments"]["como_localizar"]
        assert set(payload["adjustments"]["significado"]) == set(ADJUSTMENT_CODES)


class TestSelecaoDeColunas:
    """A minimizacao vale para o que nao se usa, nao so para o que identifica."""

    def test_toda_coluna_lida_e_consumida_por_alguma_regra(self):
        """Ler uma coluna que nenhuma metrica usa contraria a minimizacao.

        Se este teste falhar ao adicionar uma coluna, ha duas saidas honestas:
        usa-la de fato, ou declara-la em NOT_SELECTED_COLUMNS com o motivo.
        """
        import re
        from pathlib import Path

        fontes = [p for p in Path("src").rglob("*.py") if p.name != "schema.py"]
        codigo = "\n".join(p.read_text(encoding="utf-8") for p in fontes)

        ociosas = [column for column in ALLOWED_COLUMNS if not re.search(rf"\b{column}\b", codigo)]
        assert not ociosas, f"colunas lidas mas nunca usadas: {ociosas}"

    def test_colunas_nao_selecionadas_sao_documentadas(self):
        from src.data.schema import NOT_SELECTED_COLUMNS

        assert NOT_SELECTED_COLUMNS
        assert all(motivo.strip() for motivo in NOT_SELECTED_COLUMNS.values())

    def test_as_tres_listas_sao_mutuamente_exclusivas(self):
        from src.data.schema import NOT_SELECTED_COLUMNS

        permitidas = set(ALLOWED_COLUMNS)
        assert not permitidas & set(DENIED_COLUMNS)
        assert not permitidas & set(NOT_SELECTED_COLUMNS)
        assert not set(DENIED_COLUMNS) & set(NOT_SELECTED_COLUMNS)

    def test_todo_dominio_declarado_corresponde_a_coluna_lida(self):
        """CODE_LABELS documenta o dicionario do que se le, nao codigo morto."""
        from src.data.schema import CODE_LABELS

        assert set(CODE_LABELS) <= set(ALLOWED_COLUMNS)


class TestCodigosForaDoDominio:
    """M5: codigo desconhecido e contado, nunca anulado nem tratado como caso aberto."""

    def test_codigo_fora_do_dominio_e_preservado_e_contado(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(EVOLUCAO="7"), 2026, report)

        assert frame["EVOLUCAO"].iloc[0] == 7  # preservado
        assert report.out_of_domain_counts["EVOLUCAO"] == 1
        assert bool(frame["caso_encerrado"].iloc[0]) is False  # nao e desfecho valido

    def test_codigo_valido_nao_e_contado_como_fora_do_dominio(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(EVOLUCAO="2", UTI="9"), 2026, report)
        assert report.out_of_domain_counts["EVOLUCAO"] == 0
        assert report.out_of_domain_counts["UTI"] == 0

    def test_relatorio_publica_dominio_e_duplicatas(self):
        report = QualityReport()
        report.identical_rows = 7
        payload = report.to_dict(source_files=["x.csv"])
        assert payload["rules"]["linhas_identicas_nas_colunas_persistidas"] == 7
        assert "codigo_fora_do_dominio_por_coluna" in payload["rules"]
        assert any("NAO" in note and "deduplicadas" in note for note in payload["notes"])


class TestCargaPontaAPonta:
    """A carga inteira (manifesto -> CSV -> pipeline -> Parquet -> relatorio)."""

    def test_preprocess_processa_csv_local_e_grava_artefatos(self, tmp_path, monkeypatch):
        import json

        from src.config import Settings
        from src.data.preprocess import preprocess

        # Raiz de dados isolada: nao toca a base sintetica da sessao.
        settings = Settings(data_root=tmp_path, reporting_lag_days=21)
        settings.ensure_directories()
        monkeypatch.setattr("src.data.preprocess.get_settings", lambda: settings)

        def row(**override: str) -> str:
            values = {c: "" for c in ALLOWED_COLUMNS}
            values.update(
                {
                    "DT_SIN_PRI": "2026-05-08",
                    "DT_DIGITA": "2026-05-20",
                    "SG_UF_NOT": "SP",
                    "NU_IDADE_N": "45",
                    "TP_IDADE": "3",
                    "EVOLUCAO": "1",
                    "HOSPITAL": "1",
                    "UTI": "2",
                    "CLASSI_FIN": "5",
                    "VACINA_COV": "1",
                    "VACINA": "2",
                    "CS_SEXO": "F",
                }
            )
            values.update(override)
            return ";".join(f'"{values[c]}"' for c in ALLOWED_COLUMNS)

        header = ";".join(f'"{c}"' for c in ALLOWED_COLUMNS)
        linhas = [row(), row(), row(EVOLUCAO="7")]  # 2 identicas + 1 codigo fora do dominio
        csv_path = tmp_path / "INFLUD26-teste.csv"
        csv_path.write_text("\n".join([header, *linhas]) + "\n", encoding="latin-1")

        settings.raw_manifest_path.write_text(
            json.dumps(
                {
                    "2026": {
                        "year": 2026,
                        "filename": csv_path.name,
                        "path": str(csv_path),
                        "url": None,
                        "origin": "arquivo local de teste",
                        "size_bytes": 1,
                        "sha256": "0" * 64,
                        "downloaded_at": "2026-01-01T00:00:00+00:00",
                    }
                }
            ),
            encoding="utf-8",
        )

        parquet = preprocess([2026])

        assert parquet.exists()
        report = json.loads(settings.quality_report_path.read_text(encoding="utf-8"))
        assert report["rows_read"] == 3 and report["rows_dropped"] == 0
        assert report["rules"]["linhas_identicas_nas_colunas_persistidas"] >= 1
        # A duplicidade e medida sobre o que de fato foi persistido, e a
        # lista das colunas medidas acompanha a contagem.
        assert "NU_IDADE_N" not in report["rules"]["colunas_da_medicao_de_duplicidade"]
        assert report["provenance"][0]["origin"] == "arquivo local de teste"
        assert report["pipeline"]  # o pipeline aplicado e publicado
        assert settings.ingestion_history_path.exists()

        import pandas as pd

        frame = pd.read_parquet(parquet)
        assert not set(frame.columns) & set(DENIED_COLUMNS)
        assert not {"idade_anos", "NU_IDADE_N", "TP_IDADE"} & set(frame.columns)
        assert "ajustes_aplicados" in frame.columns


class TestSemanaEpidemiologica:
    """A semana do Ministerio da Saude comeca no domingo -- nao e a semana ISO."""

    @pytest.mark.parametrize(
        ("data", "ano_esperado", "semana_esperada"),
        [
            # SE 1 e a primeira semana iniciada em domingo com >= 4 dias no ano
            # novo. 2024-12-29 e domingo e ja pertence a 2025 -- a semana ISO
            # diria 2024-W52.
            ("2024-12-28", 2024, 52),
            ("2024-12-29", 2025, 1),
            ("2025-01-01", 2025, 1),
            # 2025 e um ano de 53 semanas: a SE 53 comeca em 28/12/2025 e
            # atravessa a virada.
            ("2025-12-27", 2025, 52),
            ("2025-12-31", 2025, 53),
            ("2026-01-01", 2025, 53),
            ("2026-01-03", 2025, 53),
            ("2026-01-04", 2026, 1),
            # 2020 tambem tem 53 semanas, e a SE 1 comeca ainda em 2019.
            ("2019-12-29", 2020, 1),
            ("2020-12-31", 2020, 53),
        ],
    )
    def test_semana_epidemiologica_virada_de_ano(self, data, ano_esperado, semana_esperada):
        from src.data.cleaning.epiweek import epidemiological_week

        semanas = epidemiological_week(pd.Series(pd.to_datetime([data])))

        assert int(semanas["semana_epi_ano"].iloc[0]) == ano_esperado
        assert int(semanas["semana_epi_num"].iloc[0]) == semana_esperada

    def test_rotulo_e_ordenavel_e_acompanha_o_ano_epidemiologico(self):
        frame = clean_chunk(
            _raw_chunk(DT_SIN_PRI="2026-01-01", DT_DIGITA="2026-02-01"), 2026, QualityReport()
        )

        assert frame["semana_epi"].iloc[0] == "2025-53"
        # O ano civil dos sintomas e outro campo: 1 de janeiro de 2026 pertence
        # a semana epidemiologica 53 de 2025.
        assert int(frame["ano_sintomas"].iloc[0]) == 2026
        assert frame["mes_sintomas"].iloc[0] == "2026-01"

    def test_semana_epi_divergente_de_sem_pri_conta_sem_corrigir(self):
        """Reconciliacao, nao correcao: nenhuma das duas colunas preenche a outra."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(DT_SIN_PRI="2024-12-29", SEM_PRI="52"), 2024, report)

        # A derivada manda: 29/12/2024 e a SE 1 de 2025.
        assert int(frame["semana_epi_num"].iloc[0]) == 1
        assert int(frame["semana_epi_ano"].iloc[0]) == 2025
        # SEM_PRI e preservada exatamente como veio -- nunca sobrescrita nem
        # usada em coalesce para preencher a derivada.
        assert frame["SEM_PRI"].iloc[0] == "52"
        # E a divergencia vira contagem, nao ajuste.
        assert report.epiweek_compared == 1
        assert report.epiweek_mismatch == 1
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""

    def test_semana_epi_coincidente_nao_conta_divergencia(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(DT_SIN_PRI="2024-12-29", SEM_PRI="01"), 2024, report)

        assert report.epiweek_compared == 1
        assert report.epiweek_mismatch == 0

    def test_sem_pri_ausente_nao_impede_a_derivacao(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(DT_SIN_PRI="2026-05-08", SEM_PRI=""), 2026, report)

        assert frame["semana_epi"].iloc[0] == "2026-18"
        assert report.epiweek_compared == 0


class TestCodigoIlegivel:
    """A promessa "nada e alterado silenciosamente" vale tambem para categoricos."""

    def test_codigo_ilegivel_registrado_como_ajuste(self):
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(UTI="X"), 2026, report)

        assert pd.isna(frame["UTI"].iloc[0])
        assert report.unreadable_codes["UTI"] == 1
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "codigo_ilegivel:UTI"

    def test_celula_vazia_nao_e_codigo_ilegivel(self):
        """A razao de existir do ajuste: distinguir lixo de campo nunca preenchido."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(UTI=""), 2026, report)

        assert pd.isna(frame["UTI"].iloc[0])
        assert report.unreadable_codes["UTI"] == 0
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""


class TestCompletudePorColuna:
    """vazio / codigo_ignorado / fora_do_dominio sao tres coisas diferentes."""

    def test_identidade_da_completude_confere(self):
        report = QualityReport()
        for valor in ("1", "2", "9", "7", ""):
            clean_chunk(_raw_chunk(UTI=valor), 2026, report)

        completude = report.code_completeness()["UTI"]
        assert completude["total"] == 5
        assert completude["validos"] == 2
        assert completude["ignorados"] == 1
        assert completude["ausentes"] == 1
        assert completude["fora_do_dominio"] == 1
        assert completude["identidade_confere"] is True

    def test_classi_fin_nao_tem_codigo_9_e_nao_o_conta_como_ignorado(self):
        """O dicionario declara 1..5 para CLASSI_FIN: um 9 ali e codigo inexistente."""
        from src.data.schema import missing_codes_for

        assert missing_codes_for("CLASSI_FIN") == frozenset()
        assert missing_codes_for("CRITERIO") == frozenset()
        assert missing_codes_for("UTI") == MISSING_CODES

        report = QualityReport()
        clean_chunk(_raw_chunk(CLASSI_FIN="9"), 2026, report)

        assert report.ignored_code_counts["CLASSI_FIN"] == 0
        assert report.out_of_domain_counts["CLASSI_FIN"] == 1

    def test_relatorio_publica_a_completude_e_a_reconciliacao(self):
        report = QualityReport()
        clean_chunk(_raw_chunk(UTI="9"), 2026, report)
        rules = report.to_dict(source_files=["INFLUD26.csv"])["rules"]

        assert rules["completude_por_coluna_categorica"]["UTI"]["ignorados"] == 1
        assert "semana_epidemiologica" in rules
        assert "codigo_ilegivel_por_coluna" in rules


class TestVentilacaoNoContratoDeColunas:
    def test_suport_ven_nao_usa_o_dominio_sim_nao_ignorado(self):
        """Regressao explicita: 1/2/9 aqui inverteria o indicador de ventilacao."""
        from src.data.schema import CODE_LABELS, YES_NO_IGNORED

        assert CODE_LABELS["SUPORT_VEN"] is not YES_NO_IGNORED
        assert CODE_LABELS["SUPORT_VEN"][2] == "Sim, nao invasivo"
        assert set(CODE_LABELS["SUPORT_VEN"]) == {1, 2, 3, 9}
