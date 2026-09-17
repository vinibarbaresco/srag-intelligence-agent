"""Testes dos indicadores epidemiologicos.

Os valores esperados sao derivados da composicao explicita da base sintetica
(ver `tests/conftest.py`), nao de uma execucao anterior: se a regra de calculo
mudar, o teste falha por divergencia com a definicao, e nao por regressao muda.
"""

from __future__ import annotations

import pytest

from src.config import get_settings, reset_settings_cache
from src.metrics import epidemiology
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

    def test_taxa_de_admissao_aponta_para_a_ocupacao_sem_se_confundir_com_ela(self, connection):
        """Admissao e ocupacao sao indicadores distintos e o envelope diz isso."""
        occupancy = icu_metrics(connection, SP).components["taxa_de_ocupacao_de_leitos_de_uti"]
        assert occupancy["indicador"] == "icu_bed_occupancy_rate"
        assert "NAO e ocupacao" in occupancy["nota"]
        assert "value" not in occupancy

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
        assert result.records_used == 110  # hospitalizados no periodo
        # ...mas o censo diario usa apenas as 39 estadias coerentes -- a
        # populacao medida e a mesma que alimenta o censo.
        assert completude["estadias_no_censo"] == 39
        assert completude["populacao"].startswith("estadias em UTI que intersectam")

        assert icu_stay_completeness(connection, SP) == completude

    def test_completude_da_permanencia_quantifica_a_imputacao(self, connection):
        completude = icu_metrics(connection, SP).components["completude_da_permanencia_em_uti"]

        soma = (
            completude["saida_registrada"]
            + completude["permanencia_imputada_pela_data_de_evolucao"]
            + completude["permanencia_imputada_em_aberto"]
        )
        assert soma == completude["estadias_no_censo"]  # toda estadia cai em um so caso
        assert completude["percentual_com_saida_registrada"] == pytest.approx(100.0)
        assert completude["imputadas_truncadas_pelo_teto"] == 0
        assert "teto" in completude["efeito_da_imputacao"]

    def test_teto_de_permanencia_e_empirico_e_declarado(self, connection):
        from src.metrics.epidemiology import icu_stay_cap

        cap = icu_stay_cap(connection, SP)
        # Todas as estadias sinteticas com saida duram 3 dias: o p95 e 3.
        assert cap["dias"] == 3
        assert "percentil" in cap["origem"]

    def test_estadia_em_aberto_e_truncada_pelo_teto(self, connection, monkeypatch):
        """Sem o teto, uma estadia aberta contaria como internada ate o corte."""
        from src.config import get_settings
        from src.metrics.epidemiology import icu_patient_census

        settings = get_settings()
        monkeypatch.setattr(settings, "icu_stay_cap_days", 2)
        census = icu_patient_census(connection, SP)

        # Cada ponto declara quanto do valor depende de imputacao.
        assert all({"imputados", "percentual_imputado"} <= set(point) for point in census)
        # Com o teto forcado a 2 dias, nenhuma estadia sintetica (3 dias) pode
        # contribuir por mais de 3 dias consecutivos (entrada + 2).
        assert max(point["pacientes_em_uti"] for point in census) <= 40

    def test_pico_do_censo_declara_percentual_imputado(self, connection):
        components = icu_metrics(connection, SP).components
        assert components["censo_diario_pico_data"] is not None
        assert components["censo_diario_pico_percentual_imputado"] is not None

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
        assert "nao representa a populacao" in population["unavailable_reason"]


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


class TestMesParcial:
    def test_mes_e_parcial_apenas_se_o_corte_antecede_o_fim_do_mes(self, connection, monkeypatch):
        from src.config import get_settings

        settings = get_settings()
        # REFERENCE_DATE = 2026-08-23. Com lag 23, o corte cai em 31/07: mes completo.
        monkeypatch.setattr(settings, "reporting_lag_days", 23)
        series = monthly_cases(connection, SP, window_months=3)
        assert series["points"][-1]["mes"] == "2026-07"
        assert series["points"][-1]["parcial"] is False
        assert series["notes"] == []

        # Com o lag padrao (21) o corte cai em 02/08: agosto e parcial.
        monkeypatch.setattr(settings, "reporting_lag_days", 21)
        series = monthly_cases(connection, SP, window_months=3)
        assert series["points"][-1]["parcial"] is True


