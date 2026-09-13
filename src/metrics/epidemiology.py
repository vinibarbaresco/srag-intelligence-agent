"""Calculo dos indicadores epidemiologicos, em SQL deterministico.

Nenhum numero deste modulo passa por um modelo de linguagem. Todo indicador e
uma consulta parametrizada sobre a view analitica do DuckDB e retorna o envelope
:class:`MetricResult`, com numerador, denominador, periodo, filtros, fonte e
limitacoes -- o que torna cada valor do relatorio rastreavel ate a consulta que
o produziu.

Regra transversal: quando o denominador e zero, ausente ou insuficiente, o
indicador retorna `value=None` com `unavailable_reason` preenchido. Nunca se
devolve zero no lugar de "nao calculavel".
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.config import get_settings
from src.data.load_database import VIEW_ANALYTICS
from src.metrics.definitions import (
    CASE_GROWTH_RATE,
    ICU_ADMISSION_RATE,
    ICU_BED_OCCUPANCY_RATE,
    MORTALITY_RATE,
    POPULATION_VACCINATION_COVERAGE,
    VACCINATION_COVERAGE,
    MetricDefinition,
    MetricResult,
)
from src.metrics.filters import AnalyticFilters, analysis_cutoff

_PERCENT_DECIMALS = 2


def _period(start: date, end: date, label: str) -> dict[str, Any]:
    return {"inicio": start.isoformat(), "fim": end.isoformat(), "descricao": label}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, _PERCENT_DECIMALS)


def _unavailable(
    definition: MetricDefinition,
    reason: str,
    *,
    period: dict[str, Any],
    filters: AnalyticFilters,
    numerator: int | None = None,
    denominator: int | None = None,
    components: dict[str, Any] | None = None,
) -> MetricResult:
    """Constroi um resultado explicitamente indisponivel (Guardrail 6)."""
    return MetricResult(
        metric=definition.key,
        value=None,
        numerator=numerator,
        denominator=denominator,
        period=period,
        filters=filters.to_dict(),
        definition=definition,
        components=components or {},
        unavailable_reason=reason,
    )


# =============================================================================
# Diagnostico de completude
# =============================================================================


def notification_delay_profile(
    connection: Any, filters: AnalyticFilters | None = None
) -> dict[str, Any]:
    """Mede o atraso de notificacao observado na propria base.

    O atraso entre o inicio dos sintomas e a digitacao da ficha determina quanto
    da serie recente ainda esta incompleto. Medi-lo -- em vez de assumir um valor
    -- permite dizer no relatorio *quanto* a janela recente e confiavel e
    verificar se `REPORTING_LAG_DAYS` esta calibrado.

    Args:
        connection: conexao DuckDB somente leitura.
        filters: recorte por UF e classificacao final.

    Returns:
        Percentis do atraso em dias, o corte configurado e um alerta quando o
        corte adotado e menor que o percentil 75 observado.
    """
    filters = filters or AnalyticFilters()
    settings = get_settings()
    cutoff = analysis_cutoff(connection)
    horizon = cutoff - timedelta(days=365)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            quantile_cont(date_diff('day', data_sintomas, data_digitacao), 0.5),
            quantile_cont(date_diff('day', data_sintomas, data_digitacao), 0.75),
            quantile_cont(date_diff('day', data_sintomas, data_digitacao), 0.9),
            count(*)
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ?
          AND data_digitacao IS NOT NULL
          AND {clause}
        """,
        [horizon, cutoff, *parameters],
    ).fetchone()

    median, p75, p90, sample = row
    configured = settings.reporting_lag_days
    under_calibrated = p75 is not None and configured < float(p75)

    return {
        "atraso_mediano_dias": float(median) if median is not None else None,
        "atraso_p75_dias": float(p75) if p75 is not None else None,
        "atraso_p90_dias": float(p90) if p90 is not None else None,
        "registros_avaliados": int(sample),
        "corte_configurado_dias": configured,
        "corte_suficiente": not under_calibrated,
        "alerta": (
            f"O corte configurado ({configured} dias) e menor que o atraso do "
            f"percentil 75 observado ({p75} dias): a janela recente ainda esta "
            "incompleta e a variacao de casos tende a ser subestimada."
            if under_calibrated
            else None
        ),
    }


