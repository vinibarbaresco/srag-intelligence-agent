"""Versao interativa (Plotly) dos dois graficos obrigatorios, para o HTML.

O PNG estatico (`src.visualization.charts`) continua sendo o artefato das
tools -- consumido pela API, pelo anexo tecnico e por quem abre o relatorio sem
rede. Este modulo gera a mesma leitura (mesmos pontos, mesmo pico, mesma media
movel, vindos de `src.visualization.series_insights`) como um componente HTML
autocontido com hover, zoom e legenda clicavel, para a experiencia principal do
relatorio HTML. Os dois nunca podem divergir porque partem do mesmo envelope de
serie e da mesma camada de leituras.

Plotly.js e carregado uma unica vez, via CDN, no `<head>` do relatorio
(`src.agent.report`). Sem rede, o componente permanece como uma area vazia --
por isso o PNG estatico e sempre embutido tambem, como versao de impressao e
como conteudo de fallback (ver `_PRINT_ONLY` no CSS do relatorio).
"""

from __future__ import annotations

import json
from typing import Any

from src.visualization.series_insights import daily_insights, format_month_label, monthly_insights

_ACCENT = "#0F6B62"
_SECONDARY = "#B8C0CC"
_PEAK = "#C2521B"
_INK = "#1A2331"
_MUTED = "#6B7280"

_CONFIG = {
    "displayModeBar": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "select2d",
        "lasso2d",
        "autoScale2d",
        "hoverCompareCartesian",
        "toggleSpikelines",
    ],
    "responsive": True,
    "locale": "pt-br",
}

_LAYOUT_BASE: dict[str, Any] = {
    "font": {"family": "'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif", "color": _INK},
    "margin": {"l": 48, "r": 16, "t": 12, "b": 40},
    "plot_bgcolor": "white",
    "paper_bgcolor": "white",
    "hovermode": "x unified",
    "hoverlabel": {"bgcolor": "white", "bordercolor": "#D7DBE0", "font": {"color": _INK}},
    "legend": {
        "orientation": "h",
        "y": 1.12,
        "x": 0,
        "font": {"size": 12, "color": _MUTED},
    },
    "xaxis": {
        "showgrid": False,
        "showline": True,
        "linecolor": "#D7DBE0",
        "tickfont": {"size": 11},
    },
    "yaxis": {
        "showgrid": True,
        "gridcolor": "#EEF0F2",
        "zeroline": False,
        "tickfont": {"size": 11},
        "separatethousands": True,
    },
}


def _component(element_id: str, data: list[dict[str, Any]], layout: dict[str, Any]) -> str:
    merged_layout = {**_LAYOUT_BASE, **layout}
    return (
        f'<div class="chart-interactive" id="{element_id}"></div>\n'
        "<script>\n"
        f"Plotly.newPlot({json.dumps(element_id)}, "
        f"{json.dumps(data)}, {json.dumps(merged_layout)}, {json.dumps(_CONFIG)});\n"
        "</script>"
    )


def daily_chart_component(series: dict[str, Any], element_id: str = "chart-daily") -> str:
    """Componente Plotly da serie diaria: casos, media movel de 7 dias e pico."""
    insights = daily_insights(series)
    if not insights.dates:
        return '<p class="chart-empty">Sem dados suficientes para o gráfico diário.</p>'

    daily_trace = {
        "type": "scatter",
        "mode": "lines",
        "name": "Casos por dia",
        "x": insights.dates,
        "y": insights.values,
        "line": {"color": _SECONDARY, "width": 1.4},
        "fill": "tozeroy",
        "fillcolor": "rgba(15,107,98,0.07)",
        "hovertemplate": "%{x|%d/%m/%Y}<br>Casos: %{y}<extra></extra>",
    }
    average_trace = {
        "type": "scatter",
        "mode": "lines",
        "name": "Média móvel de 7 dias",
        "x": insights.dates,
        "y": insights.moving_average,
        "line": {"color": _ACCENT, "width": 3},
        "hovertemplate": "%{x|%d/%m/%Y}<br>Média 7d: %{y:.1f}<extra></extra>",
    }
    peak_trace = {
        "type": "scatter",
        "mode": "markers+text",
        "name": "Pico",
        "x": [insights.peak_date],
        "y": [insights.peak_value],
        "marker": {"color": _PEAK, "size": 10},
        "text": [f"pico: {insights.peak_value}"],
        "textposition": "top center",
        "textfont": {"color": _PEAK, "size": 11},
        "hovertemplate": "%{x|%d/%m/%Y}<br>Pico: %{y}<extra></extra>",
        "showlegend": False,
    }
    last_trace = {
        "type": "scatter",
        "mode": "markers+text",
        "name": "Último valor",
        "x": [insights.last_date],
        "y": [insights.last_value],
        "marker": {"color": _INK, "size": 9},
        "text": [f"último: {insights.last_value}"],
        "textposition": "bottom center",
        "textfont": {"color": _INK, "size": 11},
        "hovertemplate": "%{x|%d/%m/%Y}<br>Último valor: %{y}<extra></extra>",
        "showlegend": False,
    }

    return _component(
        element_id,
        [daily_trace, average_trace, peak_trace, last_trace],
        {"xaxis": {**_LAYOUT_BASE["xaxis"], "type": "date"}},
    )


def monthly_chart_component(series: dict[str, Any], element_id: str = "chart-monthly") -> str:
    """Componente Plotly da serie mensal: barras coloridas por pico/mes parcial."""
    insights = monthly_insights(series)
    if not insights.months:
        return '<p class="chart-empty">Sem dados suficientes para o gráfico mensal.</p>'

    labels = [format_month_label(month) for month in insights.months]
    colors = []
    for month, partial in zip(insights.months, insights.partial_flags, strict=True):
        if partial:
            colors.append(_SECONDARY)
        elif month == insights.peak_month:
            colors.append(_PEAK)
        else:
            colors.append(_ACCENT)

    bar_trace = {
        "type": "bar",
        "name": "Casos por mês",
        "x": labels,
        "y": insights.values,
        "marker": {"color": colors},
        "text": [f"{value:,}".replace(",", ".") for value in insights.values],
        "textposition": "outside",
        "textfont": {"size": 10.5, "color": _MUTED},
        "hovertemplate": "%{x}<br>Casos: %{y}<extra></extra>",
        "showlegend": False,
    }

    # Legenda manual: o Plotly nao criaria entradas separadas para uma unica
    # serie de barras com cores por ponto, e a legenda e o que explica o
    # significado das tres cores.
    legend_traces = [
        {
            "type": "bar",
            "name": name,
            "x": [None],
            "y": [None],
            "marker": {"color": color},
            "showlegend": True,
        }
        for name, color in (
            ("Casos por mês", _ACCENT),
            ("Mês de pico", _PEAK),
            *([("Mês parcial", _SECONDARY)] if any(insights.partial_flags) else []),
        )
    ]

    return _component(
        element_id,
        [bar_trace, *legend_traces],
        {"yaxis": {**_LAYOUT_BASE["yaxis"], "rangemode": "tozero"}},
    )