class TestViesDaLetalidade:
    def test_percentual_em_aberto_e_publicado(self, connection):
        result = mortality_rate(connection, SP)
        # 80 casos em aberto (EVOLUCAO nulo) em 150.
        assert result.components["casos_em_aberto"] == 80
        assert result.components["percentual_em_aberto"] == pytest.approx(53.33, abs=0.01)
        assert result.records_used == 150

    def test_limitacao_declara_a_direcao_do_vies(self):
        limitations = " ".join(DEFINITIONS_BY_KEY["mortality_rate"].limitations)
        assert "SUPERESTIMADA" in limitations


class TestIncidencia:
    def test_incidencia_nacional_usa_populacao_total(self, connection):
        result = epidemiology.incidence_rate(connection)
        # 151 casos (150 SP + 1 RJ) sobre 40 milhoes de habitantes.
        assert result.numerator == 151
        assert result.denominator == 40_000_000
        assert result.value == pytest.approx(0.38)
        assert result.components["ano_da_estimativa_populacional"] == 2026

    def test_incidencia_por_uf_usa_populacao_da_uf(self, connection):
        result = epidemiology.incidence_rate(connection, SP)
        assert result.numerator == 150
        assert result.denominator == 10_000_000
        assert result.value == pytest.approx(1.5)

    def test_sem_referencia_populacional_declara_indisponibilidade(self, tmp_path):
        import duckdb

        from src.data.reference.tables import TABLE_POPULATION

        # Banco com a view analitica mas com a tabela de populacao vazia.
        source = duckdb.connect(str(get_settings().database_path), read_only=True)
        try:
            frame = source.execute("SELECT * FROM srag_analytics").df()
        finally:
            source.close()
        isolated = duckdb.connect(str(tmp_path / "isolado.duckdb"))
        try:
            isolated.register("frame", frame)
            isolated.execute("CREATE VIEW srag_analytics AS SELECT * FROM frame")
            isolated.execute(
                f"CREATE TABLE {TABLE_POPULATION} (uf VARCHAR, ano INTEGER, populacao BIGINT)"
            )
            result = epidemiology.incidence_rate(isolated)
        finally:
            isolated.close()

        assert result.value is None
        assert "populacional" in result.unavailable_reason
        assert result.numerator == 151  # o numerador continua publicado


class TestBaselineSazonal:
    def test_excesso_nacional_frente_a_mediana(self, connection):
        result = epidemiology.seasonal_baseline(connection)
        assert result.components["casos_na_janela_atual"] == 151
        assert result.components["mediana_do_baseline"] == 150
        assert result.value == pytest.approx(0.67)
        assert result.components["anos_considerados"] == [2023, 2024]
        assert result.components["anos_ausentes_na_base"] == [2022]

    def test_recorte_por_uf_compara_com_a_mesma_uf(self, connection):
        result = epidemiology.seasonal_baseline(connection, SP)
        assert result.value == pytest.approx(0.0)
        assert result.components["casos_por_ano_de_baseline"]["2023"]["casos"] == 180
        assert result.components["casos_por_ano_de_baseline"]["2024"]["casos"] == 120

    def test_janela_comparada_preserva_mes_e_dia(self, connection):
        result = epidemiology.seasonal_baseline(connection)
        current = result.period
        compared = result.components["casos_por_ano_de_baseline"]["2024"]
        assert compared["inicio"][5:] == current["inicio"][5:]
        assert compared["fim"][5:] == current["fim"][5:]

    def test_anos_pandemicos_nunca_entram(self, connection, monkeypatch):
        monkeypatch.setenv("BASELINE_YEARS", "2020,2021,2023,2024")
        reset_settings_cache()
        try:
            result = epidemiology.seasonal_baseline(connection)
        finally:
            monkeypatch.undo()
            reset_settings_cache()
        assert result.components["anos_considerados"] == [2023, 2024]
        assert result.components["anos_excluidos_por_definicao"]["estavam_na_configuracao"] == [
            2020,
            2021,
        ]

    def test_poucos_anos_presentes_declara_indisponibilidade(self, connection, monkeypatch):
        monkeypatch.setenv("BASELINE_MIN_YEARS", "3")
        reset_settings_cache()
        try:
            result = epidemiology.seasonal_baseline(connection)
        finally:
            monkeypatch.undo()
            reset_settings_cache()
        assert result.value is None
        assert "2 ano(s) de baseline" in result.unavailable_reason
        # Os componentes continuam publicados: o leitor ve o que existe.
        assert result.components["anos_considerados"] == [2023, 2024]