# =============================================================================
# Indicador 1 -- Taxa de aumento de casos
# =============================================================================


def case_growth_rate(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Compara duas janelas consecutivas de casos por data de primeiros sintomas.

    Args:
        connection: conexao DuckDB somente leitura.
        filters: recorte por UF e classificacao final.
        window_days: tamanho de cada janela; padrao `GROWTH_WINDOW_DAYS`.

    Returns:
        Variacao percentual entre a janela atual e a anterior.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)

    current_start = cutoff - timedelta(days=window - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE data_sintomas BETWEEN ? AND ?) AS atual,
            count(*) FILTER (WHERE data_sintomas BETWEEN ? AND ?) AS anterior
        FROM {VIEW_ANALYTICS}
        WHERE {clause}
        """,
        [current_start, cutoff, previous_start, previous_end, *parameters],
    ).fetchone()

    current_cases, previous_cases = int(row[0]), int(row[1])
    period = _period(
        previous_start,
        cutoff,
        f"janela atual {current_start.isoformat()} a {cutoff.isoformat()} "
        f"comparada a {previous_start.isoformat()} a {previous_end.isoformat()}",
    )
    components = {
        "casos_periodo_atual": current_cases,
        "casos_periodo_anterior": previous_cases,
        "janela_dias": window,
        "periodo_atual": _period(current_start, cutoff, "janela atual"),
        "periodo_anterior": _period(previous_start, previous_end, "janela anterior"),
        "data_corte_analitica": cutoff.isoformat(),
        "completude_da_notificacao": notification_delay_profile(connection, filters),
    }

    if previous_cases == 0:
        return _unavailable(
            CASE_GROWTH_RATE,
            "A janela anterior nao possui casos registrados; a variacao "
            "percentual e matematicamente indefinida (divisao por zero).",
            period=period,
            filters=filters,
            numerator=current_cases - previous_cases,
            denominator=0,
            components=components,
        )

    value = round((current_cases - previous_cases) / previous_cases * 100, _PERCENT_DECIMALS)
    return MetricResult(
        metric=CASE_GROWTH_RATE.key,
        value=value,
        numerator=current_cases - previous_cases,
        denominator=previous_cases,
        period=period,
        filters=filters.to_dict(),
        definition=CASE_GROWTH_RATE,
        components=components,
    )


# =============================================================================
# Indicador 2 -- Taxa de mortalidade
# =============================================================================


def mortality_rate(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Letalidade entre casos encerrados de SRAG na janela analisada."""
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*)                                        AS casos,
            count(*) FILTER (WHERE caso_encerrado)           AS encerrados,
            count(*) FILTER (WHERE eh_obito_srag)            AS obitos_srag,
            count(*) FILTER (WHERE EVOLUCAO = 3)             AS obitos_outras_causas,
            count(*) FILTER (WHERE EVOLUCAO = 9)             AS evolucao_ignorada,
            count(*) FILTER (WHERE EVOLUCAO IS NULL)         AS evolucao_ausente
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    total, closed, deaths, other_deaths, ignored, absent = (int(value) for value in row)
    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")
    components = {
        "casos_no_periodo": total,
        "casos_encerrados": closed,
        "obitos_por_srag": deaths,
        "obitos_por_outras_causas": other_deaths,
        "evolucao_ignorada": ignored,
        "evolucao_nao_informada": absent,
        "casos_em_aberto": total - closed - ignored,
        "letalidade_bruta_sobre_todos_os_casos": (
            _ratio(deaths, total) if total else None
        ),
        "data_corte_analitica": cutoff.isoformat(),
    }

    if closed == 0:
        return _unavailable(
            MORTALITY_RATE,
            "Nao ha casos encerrados no periodo analisado; sem denominador "
            "elegivel, a taxa de mortalidade nao pode ser calculada.",
            period=period,
            filters=filters,
            numerator=deaths,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=MORTALITY_RATE.key,
        value=_ratio(deaths, closed),
        numerator=deaths,
        denominator=closed,
        period=period,
        filters=filters.to_dict(),
        definition=MORTALITY_RATE,
        components=components,
    )


