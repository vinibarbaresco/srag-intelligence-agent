"""Testes das regras de transformacao do CSV bruto.

Cobrem o contrato de colunas (minimizacao de dados pessoais), o parse de datas
nos dois formatos ja publicados pelo DATASUS, a normalizacao de idade e a
marcacao -- e nao exclusao -- de registros inconsistentes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.preprocess import QualityReport, _age_in_years, _parse_dates, transform_chunk
from src.data.schema import (
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
        frame = transform_chunk(_raw_chunk(), 2026, QualityReport())
        assert not set(frame.columns) & set(DENIED_COLUMNS)

    def test_idade_exata_e_acompanhada_de_faixa_agregada(self):
        frame = transform_chunk(_raw_chunk(), 2026, QualityReport())
        assert frame["faixa_etaria"].iloc[0] == "40-49"


class TestParseDeDatas:
    """A fonte ja publicou o mesmo campo em tres formatos distintos."""

    def test_formato_iso_com_sufixo_z(self):
        parsed = _parse_dates(pd.Series(["2026-04-30T00:00:00.000Z"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2026-04-30")

    def test_formato_iso_simples(self):
        # Formato do INFLUD25 versao 26-06-2025, distribuido com o enunciado.
        parsed = _parse_dates(pd.Series(["2024-12-29"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2024-12-29")

    def test_formato_brasileiro_como_fallback(self):
        parsed = _parse_dates(pd.Series(["30/04/2026"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2026-04-30")

    def test_data_invalida_vira_nulo_e_nao_excecao(self):
        parsed = _parse_dates(pd.Series(["31/02/2026", "texto", ""], dtype="string"))
        assert parsed.isna().all()

    def test_data_invalida_e_contabilizada(self):
        report = QualityReport()
        transform_chunk(_raw_chunk(DT_EVOLUCA="data-quebrada"), 2026, report)
        assert report.invalid_dates["DT_EVOLUCA"] == 1


class TestNormalizacaoDeIdade:
    @pytest.mark.parametrize(
        ("valor", "unidade", "esperado"),
        [("45", 3, 45.0), ("18", 2, 1.5), ("365", 1, 0.999), ("0", 3, 0.0)],
    )
    def test_converte_para_anos(self, valor, unidade, esperado):
        years = _age_in_years(pd.Series([valor]), pd.Series([unidade]))
        assert years.iloc[0] == pytest.approx(esperado, abs=0.01)

    def test_idade_implausivel_e_anulada_e_contabilizada(self):
        report = QualityReport()
        frame = transform_chunk(_raw_chunk(NU_IDADE_N="200"), 2026, report)
        assert pd.isna(frame["idade_anos"].iloc[0])
        assert report.age_out_of_range == 1


class TestTratamentoDeAusencia:
    def test_codigo_9_e_preservado_e_contabilizado(self):
        report = QualityReport()
        frame = transform_chunk(_raw_chunk(UTI="9"), 2026, report)
        assert frame["UTI"].iloc[0] == 9
        assert report.ignored_code_counts["UTI"] == 1

    def test_codigo_9_nao_vira_nao_nem_zero(self):
        frame = transform_chunk(_raw_chunk(EVOLUCAO="9"), 2026, QualityReport())
        assert frame["EVOLUCAO"].iloc[0] == 9
        assert 9 in MISSING_CODES

    def test_uf_fora_do_dominio_e_anulada_e_contabilizada(self):
        report = QualityReport()
        frame = transform_chunk(_raw_chunk(SG_UF_NOT="ZZ"), 2026, report)
        assert pd.isna(frame["SG_UF_NOT"].iloc[0])
        assert report.unknown_uf == 1


class TestRegistrosInconsistentes:
    def test_sintomas_depois_da_digitacao_e_marcado_nao_removido(self):
        report = QualityReport()
        frame = transform_chunk(
            _raw_chunk(
                DT_SIN_PRI="2026-05-25T00:00:00.000Z",
                DT_DIGITA="2026-05-20T00:00:00.000Z",
            ),
            2026,
            report,
        )
        assert len(frame) == 1  # o registro permanece na base
        assert bool(frame["flag_data_invalida"].iloc[0]) is True
        assert report.inconsistent_timeline == 1

    def test_saida_de_uti_antes_da_entrada_e_marcada(self):
        frame = transform_chunk(
            _raw_chunk(
                DT_ENTUTI="2026-05-15T00:00:00.000Z",
                DT_SAIDUTI="2026-05-12T00:00:00.000Z",
            ),
            2026,
            QualityReport(),
        )
        assert bool(frame["flag_data_invalida"].iloc[0]) is True

    def test_registro_valido_nao_e_marcado(self):
        frame = transform_chunk(_raw_chunk(), 2026, QualityReport())
        assert bool(frame["flag_data_invalida"].iloc[0]) is False

    def test_nenhum_registro_e_descartado(self):
        report = QualityReport()
        transform_chunk(_raw_chunk(DT_SIN_PRI="lixo"), 2026, report)
        assert report.rows_read == report.rows_written


class TestRelatorioDeQualidade:
    def test_documenta_regras_e_contagens(self):
        report = QualityReport()
        transform_chunk(_raw_chunk(UTI="9"), 2026, report)
        payload = report.to_dict(source_files=["INFLUD26.csv"])

        assert payload["rows_read"] == 1
        assert payload["rows_dropped"] == 0
        assert payload["rules"]["codigo_9_ignorado_por_coluna"]["UTI"] == 1
        assert any("Nenhum registro e excluido" in note for note in payload["notes"])
