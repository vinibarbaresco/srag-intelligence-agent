"""Testes dos indicadores epidemiologicos.

Os valores esperados sao derivados da composicao explicita da base sintetica
(ver `tests/conftest.py`), nao de uma execucao anterior: se a regra de calculo
mudar, o teste falha por divergencia com a definicao, e nao por regressao muda.
"""

from __future__ import annotations

import pytest

from src.metrics.definitions import DEFINITIONS_BY_KEY
from src.metrics.epidemiology import (
    case_growth_rate,
    icu_metrics,
    icu_patient_census,
    mortality_rate,
    notification_delay_profile,
    vaccination_metrics,
)
from src.metrics.filters import AnalyticFilters, InvalidFilterError, analysis_cutoff
from src.metrics.timeseries import daily_cases, monthly_cases
from tests.conftest import EXPECTED_CUTOFF

#: As contagens exatas da base sintetica valem para SP. Existe um unico registro
#: em RJ, presente apenas para comprovar que o filtro por UF de fato restringe o
#: recorte; ele e mantido fora das asserções numericas para que os valores
#: esperados continuem derivando da composicao declarada em `conftest.py`.
SP = AnalyticFilters(uf="SP")


class TestJanelaDeAnalise:
    def test_corte_ancorado_na_base_e_nao_na_data_de_hoje(self, connection):
        assert analysis_cutoff(connection) == EXPECTED_CUTOFF


class TestTaxaDeAumentoDeCasos:
    def test_calcula_variacao_entre_janelas(self, connection):
        result = case_growth_rate(connection, SP)
        # 150 casos na janela atual contra 100 na anterior.
        assert result.value == pytest.approx(50.0)
        assert result.components["casos_periodo_atual"] == 150
        assert result.components["casos_periodo_anterior"] == 100
        assert result.numerator == 50
        assert result.denominator == 100

    def test_denominador_zero_devolve_indisponivel_e_nao_zero(self, connection):
        # Nenhum caso de SRAG por influenza na base: ambas as janelas ficam vazias.
        result = case_growth_rate(connection, AnalyticFilters(classification=1))
        assert result.value is None
        assert result.denominator == 0
        assert "indefinida" in result.unavailable_reason

    def test_envelope_declara_fonte_e_limitacoes(self, connection):
        payload = case_growth_rate(connection).to_dict()
        assert payload["source"]
        assert payload["limitations"]
        assert payload["period"]["inicio"] < payload["period"]["fim"]


class TestTaxaDeMortalidade:
    def test_usa_apenas_casos_encerrados_no_denominador(self, connection):
        result = mortality_rate(connection, SP)
        # 15 obitos por SRAG em 60 casos encerrados (40 curas + 15 obitos + 5 outros).
        assert result.numerator == 15
        assert result.denominator == 60
        assert result.value == pytest.approx(25.0)

    def test_codigo_9_e_casos_em_aberto_ficam_fora_do_denominador(self, connection):
        result = mortality_rate(connection, SP)
        assert result.components["evolucao_ignorada"] == 10
        assert result.components["casos_em_aberto"] == 80
        assert result.denominator < result.components["casos_no_periodo"]

    def test_obito_por_outras_causas_conta_como_encerrado_mas_nao_como_obito_srag(self, connection):
        result = mortality_rate(connection, SP)
        assert result.components["obitos_por_outras_causas"] == 5
        assert result.components["obitos_por_srag"] == 15


