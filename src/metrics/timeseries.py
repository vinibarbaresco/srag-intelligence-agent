"""Series temporais de casos de SRAG.

As mesmas consultas alimentam as tools, os graficos e o relatorio -- nao ha
calculo duplicado entre a camada de visualizacao e a camada de metricas.

Dias e meses sem nenhum caso aparecem com valor zero em vez de serem omitidos:
uma lacuna na serie deve ser visivel como zero real, nao como ausencia de ponto.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.data.load_database import VIEW_ANALYTICS
from src.metrics.definitions import DAILY_CASES, MONTHLY_CASES, MetricDefinition
from src.metrics.filters import AnalyticFilters, analysis_cutoff

DEFAULT_DAILY_WINDOW_DAYS = 30
DEFAULT_MONTHLY_WINDOW_MONTHS = 12


def _envelope(
    definition: MetricDefinition,
    points: list[dict[str, Any]],
    start: date,
    end: date,
    filters: AnalyticFilters,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta o envelope padrao de retorno de uma serie temporal."""
    totals = [point["casos"] for point in points]
    return {
        "metric": definition.key,
        "unit": definition.unit,
        "points": points,
        "period": {
            "inicio": start.isoformat(),
            "fim": end.isoformat(),
            "descricao": definition.period,
        },
        "filters": filters.to_dict(),
        "summary": {
            "total_de_casos": sum(totals),
            "media_por_ponto": round(sum(totals) / len(totals), 2) if totals else None,
            "maximo": max(totals) if totals else None,
            "minimo": min(totals) if totals else None,
            "pontos": len(points),
        },
        "source": definition.source,
        "definition": definition.definition,
        "limitations": list(definition.limitations),
        **(extra or {}),
    }


def daily_cases(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int = DEFAULT_DAILY_WINDOW_DAYS,
) -> dict[str, Any]:
    """Numero diario de casos de SRAG nos ultimos `window_days` dias analisaveis.

    A janela termina na data de corte analitica (data de digitacao mais recente
    menos `REPORTING_LAG_DAYS`), nao na data de hoje.

    Args:
        connection: conexao DuckDB somente leitura.
        filters: recorte por UF e classificacao final.
        window_days: tamanho da janela em dias.

    Returns:
        Envelope com a serie, o periodo, o resumo, a fonte e as limitacoes.
    """
    filters = filters or AnalyticFilters()
    end = analysis_cutoff(connection)
    start = end - timedelta(days=window_days - 1)

    clause, parameters = filters.where_clause()
    rows = connection.execute(
        f"""
        WITH calendario AS (
            SELECT CAST(dia AS DATE) AS dia
            FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY) AS t(dia)
        ),
        casos AS (
            SELECT data_sintomas, count(*) AS total
            FROM {VIEW_ANALYTICS}
            WHERE data_sintomas BETWEEN ? AND ? AND {clause}
            GROUP BY data_sintomas
        )
        SELECT calendario.dia, coalesce(casos.total, 0)
        FROM calendario
        LEFT JOIN casos ON casos.data_sintomas = calendario.dia
        ORDER BY calendario.dia
        """,
        [start, end, start, end, *parameters],
    ).fetchall()

    points = [{"data": day.isoformat(), "casos": int(total)} for day, total in rows]
    return _envelope(DAILY_CASES, points, start, end, filters)


def monthly_cases(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_months: int = DEFAULT_MONTHLY_WINDOW_MONTHS,
) -> dict[str, Any]:
    """Numero mensal de casos de SRAG nos ultimos `window_months` meses.

    A janela cobre meses calendario completos ate o mes da data de corte. O
    ultimo mes e sinalizado como parcial quando a data de corte nao coincide com
    o fim do mes, para que a queda aparente do ultimo ponto nao seja lida como
    tendencia.
    """
    filters = filters or AnalyticFilters()
    end = analysis_cutoff(connection)
    last_month_start = end.replace(day=1)
    start = _subtract_months(last_month_start, window_months - 1)

    clause, parameters = filters.where_clause()
    rows = connection.execute(
        f"""
        WITH calendario AS (
            SELECT CAST(mes AS DATE) AS mes
            FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 MONTH) AS t(mes)
        ),
        casos AS (
            SELECT CAST(mes_sintomas AS DATE) AS mes, count(*) AS total
            FROM {VIEW_ANALYTICS}
            WHERE data_sintomas BETWEEN ? AND ? AND {clause}
            GROUP BY 1
        )
        SELECT calendario.mes, coalesce(casos.total, 0)
        FROM calendario
        LEFT JOIN casos ON casos.mes = calendario.mes
        ORDER BY calendario.mes
        """,
        [start, last_month_start, start, end, *parameters],
    ).fetchall()

    # O ultimo mes so e parcial se a janela terminar antes do seu ultimo dia.
    # Um corte exatamente no fim do mes produz um mes completo.
    last_day_of_month = _subtract_months(last_month_start, -1) - timedelta(days=1)
    last_month_is_partial = end < last_day_of_month

    points = [
        {
            "mes": month.strftime("%Y-%m"),
            "casos": int(total),
            "parcial": month == last_month_start and last_month_is_partial,
        }
        for month, total in rows
    ]
    notes = (
        [
            f"O mes {last_month_start.strftime('%Y-%m')} esta marcado como parcial: "
            f"a janela analisavel termina em {end.isoformat()}, antes do fim do mes."
        ]
        if points and last_month_is_partial
        else []
    )
    return _envelope(MONTHLY_CASES, points, start, end, filters, extra={"notes": notes})


def _subtract_months(reference: date, months: int) -> date:
    """Subtrai `months` meses de uma data ancorada no primeiro dia do mes."""
    total = reference.year * 12 + (reference.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)
