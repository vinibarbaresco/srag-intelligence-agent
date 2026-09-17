"""Versao interativa (Plotly) dos dois graficos obrigatorios, para o HTML.

O PNG estatico (`src.visualization.charts`) continua sendo o artefato das
tools -- consumido pela API, pelo anexo tecnico e por quem abre o relatorio sem
rede. Este modulo gera a mesma leitura (mesmos pontos, mesmo pico, mesma media
movel, vindos de `src.visualization.series_insights`) como um componente HTML
autocontido com hover, zoom e legenda clicavel, para a experiencia principal do
relatorio HTML. Os dois nunca podem divergir porque partem do mesmo envelope de
serie e da mesma camada de leituras.

Cada componente e emitido nas duas paletas do relatorio. A pagina desenha na
paleta clara e registra, em `window.__sragCharts`, os ajustes de cor da paleta
escura -- o alternador de tema do relatorio aplica esses ajustes com
`Plotly.relayout`/`Plotly.restyle`. Sem isso o grafico continuaria branco
dentro de uma pagina escura: um SVG do Plotly nao herda cor de CSS.

Plotly.js e carregado uma unica vez, via CDN, no `<head>` do relatorio
(`src.agent.report`). Sem rede, o componente permanece como uma area vazia --
por isso o PNG estatico e sempre embutido tambem, como versao de impressao e
como conteudo de fallback (ver `.chart-print-only` no CSS do relatorio).
"""

from __future__ import annotations

import json
from typing import Any

from src.visualization.series_insights import daily_insights, format_month_label, monthly_insights

#: Uma paleta por tema. As chaves sao as mesmas nos dois: quem escreve um
#: grafico pede `palette["accent"]` e nao precisa saber em que tema esta.
PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "accent": "#0F6B62",
        "secondary": "#B8C0CC",
        "peak": "#C2521B",
        "ink": "#1A2331",
        "muted": "#6B7280",
        "surface": "#FFFFFF",
        "grid": "#EEF0F2",
        "axis": "#D7DBE0",
        "area": "rgba(15,107,98,0.08)",
    },
    "dark": {
        "accent": "#4FD1C0",
        "secondary": "#5A6678",
        "peak": "#F59356",
        "ink": "#E6EDF3",
        "muted": "#9AA4B2",
        "surface": "#171C24",
        "grid": "#262D38",
        "axis": "#39424F",
        "area": "rgba(79,209,192,0.12)",
    },
}

#: Atributos de cor do layout que mudam com o tema -- em notacao pontilhada,
#: que e a que `Plotly.relayout` entende. Cor de tick de eixo nao entra: sem
#: `tickfont.color` proprio, o eixo herda `font.color`.
_THEMED_LAYOUT_KEYS = (
    "paper_bgcolor",
    "plot_bgcolor",
    "font.color",
    "hoverlabel.bgcolor",
    "hoverlabel.bordercolor",
    "hoverlabel.font.color",
    "legend.font.color",
    "xaxis.linecolor",
    "yaxis.gridcolor",
)

#: Atributos de cor de trace que mudam com o tema.
_THEMED_TRACE_KEYS = ("line.color", "marker.color", "fillcolor", "textfont.color")