# =============================================================================
# Indicador 3 -- UTI
# =============================================================================


def icu_metrics(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Indicadores de UTI, com a limitacao de ocupacao declarada.

    Retorna a taxa de **admissao** em UTI entre hospitalizados -- a unica
    proporcao calculavel com o SIVEP-Gripe -- e anexa em `components` o estado
    explicito de "nao calculavel" da taxa de ocupacao de leitos, alem do pico do
    censo diario de pacientes em UTI no periodo.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE foi_hospitalizado)                       AS hospitalizados,
            count(*) FILTER (WHERE foi_hospitalizado AND uti_informado)     AS uti_informado,
            count(*) FILTER (WHERE foi_hospitalizado AND teve_admissao_uti) AS admitidos_uti,
            count(*) FILTER (WHERE foi_hospitalizado AND UTI = 9)           AS uti_ignorado,
            count(*) FILTER (WHERE foi_hospitalizado AND UTI IS NULL)       AS uti_ausente,
            count(*) FILTER (
                WHERE teve_admissao_uti AND data_entrada_uti IS NULL
            )                                                               AS uti_sem_data_entrada
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    hospitalized, icu_known, icu_yes, icu_ignored, icu_absent, icu_no_date = (
        int(value) for value in row
    )
    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")

    census = icu_patient_census(connection, filters, window_days=window)
    peak = max((point["pacientes_em_uti"] for point in census), default=0)

    components = {
        "hospitalizados_no_periodo": hospitalized,
        "com_informacao_de_uti": icu_known,
        "admitidos_em_uti": icu_yes,
        "uti_ignorado": icu_ignored,
        "uti_nao_informado": icu_absent,
        "admitidos_sem_data_de_entrada_em_uti": icu_no_date,
        "censo_diario_pico_pacientes_em_uti": peak,
        "censo_diario": census,
        "taxa_de_ocupacao_de_leitos_de_uti": {
            "value": None,
            "unavailable_reason": ICU_BED_OCCUPANCY_RATE.not_computable_reason,
            "limitations": list(ICU_BED_OCCUPANCY_RATE.limitations),
        },
        "data_corte_analitica": cutoff.isoformat(),
    }

    if icu_known == 0:
        return _unavailable(
            ICU_ADMISSION_RATE,
            "Nenhum caso hospitalizado no periodo possui a informacao de "
            "internacao em UTI preenchida (1-Sim ou 2-Nao).",
            period=period,
            filters=filters,
            numerator=icu_yes,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=ICU_ADMISSION_RATE.key,
        value=_ratio(icu_yes, icu_known),
        numerator=icu_yes,
        denominator=icu_known,
        period=period,
        filters=filters.to_dict(),
        definition=ICU_ADMISSION_RATE,
        components=components,
    )


