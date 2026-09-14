"""Testes do pipeline de tratamento como estrutura declarada.

O ganho da organizacao em regras nomeadas e poder exercitar cada uma em
isolamento, sem executar a carga inteira -- e poder afirmar propriedades sobre o
pipeline em si: que a ordem esta declarada, que toda regra e documentada, e que
a documentacao publicada vem da mesma estrutura que executa.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaning import CLEANING_PIPELINE, CleaningRule, describe_pipeline
from src.data.cleaning.base import CleaningContext
from src.data.cleaning.codes import NormalizeStateCodes
from src.data.cleaning.coherence import EvaluateCoherence
from src.data.cleaning.dates import ParseDateColumns
from src.data.cleaning.demographics import DeriveAge
from src.data.cleaning.pipeline import clean_chunk
from src.data.quality import AdjustmentLog, QualityReport
from src.data.schema import COHERENCE_FLAGS, DERIVED_SEMANTIC_COLUMNS


@pytest.fixture
def context() -> CleaningContext:
    """Contexto de tratamento sobre um indice de uma linha."""
    index = pd.RangeIndex(1)
    return CleaningContext(
        year=2026, report=QualityReport(), adjustments=AdjustmentLog(index)
    )


def _frame(**values) -> pd.DataFrame:
    """Bloco minimo com as colunas que as regras leem."""
    base = {
        "DT_NOTIFIC": ["2026-05-10"],
        "DT_SIN_PRI": ["2026-05-08"],
        "DT_INTERNA": ["2026-05-09"],
        "DT_ENTUTI": [""],
        "DT_SAIDUTI": [""],
        "DT_EVOLUCA": ["2026-05-20"],
        "DT_ENCERRA": [""],
        "DT_DIGITA": ["2026-05-25"],
        "DOSE_1_COV": [""],
        "DOSE_2_COV": [""],
        "DOSE_REF": [""],
        "HOSPITAL": ["1"],
        "UTI": ["2"],
        "EVOLUCAO": ["1"],
        "VACINA": ["2"],
        "VACINA_COV": ["1"],
        "SG_UF_NOT": ["SP"],
        "SG_UF": ["SP"],
        "NU_IDADE_N": ["45"],
        "TP_IDADE": ["3"],
    }
    base.update({key: [value] for key, value in values.items()})
    return pd.DataFrame(base, dtype="string")


class TestPipelineDeclarado:
    def test_toda_regra_tem_nome_e_descricao(self):
        for rule in CLEANING_PIPELINE:
            assert isinstance(rule, CleaningRule)
            assert rule.name and rule.description
            assert len(rule.description) > 40, f"{rule.name} mal documentada"

    def test_nomes_de_regra_sao_unicos(self):
        nomes = [rule.name for rule in CLEANING_PIPELINE]
        assert len(nomes) == len(set(nomes))

    def test_coerencia_roda_antes_da_semantica(self):
        """`estadia_uti_utilizavel` depende da flag de coerencia de UTI."""
        ordem = [type(rule).__name__ for rule in CLEANING_PIPELINE]
        assert ordem.index("EvaluateCoherence") < ordem.index("DeriveSemanticFlags")

    def test_datas_sao_normalizadas_antes_da_coerencia(self):
        """As regras de coerencia comparam datas; elas precisam existir antes."""
        ordem = [type(rule).__name__ for rule in CLEANING_PIPELINE]
        assert ordem.index("ParseDateColumns") < ordem.index("EvaluateCoherence")

    def test_descricao_do_pipeline_reflete_a_execucao(self):
        descrito = describe_pipeline()
        assert len(descrito) == len(CLEANING_PIPELINE)
        assert [item["name"] for item in descrito] == [r.name for r in CLEANING_PIPELINE]
        assert [item["ordem"] for item in descrito] == [
            str(n) for n in range(1, len(CLEANING_PIPELINE) + 1)
        ]


class TestRegrasIsoladas:
    """Cada regra e exercitavel sem rodar o pipeline inteiro."""

    def test_parse_de_datas_isolado(self, context):
        frame = ParseDateColumns().apply(_frame(), context)
        assert frame["DT_SIN_PRI"].iloc[0] == pd.Timestamp("2026-05-08")

    def test_validacao_de_uf_isolada(self, context):
        frame = NormalizeStateCodes().apply(_frame(SG_UF_NOT="ZZ"), context)
        assert pd.isna(frame["SG_UF_NOT"].iloc[0])
        assert context.report.unknown_uf == 1

    def test_derivacao_de_idade_isolada(self, context):
        frame = DeriveAge().apply(_frame(NU_IDADE_N="30", TP_IDADE="2"), context)
        assert frame["idade_anos"].iloc[0] == pytest.approx(2.5)
        assert frame["faixa_etaria"].iloc[0] == "0-4"

    def test_coerencia_isolada_precisa_das_datas(self, context):
        frame = ParseDateColumns().apply(_frame(), context)
        frame = EvaluateCoherence().apply(frame, context)
        assert set(COHERENCE_FLAGS) <= set(frame.columns)
        assert not frame["flag_data_invalida"].iloc[0]


class TestSemanticaDerivadaEmPython:
    """A traducao dos codigos vive ao lado do dicionario, nao no SQL."""

    def _derive(self, context, **values) -> pd.DataFrame:
        frame = _frame(**values)
        for rule in CLEANING_PIPELINE:
            frame = rule.apply(frame, context)
        return frame

    def test_todas_as_colunas_semanticas_sao_produzidas(self, context):
        frame = self._derive(context)
        assert set(DERIVED_SEMANTIC_COLUMNS) <= set(frame.columns)

    @pytest.mark.parametrize(
        ("evolucao", "obito", "encerrado"),
        [("1", False, True), ("2", True, True), ("3", False, True), ("9", False, False)],
    )
    def test_desfecho_segue_o_dicionario(self, context, evolucao, obito, encerrado):
        frame = self._derive(context, EVOLUCAO=evolucao)
        assert bool(frame["eh_obito_srag"].iloc[0]) is obito
        assert bool(frame["caso_encerrado"].iloc[0]) is encerrado

    @pytest.mark.parametrize(
        ("uti", "admitido", "informado"),
        [("1", True, True), ("2", False, True), ("9", False, False)],
    )
    def test_uti_segue_o_dicionario(self, context, uti, admitido, informado):
        frame = self._derive(context, UTI=uti, DT_ENTUTI="2026-05-09")
        assert bool(frame["teve_admissao_uti"].iloc[0]) is admitido
        assert bool(frame["uti_informado"].iloc[0]) is informado

    def test_codigo_ausente_nunca_vira_verdadeiro(self, context):
        frame = self._derive(context, EVOLUCAO="", UTI="", VACINA_COV="")
        for column in (
            "eh_obito_srag",
            "caso_encerrado",
            "teve_admissao_uti",
            "uti_informado",
            "vacinado_covid",
            "vacina_covid_informada",
        ):
            assert bool(frame[column].iloc[0]) is False, column

    def test_estadia_utilizavel_exige_coerencia_e_data_de_entrada(self, context):
        utilizavel = self._derive(context, UTI="1", DT_ENTUTI="2026-05-09")
        assert bool(utilizavel["estadia_uti_utilizavel"].iloc[0]) is True

        sem_data = self._derive(context, UTI="1", DT_ENTUTI="")
        assert bool(sem_data["estadia_uti_utilizavel"].iloc[0]) is False
        assert bool(sem_data["flag_uti_inconsistente"].iloc[0]) is True

        incoerente = self._derive(
            context, UTI="1", DT_ENTUTI="2026-05-15", DT_SAIDUTI="2026-05-12"
        )
        assert bool(incoerente["estadia_uti_utilizavel"].iloc[0]) is False


class TestPipelineCompleto:
    def test_subconjunto_de_regras_pode_ser_aplicado(self):
        """Permite testar uma etapa sem arrastar o pipeline inteiro."""
        report = QualityReport()
        frame = clean_chunk(_frame(), 2026, report, rules=(ParseDateColumns(),))

        assert frame["DT_SIN_PRI"].iloc[0] == pd.Timestamp("2026-05-08")
        assert "eh_obito_srag" not in frame.columns  # regra nao aplicada

    def test_pipeline_completo_produz_todas_as_colunas_derivadas(self):
        from src.data.schema import ALL_DERIVED_COLUMNS

        frame = clean_chunk(_frame(), 2026, QualityReport())
        assert set(ALL_DERIVED_COLUMNS) <= set(frame.columns)

    def test_relatorio_de_qualidade_publica_o_pipeline(self):
        report = QualityReport()
        clean_chunk(_frame(), 2026, report)
        payload = report.to_dict(
            source_files=["INFLUD26.csv"], run_id="run-1", pipeline=describe_pipeline()
        )
        assert len(payload["pipeline"]) == len(CLEANING_PIPELINE)