class TestUTI:
    def test_taxa_de_admissao_ignora_codigo_9(self, connection):
        result = icu_metrics(connection, SP)
        # 40 admitidos em 100 hospitalizados com UTI informado (1 ou 2).
        assert result.numerator == 40
        assert result.denominator == 100
        assert result.value == pytest.approx(40.0)
        assert result.components["uti_ignorado"] == 10

    def test_ocupacao_de_leitos_e_declarada_nao_calculavel(self, connection):
        occupancy = icu_metrics(connection, SP).components["taxa_de_ocupacao_de_leitos_de_uti"]
        assert occupancy["value"] is None
        assert "capacidade instalada" in occupancy["unavailable_reason"]

    def test_censo_diario_cobre_toda_a_janela(self, connection):
        census = icu_patient_census(connection, SP)
        assert len(census) == 30
        assert all("pacientes_em_uti" in point for point in census)
        assert max(point["pacientes_em_uti"] for point in census) > 0

    def test_estadia_incoerente_sai_do_censo_mas_nao_da_taxa_de_admissao(self, connection):
        """Flags por dimensao: o defeito exclui do que depende dele, e so."""
        from src.metrics.epidemiology import icu_stay_completeness

        result = icu_metrics(connection, SP)
        completude = result.components["completude_da_permanencia_em_uti"]

        # A taxa de admissao segue com as 40 admissoes da base sintetica...
        assert result.numerator == 40
        # ...mas o censo diario usa apenas as 39 estadias coerentes.
        assert completude["admissoes_em_uti"] == 40
        assert completude["estadias_utilizaveis_no_censo"] == 39
        assert completude["excluidas_por_inconsistencia"] == 1

        assert icu_stay_completeness(connection, SP) == completude

    def test_completude_da_permanencia_quantifica_a_imputacao(self, connection):
        completude = icu_metrics(connection, SP).components["completude_da_permanencia_em_uti"]
        utilizaveis = completude["estadias_utilizaveis_no_censo"]

        soma = (
            completude["saida_registrada"]
            + completude["permanencia_imputada_pela_data_de_evolucao"]
            + completude["permanencia_imputada_ate_a_data_de_corte"]
        )
        assert soma == utilizaveis  # toda estadia cai em exatamente um caso
        assert completude["percentual_com_saida_registrada"] is not None
        assert "superestima" in completude["efeito_da_imputacao"]

    def test_indicador_nomeia_o_que_mede(self):
        definition = DEFINITIONS_BY_KEY["icu_admission_rate"]
        assert "NAO e taxa de ocupacao" in definition.limitations[0]


class TestVacinacao:
    def test_cobertura_entre_casos_notificados(self, connection):
        result = vaccination_metrics(connection, SP)
        # 30 vacinados em 120 casos com informacao preenchida.
        assert result.numerator == 30
        assert result.denominator == 120
        assert result.value == pytest.approx(25.0)

    def test_reporta_completude_da_informacao(self, connection):
        covid = vaccination_metrics(connection, SP).components["covid19"]
        assert covid["ignorado"] == 15
        assert covid["completude_da_informacao_pct"] == pytest.approx(80.0)

    def test_cobertura_populacional_e_declarada_nao_calculavel(self, connection):
        population = vaccination_metrics(connection, SP).components[
            "taxa_de_vacinacao_da_populacao"
        ]
        assert population["value"] is None
        assert "populacao geral" in population["unavailable_reason"]


class TestFiltros:
    def test_uf_invalida_e_rejeitada(self):
        with pytest.raises(InvalidFilterError):
            AnalyticFilters(uf="XX")

    def test_classificacao_invalida_e_rejeitada(self):
        with pytest.raises(InvalidFilterError):
            AnalyticFilters(classification=99)

    def test_uf_e_normalizada(self):
        assert AnalyticFilters(uf="sp").uf == "SP"

    def test_filtro_restringe_o_recorte(self, connection):
        nacional = case_growth_rate(connection)
        recorte = case_growth_rate(connection, AnalyticFilters(uf="RJ"))
        assert (
            recorte.components["casos_periodo_atual"] < nacional.components["casos_periodo_atual"]
        )

    def test_filtro_sem_resultado_nao_quebra(self, connection):
        result = mortality_rate(connection, AnalyticFilters(uf="AC"))
        assert result.value is None
        assert result.denominator == 0


class TestSeriesTemporais:
    def test_serie_diaria_tem_um_ponto_por_dia_incluindo_zeros(self, connection):
        series = daily_cases(connection, SP, window_days=30)
        assert len(series["points"]) == 30
        assert series["period"]["fim"] == EXPECTED_CUTOFF.isoformat()
        assert all("casos" in point for point in series["points"])

    def test_serie_mensal_marca_mes_parcial(self, connection):
        series = monthly_cases(connection, SP, window_months=12)
        assert len(series["points"]) == 12
        assert series["points"][-1]["parcial"] is True
        assert all(point["parcial"] is False for point in series["points"][:-1])

    def test_series_declaram_limitacoes(self, connection):
        assert daily_cases(connection)["limitations"]
        assert monthly_cases(connection)["limitations"]


class TestCompletudeDaNotificacao:
    def test_mede_o_atraso_observado_na_base(self, connection):
        profile = notification_delay_profile(connection)
        assert profile["registros_avaliados"] > 0
        assert profile["atraso_mediano_dias"] is not None
        assert profile["corte_configurado_dias"] == 21