def icu_patient_census(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> list[dict[str, Any]]:
    """Serie diaria de pacientes de SRAG presentes em UTI.

    A permanencia de quem nao tem data de saida registrada e imputada ate a data
    de evolucao e, na ausencia dela, ate a data de corte -- o que superestima os
    dias mais recentes. A limitacao esta declarada em `ICU_PATIENT_CENSUS`.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    rows = connection.execute(
        f"""
        WITH calendario AS (
            SELECT CAST(dia AS DATE) AS dia
            FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY) AS t(dia)
        ),
        estadias AS (
            SELECT
                data_entrada_uti AS entrada,
                least(
                    coalesce(data_saida_uti, data_evolucao, CAST(? AS DATE)),
                    CAST(? AS DATE)
                ) AS saida
            FROM {VIEW_ANALYTICS}
            WHERE teve_admissao_uti
              AND data_entrada_uti IS NOT NULL
              AND {clause}
        )
        SELECT calendario.dia, count(estadias.entrada)
        FROM calendario
        LEFT JOIN estadias
          ON calendario.dia BETWEEN estadias.entrada AND estadias.saida
        GROUP BY calendario.dia
        ORDER BY calendario.dia
        """,
        [start, cutoff, cutoff, cutoff, *parameters],
    ).fetchall()

    return [
        {"data": day.isoformat(), "pacientes_em_uti": int(count)} for day, count in rows
    ]


# =============================================================================
# Indicador 4 -- Vacinacao
# =============================================================================


def vaccination_metrics(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Cobertura vacinal declarada entre casos notificados de SRAG.

    O indicador pedido no desafio -- "taxa de vacinacao da populacao" -- nao e
    calculavel com este dataset. O retorno traz a melhor aproximacao possivel
    (cobertura entre casos notificados) e mantem `population_vaccination_coverage`
    explicitamente nulo, com o motivo, em vez de apresentar a aproximacao sob o
    nome do indicador original.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*)                                                   AS casos,
            count(*) FILTER (WHERE vacina_covid_informada)             AS covid_informado,
            count(*) FILTER (WHERE vacinado_covid)                     AS covid_sim,
            count(*) FILTER (WHERE VACINA_COV = 9)                     AS covid_ignorado,
            count(*) FILTER (WHERE VACINA_COV IS NULL)                 AS covid_ausente,
            count(*) FILTER (WHERE vacina_influenza_informada)         AS influenza_informado,
            count(*) FILTER (WHERE vacinado_influenza)                 AS influenza_sim,
            count(*) FILTER (WHERE VACINA = 9)                         AS influenza_ignorado,
            count(*) FILTER (WHERE VACINA IS NULL)                     AS influenza_ausente
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    (
        total,
        covid_known,
        covid_yes,
        covid_ignored,
        covid_absent,
        flu_known,
        flu_yes,
        flu_ignored,
        flu_absent,
    ) = (int(value) for value in row)

    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")
    components = {
        "casos_no_periodo": total,
        "covid19": {
            "com_informacao": covid_known,
            "vacinados": covid_yes,
            "ignorado": covid_ignored,
            "nao_informado": covid_absent,
            "cobertura_declarada_pct": _ratio(covid_yes, covid_known) if covid_known else None,
            "completude_da_informacao_pct": _ratio(covid_known, total) if total else None,
        },
        "influenza": {
            "com_informacao": flu_known,
            "vacinados": flu_yes,
            "ignorado": flu_ignored,
            "nao_informado": flu_absent,
            "cobertura_declarada_pct": _ratio(flu_yes, flu_known) if flu_known else None,
            "completude_da_informacao_pct": _ratio(flu_known, total) if total else None,
        },
        "taxa_de_vacinacao_da_populacao": {
            "value": None,
            "unavailable_reason": POPULATION_VACCINATION_COVERAGE.not_computable_reason,
            "limitations": list(POPULATION_VACCINATION_COVERAGE.limitations),
        },
        "data_corte_analitica": cutoff.isoformat(),
    }

    if covid_known == 0:
        return _unavailable(
            VACCINATION_COVERAGE,
            "Nenhum caso do periodo possui a informacao de vacinacao contra "
            "covid-19 preenchida (1-Sim ou 2-Nao).",
            period=period,
            filters=filters,
            numerator=covid_yes,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=VACCINATION_COVERAGE.key,
        value=_ratio(covid_yes, covid_known),
        numerator=covid_yes,
        denominator=covid_known,
        period=period,
        filters=filters.to_dict(),
        definition=VACCINATION_COVERAGE,
        components=components,
    )