_CONFIG = {
    # So no hover: a barra de ferramentas fixa no canto superior direito
    # cobria a legenda em tela estreita, e zoom nao e a acao principal de quem
    # le um relatorio -- e uma saida para quem quer investigar um trecho.
    "displayModeBar": "hover",
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


def _layout_base(palette: dict[str, str]) -> dict[str, Any]:
    return {
        "font": {
            "family": "'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif",
            "color": palette["ink"],
        },
        "margin": {"l": 52, "r": 18, "t": 16, "b": 44},
        "plot_bgcolor": palette["surface"],
        "paper_bgcolor": palette["surface"],
        "hovermode": "x unified",
        "hoverlabel": {
            "bgcolor": palette["surface"],
            "bordercolor": palette["axis"],
            "font": {"color": palette["ink"]},
        },
        "modebar": {"orientation": "v", "color": palette["muted"]},
        # Legenda abaixo do eixo: em cima ela disputava a faixa do topo com a
        # barra de ferramentas. `autoexpand` (padrao) abre a margem inferior
        # para ela, e `automargin` faz o mesmo pelos rotulos do eixo.
        "legend": {
            "orientation": "h",
            "y": -0.18,
            "yanchor": "top",
            "x": 0,
            "font": {"size": 12, "color": palette["muted"]},
        },
        "xaxis": {
            "showgrid": False,
            "showline": True,
            "linecolor": palette["axis"],
            "tickfont": {"size": 11},
            "automargin": True,
        },
        "yaxis": {
            "showgrid": True,
            "gridcolor": palette["grid"],
            "zeroline": False,
            "tickfont": {"size": 11},
            "separatethousands": True,
        },
    }


def _dig(source: dict[str, Any], dotted: str) -> Any:
    cursor: Any = source
    for part in dotted.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _layout_patch(layout: dict[str, Any]) -> dict[str, Any]:
    return {key: _dig(layout, key) for key in _THEMED_LAYOUT_KEYS if _dig(layout, key) is not None}


def _trace_patches(data: list[dict[str, Any]]) -> list[list[Any]]:
    """Ajustes de cor por trace, no formato `[indice, {atributo: valor}]`.

    `Plotly.restyle` aplicado a um indice de trace por vez: mais chamadas, mas
    sem depender de alinhamento posicional entre listas de cores e traces --
    que quebraria em silencio se um trace passasse a ser condicional.
    """
    patches: list[list[Any]] = []
    for index, trace in enumerate(data):
        patch = {
            key: _dig(trace, key) for key in _THEMED_TRACE_KEYS if _dig(trace, key) is not None
        }
        if patch:
            patches.append([index, patch])
    return patches


def _themed_component(
    element_id: str,
    build_traces: Any,
    layout_extra: Any = None,
) -> str:
    """Desenha na paleta clara e registra o ajuste das duas paletas.

    `build_traces` e `layout_extra` recebem a paleta: o mesmo codigo produz o
    grafico nos dois temas, entao nenhuma cor pode ficar para tras quando um
    trace novo aparecer.
    """
    themes: dict[str, dict[str, Any]] = {}
    light_data: list[dict[str, Any]] = []
    light_layout: dict[str, Any] = {}

    for theme, palette in PALETTES.items():
        data = build_traces(palette)
        layout = {**_layout_base(palette), **(layout_extra(palette) if layout_extra else {})}
        themes[theme] = {"layout": _layout_patch(layout), "traces": _trace_patches(data)}
        if theme == "light":
            light_data, light_layout = data, layout

    return (
        f'<div class="chart-interactive" id="{element_id}"></div>\n'
        "<script>\n"
        "(function () {\n"
        f"  var id = {json.dumps(element_id)};\n"
        "  window.__sragCharts = window.__sragCharts || {};\n"
        f"  window.__sragCharts[id] = {json.dumps(themes)};\n"
        "  if (typeof Plotly === 'undefined') { return; }\n"
        f"  Plotly.newPlot(id, {json.dumps(light_data)}, "
        f"{json.dumps(light_layout)}, {json.dumps(_CONFIG)});\n"
        "  if (window.__sragPaintCharts) { window.__sragPaintCharts(); }\n"
        "})();\n"
        "</script>"
    )


def daily_chart_component(series: dict[str, Any], element_id: str = "chart-daily") -> str:
    """Componente Plotly da serie diaria: casos, media movel de 7 dias e pico."""
    insights = daily_insights(series)
    if not insights.dates:
        return '<p class="chart-empty">Sem dados suficientes para o gráfico diário.</p>'

    def build(palette: dict[str, str]) -> list[dict[str, Any]]:
        return [
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Casos por dia",
                "x": insights.dates,
                "y": insights.values,
                "line": {"color": palette["secondary"], "width": 1.4},
                "fill": "tozeroy",
                "fillcolor": palette["area"],
                "hovertemplate": "%{x|%d/%m/%Y}<br>Casos: %{y}<extra></extra>",
            },
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Média móvel de 7 dias",
                "x": insights.dates,
                "y": insights.moving_average,
                "line": {"color": palette["accent"], "width": 3},
                "hovertemplate": "%{x|%d/%m/%Y}<br>Média 7d: %{y:.1f}<extra></extra>",
            },
            {
                "type": "scatter",
                "mode": "markers+text",
                "name": "Pico",
                "x": [insights.peak_date],
                "y": [insights.peak_value],
                "marker": {"color": palette["peak"], "size": 10},
                "text": [f"pico: {insights.peak_value}"],
                "textposition": "top center",
                "textfont": {"color": palette["peak"], "size": 11},
                "hovertemplate": "%{x|%d/%m/%Y}<br>Pico: %{y}<extra></extra>",
                "showlegend": False,
            },
            {
                "type": "scatter",
                "mode": "markers+text",
                "name": "Último valor",
                "x": [insights.last_date],
                "y": [insights.last_value],
                "marker": {"color": palette["ink"], "size": 9},
                "text": [f"último: {insights.last_value}"],
                "textposition": "bottom center",
                "textfont": {"color": palette["ink"], "size": 11},
                "hovertemplate": "%{x|%d/%m/%Y}<br>Último valor: %{y}<extra></extra>",
                "showlegend": False,
            },
        ]

    return _themed_component(
        element_id,
        build,
        lambda palette: {"xaxis": {**_layout_base(palette)["xaxis"], "type": "date"}},
    )


def monthly_chart_component(series: dict[str, Any], element_id: str = "chart-monthly") -> str:
    """Componente Plotly da serie mensal: barras coloridas por pico/mes parcial."""
    insights = monthly_insights(series)
    if not insights.months:
        return '<p class="chart-empty">Sem dados suficientes para o gráfico mensal.</p>'

    labels = [format_month_label(month) for month in insights.months]

    def build(palette: dict[str, str]) -> list[dict[str, Any]]:
        colors = []
        for month, partial in zip(insights.months, insights.partial_flags, strict=True):
            if partial:
                colors.append(palette["secondary"])
            elif month == insights.peak_month:
                colors.append(palette["peak"])
            else:
                colors.append(palette["accent"])

        bar_trace = {
            "type": "bar",
            "name": "Casos por mês",
            "x": labels,
            "y": insights.values,
            "marker": {"color": colors},
            "text": [f"{value:,}".replace(",", ".") for value in insights.values],
            "textposition": "outside",
            "textfont": {"size": 10.5, "color": palette["muted"]},
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
                ("Casos por mês", palette["accent"]),
                ("Mês de pico", palette["peak"]),
                *([("Mês parcial", palette["secondary"])] if any(insights.partial_flags) else []),
            )
        ]
        return [bar_trace, *legend_traces]

    return _themed_component(
        element_id,
        build,
        lambda palette: {"yaxis": {**_layout_base(palette)["yaxis"], "rangemode": "tozero"}},
    )
