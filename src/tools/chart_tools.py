"""Tools de geracao dos graficos do relatorio.

Cada tool obtem a serie pela tool correspondente de `series_tools` e a entrega
ao renderizador -- garantindo que o grafico e o texto citem exatamente os mesmos
numeros.
"""

from __future__ import annotations

from typing import Any

from src.observability.audit import audited
from src.tools.schemas import ChartRequest
from src.tools.series_tools import get_daily_cases, get_monthly_cases
from src.visualization.charts import render_daily_cases_chart, render_monthly_cases_chart

_SOURCE_LABEL = "serie analitica do DATASUS (mesma consulta das tools de serie)"


@audited("render_daily_cases_chart", source=_SOURCE_LABEL)
def build_daily_cases_chart(**kwargs: Any) -> dict[str, Any]:
    """Gera o grafico de casos diarios dos ultimos 30 dias.

    Returns:
        Caminho do PNG, periodo coberto e a serie usada.
    """
    request = ChartRequest(**kwargs)
    series = get_daily_cases(uf=request.uf, classification=request.classification)
    path = render_daily_cases_chart(series)
    return {
        "chart": "casos_diarios",
        "path": str(path),
        "period": series["period"],
        "points": len(series["points"]),
        "series": series,
        "source": _SOURCE_LABEL,
    }


@audited("render_monthly_cases_chart", source=_SOURCE_LABEL)
def build_monthly_cases_chart(**kwargs: Any) -> dict[str, Any]:
    """Gera o grafico de casos mensais dos ultimos 12 meses.

    Returns:
        Caminho do PNG, periodo coberto e a serie usada.
    """
    request = ChartRequest(**kwargs)
    series = get_monthly_cases(uf=request.uf, classification=request.classification)
    path = render_monthly_cases_chart(series)
    return {
        "chart": "casos_mensais",
        "path": str(path),
        "period": series["period"],
        "points": len(series["points"]),
        "series": series,
        "source": _SOURCE_LABEL,
    }
