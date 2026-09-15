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
    return CleaningContext(year=2026, report=QualityReport(), adjustments=AdjustmentLog(index))


def _frame(**values) -> pd.DataFrame:
    """Bloco minimo com as colunas que as regras leem."""
    base = {
        "DT_SIN_PRI": ["2026-05-08"],
        "DT_INTERNA": ["2026-05-09"],
        "DT_ENTUTI": [""],
        "DT_SAIDUTI": [""],
        "DT_EVOLUCA": ["2026-05-20"],
        "DT_ENCERRA": ["2026-05-22"],
        "DT_DIGITA": ["2026-05-25"],
        "SEM_PRI": ["18"],  # semana epi de 08/05/2026
        "HOSPITAL": ["1"],
        "UTI": ["2"],
        "SUPORT_VEN": ["3"],
        "NOSOCOMIAL": ["2"],
        "CLASSI_FIN": ["5"],
        "CRITERIO": ["1"],
        "EVOLUCAO": ["1"],
        "VACINA": ["2"],
        "VACINA_COV": ["1"],
        "SG_UF_NOT": ["SP"],
        "SG_UF": ["SP"],
        "CS_SEXO": ["F"],
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
        frame = DeriveAge().apply(_frame(NU_IDADE_N="11", TP_IDADE="2"), context)
        assert frame["faixa_etaria"].iloc[0] == "0-4"
        # A idade exata e os campos brutos nao seguem para a camada analitica.
        assert not {"idade_anos", "NU_IDADE_N", "TP_IDADE"} & set(frame.columns)

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

        incoerente = self._derive(context, UTI="1", DT_ENTUTI="2026-05-15", DT_SAIDUTI="2026-05-12")
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


class TestSuporteVentilatorio:
    """SUPORT_VEN nao e Sim/Nao/Ignorado -- e o teste que impede a inversao."""

    def _derive(self, context, **values) -> pd.DataFrame:
        frame = _frame(**values)
        for rule in CLEANING_PIPELINE:
            frame = rule.apply(frame, context)
        return frame

    def test_suport_ven_2_e_ventilacao_sim(self, context):
        """Codigo 2 e "Sim, nao invasivo" -- ler como "Nao" inverteria 75.120 registros."""
        frame = self._derive(context, SUPORT_VEN="2")

        assert bool(frame["foi_ventilado"].iloc[0]) is True
        assert bool(frame["ventilacao_nao_invasiva"].iloc[0]) is True
        assert bool(frame["ventilacao_invasiva"].iloc[0]) is False

    def test_suport_ven_1_e_invasiva(self, context):
        frame = self._derive(context, SUPORT_VEN="1")
        assert bool(frame["ventilacao_invasiva"].iloc[0]) is True
        assert bool(frame["foi_ventilado"].iloc[0]) is True

    def test_ventilacao_informada_inclui_codigo_3(self, context):
        """Nao ter ventilado tambem e informacao: entra no denominador, nao no numerador."""
        frame = self._derive(context, SUPORT_VEN="3")

        assert bool(frame["ventilacao_informada"].iloc[0]) is True
        assert bool(frame["foi_ventilado"].iloc[0]) is False

    @pytest.mark.parametrize("valor", ["", "9"])
    def test_missing_ventilacao_nao_vira_false(self, context, valor):
        """Ausente nao e "nao ventilado": fica fora do numerador E do denominador."""
        frame = self._derive(context, SUPORT_VEN=valor)

        assert bool(frame["foi_ventilado"].iloc[0]) is False
        assert bool(frame["ventilacao_informada"].iloc[0]) is False


class TestRotulosCategoricos:
    """Vazio e um conceito proprio, nunca o rotulo de um codigo do dominio."""

    def _derive(self, context, **values) -> pd.DataFrame:
        frame = _frame(**values)
        for rule in CLEANING_PIPELINE:
            frame = rule.apply(frame, context)
        return frame

    def test_classi_fin_vazio_nao_vira_nao_especificado(self, context):
        """Caso nao encerrado e caso sem agente identificado sao coisas distintas."""
        vazio = self._derive(context, CLASSI_FIN="")
        assert vazio["grupo_etiologico"].iloc[0] == "nao_encerrado"

        codigo_4 = self._derive(context, CLASSI_FIN="4")
        assert codigo_4["grupo_etiologico"].iloc[0] == "nao_especificado"

    def test_classi_fin_fora_do_dominio_nao_se_disfarca_de_codigo_valido(self, context):
        frame = self._derive(context, CLASSI_FIN="7")
        assert frame["grupo_etiologico"].iloc[0] == "fora_do_dominio"

    def test_status_caso_em_aberto_nao_e_cura(self, context):
        """Caso sem desfecho registrado nao e caso que sobreviveu."""
        frame = self._derive(context, EVOLUCAO="", DT_EVOLUCA="")
        assert frame["status_caso"].iloc[0] == "em_aberto"

    @pytest.mark.parametrize(
        ("evolucao", "esperado"),
        [("1", "cura"), ("2", "obito_srag"), ("3", "obito_outras"), ("9", "desfecho_ignorado")],
    )
    def test_status_caso_segue_o_dicionario(self, context, evolucao, esperado):
        frame = self._derive(context, EVOLUCAO=evolucao)
        assert frame["status_caso"].iloc[0] == esperado

    def test_criterio_de_encerramento_e_traduzido(self, context):
        frame = self._derive(context, CRITERIO="1")
        assert bool(frame["etiologia_laboratorial"].iloc[0]) is True
        assert bool(frame["etiologia_criterio_informado"].iloc[0]) is True

        clinico = self._derive(context, CRITERIO="3")
        assert bool(clinico["etiologia_laboratorial"].iloc[0]) is False
        assert bool(clinico["etiologia_criterio_informado"].iloc[0]) is True


class TestCoerenciaRevisada:
    """Regras corrigidas contra o dicionario e contra a medicao na fonte."""

    def _derive(self, context, **values) -> pd.DataFrame:
        frame = _frame(**values)
        for rule in CLEANING_PIPELINE:
            frame = rule.apply(frame, context)
        return frame

    def test_nosocomial_nao_flaga_internacao(self, context):
        """Infeccao hospitalar: os sintomas comecam DEPOIS da internacao, por definicao."""
        frame = self._derive(
            context, NOSOCOMIAL="1", DT_INTERNA="2026-05-01", DT_SIN_PRI="2026-05-08"
        )

        assert bool(frame["caso_nosocomial"].iloc[0]) is True
        assert bool(frame["flag_internacao_inconsistente"].iloc[0]) is False

    def test_nosocomial_2_continua_flagado(self, context):
        """Sem infeccao hospitalar declarada, internar antes de adoecer segue impossivel."""
        frame = self._derive(
            context, NOSOCOMIAL="2", DT_INTERNA="2026-05-01", DT_SIN_PRI="2026-05-08"
        )

        assert bool(frame["caso_nosocomial"].iloc[0]) is False
        assert bool(frame["flag_internacao_inconsistente"].iloc[0]) is True

    def test_nosocomial_nao_dispensa_data_de_internacao(self, context):
        """A excecao vale para a ordem das datas, nao para a data ausente."""
        frame = self._derive(context, NOSOCOMIAL="1", HOSPITAL="1", DT_INTERNA="")
        assert bool(frame["flag_internacao_inconsistente"].iloc[0]) is True

    def test_saida_uti_sem_entrada_flagada(self, context):
        """Data de saida sem entrada torna a estadia incalculavel, com UTI preenchido ou nao."""
        frame = self._derive(context, UTI="", DT_ENTUTI="", DT_SAIDUTI="2026-05-15")
        assert bool(frame["flag_uti_inconsistente"].iloc[0]) is True

    def test_data_implausivel_ano_2202(self, context):
        """Erro de digitacao de ano preserva a ordem entre as datas e escapa das demais regras.

        2202-06-07 e o maior valor de `DT_INTERNA` medido na base de referencia.
        Esta dentro do intervalo representavel por `Timestamp` em qualquer versao
        do pandas suportada, entao o caminho exercitado aqui e sempre o da flag.
        """
        frame = self._derive(context, DT_INTERNA="2202-06-07")

        assert bool(frame["flag_data_implausivel"].iloc[0]) is True
        # Marca e conta, mas nao exclui: so flag_data_invalida tira da view.
        assert bool(frame["flag_data_invalida"].iloc[0]) is False
        assert context.report.coherence_flags["flag_data_implausivel"] == 1

    def test_ano_fora_do_intervalo_representavel_nunca_vira_data_utilizavel(self, context):
        """Ano absurdo alem do limite de `Timestamp` e barrado por um dos dois caminhos.

        O limite depende da versao do pandas: em 2.x a resolucao e sempre
        nanossegundos e `5202-05-09` estoura `Timestamp.max` (2262-04-11),
        virando `NaT` no parse; em 3.x a resolucao se adapta e a data e
        representada, chegando a regra de plausibilidade.

        O teste afirma o que importa em qualquer uma das duas: o valor **nunca**
        vira uma data utilizavel em silencio. Ou e marcado como implausivel, ou e
        anulado no parse e registrado como ajuste `data_ilegivel` -- que existe
        exatamente para distinguir "presente e ilegivel" de "vazio na origem".
        """
        frame = self._derive(context, DT_INTERNA="5202-05-09")

        marcado_como_implausivel = bool(frame["flag_data_implausivel"].iloc[0])
        anulado_no_parse = bool(frame["DT_INTERNA"].isna().iloc[0])
        assert marcado_como_implausivel or anulado_no_parse

        if anulado_no_parse:
            assert context.report.invalid_dates["DT_INTERNA"] == 1
            # O ajuste e lido do contexto, e nao da coluna `ajustes_aplicados`:
            # ela e escrita por `clean_chunk` ao fim do bloco, e este teste
            # aplica as regras diretamente.
            assert context.adjustments.counts()["data_ilegivel:DT_INTERNA"] == 1
        else:
            assert context.report.coherence_flags["flag_data_implausivel"] == 1

    def test_data_anterior_ao_inicio_da_serie_e_implausivel(self, context):
        frame = self._derive(context, DT_INTERNA="1695-01-17")
        assert bool(frame["flag_data_implausivel"].iloc[0]) is True

    def test_data_evolucao_posterior_a_digitacao_nao_e_flagada(self, context):
        """DT_DIGITA e a digitacao inicial, nao a ultima atualizacao do registro.

        Medido: `DT_EVOLUCA > DT_DIGITA` em 52,88% dos pares. Tratar isso como
        erro marcaria 36,66% da base como invalida sem haver erro nenhum.
        """
        frame = self._derive(context, DT_DIGITA="2026-05-10", DT_EVOLUCA="2026-06-20")

        assert bool(frame["flag_evolucao_inconsistente"].iloc[0]) is False
        assert bool(frame["flag_data_implausivel"].iloc[0]) is False
        assert bool(frame["flag_data_invalida"].iloc[0]) is False

    def test_tp_idade_2_com_63_meses_anulado(self, context):
        """63 meses nao existem na escala de meses; 5,25 anos seria indetectavel depois."""
        frame = self._derive(context, TP_IDADE="2", NU_IDADE_N="63")

        assert pd.isna(frame["faixa_etaria"].iloc[0])
        assert context.report.age_unit_out_of_domain == 1
        # A unidade NAO e reinterpretada: a idade e anulada e o ajuste, registrado.
        assert context.adjustments.counts()["idade_unidade_implausivel"] == 1

    def test_tp_idade_2_dentro_do_dominio_e_preservada(self, context):
        frame = self._derive(context, TP_IDADE="2", NU_IDADE_N="6")
        assert frame["faixa_etaria"].iloc[0] == "0-4"
        assert context.report.age_unit_out_of_domain == 0


class TestPlausibilidadeDeDatasEReprodutivel:
    """M-2: o teto era so a data de execucao, entao a deteccao expirava.

    Uma `DT_ENTUTI` de 2028 era implausivel numa carga rodada em 2026 e deixava
    de ser numa carga rodada em 2029 -- o conteudo do Parquet dependia do dia da
    execucao. O teto relativo (`DT_DIGITA` mais um ano) nao depende.
    """

    def _registro(self, **datas):
        import pandas as pd

        from src.data.schema import DATE_COLUMNS

        base = {coluna: pd.Series([pd.NaT]) for coluna in DATE_COLUMNS}
        base.update({coluna: pd.Series([pd.Timestamp(valor)]) for coluna, valor in datas.items()})
        return pd.DataFrame(base)

    @pytest.mark.parametrize("dia_da_execucao", ["2026-09-14", "2029-01-01", "2035-01-01"])
    def test_data_muito_posterior_a_digitacao_e_implausivel_em_qualquer_execucao(
        self, dia_da_execucao
    ):
        import pandas as pd

        from src.data.cleaning.coherence import implausible_dates

        frame = self._registro(
            DT_DIGITA="2025-06-26", DT_SIN_PRI="2025-06-01", DT_ENTUTI="2028-05-07"
        )
        assert bool(implausible_dates(frame, ceiling=pd.Timestamp(dia_da_execucao)).iloc[0]), (
            "a deteccao passou a depender do dia da execucao"
        )

    def test_encerramento_tardio_dentro_do_ano_nao_e_implausivel(self):
        import pandas as pd

        from src.data.cleaning.coherence import implausible_dates

        # DT_ENCERRA meses depois da digitacao inicial e rotina: `DT_DIGITA` e a
        # primeira digitacao, nao a ultima atualizacao do registro.
        frame = self._registro(
            DT_DIGITA="2025-01-10", DT_SIN_PRI="2025-01-05", DT_ENCERRA="2025-06-20"
        )
        assert not bool(implausible_dates(frame, ceiling=pd.Timestamp("2026-09-14")).iloc[0])
