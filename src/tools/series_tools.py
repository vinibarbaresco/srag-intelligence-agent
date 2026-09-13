"""Tools das series temporais de casos de SRAG.

As series retornadas aqui sao as mesmas consumidas pelos graficos -- nao ha
recalculo na camada de visualizacao.
"""

from __future__ import annotations

from typing import Any

from src.config import DATASUS_SOURCE_LABEL
from src.data.load_database import connect
from src.metrics import timeseries
from src.metrics.filters import AnalyticFilters
from src.observability.audit import audited
from src.tools.schemas import DailySeriesQuery, MonthlySeriesQuery


@audited("get_daily_cases", source=DATASUS_SOURCE_LABEL)
def get_daily_cases(**kwargs: Any) -> dict[str, Any]:
    """Numero diario de casos de SRAG nos ultimos dias analisaveis.

    A janela termina na data de corte analitica (ultima data de digitacao menos
    o periodo sujeito a atraso de notificacao), nao na data de hoje. Dias sem
    casos aparecem com valor zero.
    """
    query = DailySeriesQuery(**kwargs)
    filters = AnalyticFilters(uf=query.uf, classification=query.classification)
    with connect() as connection:
        return timeseries.daily_cases(connection, filters, window_days=query.window_days)


@audited("get_monthly_cases", source=DATASUS_SOURCE_LABEL)
def get_monthly_cases(**kwargs: Any) -> dict[str, Any]:
    """Numero mensal de casos de SRAG nos ultimos meses analisaveis.

    O mes mais recente e marcado como `parcial` quando a janela analisavel
    termina antes do fim do mes, para que a queda do ultimo ponto nao seja
    interpretada como tendencia.
    """
    query = MonthlySeriesQuery(**kwargs)
    filters = AnalyticFilters(uf=query.uf, classification=query.classification)
    with connect() as connection:
        return timeseries.monthly_cases(
            connection, filters, window_months=query.window_months
        )
