"""Geracao dos graficos do relatorio.

Os graficos consomem exatamente as mesmas series produzidas por
`src.metrics.timeseries` -- a camada de visualizacao nao recalcula nada. Isso
garante que o numero impresso no texto e a altura da barra no grafico venham da
mesma consulta. As leituras (pico, tendencia, ultimo valor) vem de
`src.visualization.series_insights`, a mesma camada que alimenta o relatorio
HTML -- o headline acima do grafico e o texto do relatorio nunca divergem
porque sao a mesma conta.

Estilo: uma cor de tinta (`_INK`) para texto e eixo, um unico accent para o
dado principal, um tom neutro para o secundario (media movel ou mes parcial).
Sem grade vertical, sem moldura completa, sem gradiente -- o objetivo e o
grafico comunicar uma mensagem em poucos segundos, nao decorar o eixo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # backend sem display, obrigatorio para execucao headless

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from src.config import DATASUS_SOURCE_LABEL, get_settings  # noqa: E402
from src.observability.logging_config import get_logger  # noqa: E402
from src.visualization.series_insights import (  # noqa: E402
    daily_insights,
    format_month_label,
    monthly_insights,
)

logger = get_logger(__name__)

# --- Paleta -------------------------------------------------------------------
_INK = "#1A2331"
_MUTED = "#6B7280"
_ACCENT = "#0F6B62"
_ACCENT_SOFT = "#CFE6E2"
_SECONDARY = "#B8C0CC"
_GRID = "#E7E9EC"
_PEAK = "#C2521B"

_FIGSIZE = (11, 4.9)
_DPI = 140


def _thousands(value: float, _position: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _base_figure() -> tuple[Any, Any]:
    plt.rcParams["font.family"] = "sans-serif"
    figure, axes = plt.subplots(figsize=_FIGSIZE, dpi=_DPI)
    figure.patch.set_facecolor("white")
    axes.set_facecolor("white")
    return figure, axes


def _style_axes(axes: Any) -> None:
    axes.grid(axis="y", color=_GRID, linewidth=0.9, zorder=0)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(_MUTED)
    axes.spines["bottom"].set_linewidth(0.8)
    axes.tick_params(labelsize=8.6, colors=_MUTED, length=0)
    axes.yaxis.set_major_formatter(FuncFormatter(_thousands))


def _headline(figure: Any, headline: str, subtitle: str) -> None:
    figure.text(0.01, 0.965, headline, fontsize=14.5, fontweight="bold", color=_INK)
    figure.text(0.01, 0.905, subtitle, fontsize=9, color=_MUTED)


def _footer(figure: Any, note: str) -> None:
    figure.text(0.01, 0.015, f"Fonte: {DATASUS_SOURCE_LABEL}. {note}", fontsize=7.3, color=_MUTED)


def _period_subtitle(series: dict[str, Any]) -> str:
    period = series["period"]
    filters = series["filters"]
    return (
        f"{period['inicio']} a {period['fim']}  |  {filters['uf']}  |  "
        f"{filters['classificacao_final']}"
    )


def render_daily_cases_chart(series: dict[str, Any], output_path: Path | None = None) -> Path:
    """Desenha a serie diaria de casos de SRAG.

    Args:
        series: envelope retornado por `src.metrics.timeseries.daily_cases`.
        output_path: destino do PNG; padrao `outputs/charts/casos_diarios.png`.

    Returns:
        Caminho do arquivo gerado.

    Raises:
        ValueError: se a serie estiver vazia.
    """
    points = series.get("points") or []
    if not points:
        raise ValueError("Serie diaria vazia: nao ha dados para plotar.")

    settings = get_settings()
    settings.ensure_directories()
    target = output_path or settings.charts_dir / "casos_diarios.png"

    insights = daily_insights(series)
    dates = [_parse_iso_date(d) for d in insights.dates]

    figure, axes = _base_figure()
    figure.subplots_adjust(top=0.80, bottom=0.16, left=0.06, right=0.98)

    axes.fill_between(dates, insights.values, color=_ACCENT, alpha=0.10, zorder=1)
    axes.plot(
        dates,
        insights.values,
        color=_SECONDARY,
        linewidth=1.3,
        marker="o",
        markersize=2.6,
        zorder=2,
        label="Casos por dia",
    )
    moving_average = [value for value in insights.moving_average]
    axes.plot(
        dates,
        moving_average,
        color=_ACCENT,
        linewidth=2.4,
        zorder=3,
        label="Média móvel de 7 dias",
    )

    # Linha de referencia da media do periodo: da escala ao "alto" e "baixo"
    # da serie sem exigir que o leitor faca a media de cabeca.
    window_mean = sum(insights.values) / len(insights.values)
    axes.axhline(window_mean, color=_MUTED, linewidth=0.9, linestyle=(0, (5, 4)), zorder=2)
    axes.annotate(
        f"média do período: {_thousands(round(window_mean), 0)}",
        (dates[0], window_mean),
        textcoords="offset points",
        xytext=(2, -12),
        fontsize=7.6,
        color=_MUTED,
        zorder=5,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
    )

    # Pico: destacado com marcador e rotulo, para que o valor mais alto da
    # janela seja lido sem precisar passar o mouse (o PNG e estatico).
    peak_x = _parse_iso_date(insights.peak_date)
    axes.scatter([peak_x], [insights.peak_value], color=_PEAK, s=42, zorder=4)
    axes.annotate(
        f"pico: {insights.peak_value}",
        (peak_x, insights.peak_value),
        textcoords="offset points",
        xytext=(0, 10),
        ha="center",
        fontsize=8.2,
        fontweight="bold",
        color=_PEAK,
    )

    # Ultimo valor: emend com rotulo, e a leitura mais recente da serie.
    last_x = _parse_iso_date(insights.last_date)
    axes.scatter([last_x], [insights.last_value], color=_INK, s=34, zorder=4)
    axes.annotate(
        f"último: {insights.last_value}",
        (last_x, insights.last_value),
        textcoords="offset points",
        xytext=(-6, -16),
        ha="right",
        fontsize=8.2,
        fontweight="bold",
        color=_INK,
    )

    _style_axes(axes)
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    axes.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(points) // 10)))
    axes.legend(
        loc="upper left",
        bbox_to_anchor=(0.0, 1.10),
        ncol=2,
        frameon=False,
        fontsize=8.4,
        labelcolor=_MUTED,
        handlelength=1.4,
    )

    _headline(figure, insights.headline, _period_subtitle(series))
    _footer(
        figure,
        "Série por data de início de sintomas, já descontado o período sujeito a "
        "atraso de notificação.",
    )
    figure.savefig(target, bbox_inches=None)
    plt.close(figure)

    logger.info("grafico gerado", extra={"arquivo": str(target), "pontos": len(points)})
    return target


def render_monthly_cases_chart(series: dict[str, Any], output_path: Path | None = None) -> Path:
    """Desenha a serie mensal de casos de SRAG.

    Meses incompletos sao plotados em tom claro e identificados na legenda, para
    que a queda do ultimo ponto nao seja interpretada como tendencia.

    Args:
        series: envelope retornado por `src.metrics.timeseries.monthly_cases`.
        output_path: destino do PNG; padrao `outputs/charts/casos_mensais.png`.

    Returns:
        Caminho do arquivo gerado.

    Raises:
        ValueError: se a serie estiver vazia.
    """
    points = series.get("points") or []
    if not points:
        raise ValueError("Serie mensal vazia: nao ha dados para plotar.")

    settings = get_settings()
    settings.ensure_directories()
    target = output_path or settings.charts_dir / "casos_mensais.png"

    insights = monthly_insights(series)
    labels = [format_month_label(month) for month in insights.months]

    figure, axes = _base_figure()
    figure.subplots_adjust(top=0.80, bottom=0.16, left=0.06, right=0.98)

    colors = []
    for month, partial in zip(insights.months, insights.partial_flags, strict=True):
        if partial:
            colors.append(_SECONDARY)
        elif month == insights.peak_month:
            colors.append(_PEAK)
        else:
            colors.append(_ACCENT)

    bars = axes.bar(labels, insights.values, color=colors, width=0.66, zorder=2)

    # Rotular as doze barras poluia sem informar: a grade ja da a magnitude.
    # Rotulados ficam os tres pontos que a leitura usa -- inicio da janela,
    # pico e mes mais recente -- em negrito no pico, que e a mensagem.
    labelled = {0, len(insights.values) - 1, insights.values.index(insights.peak_value)}
    for index, (bar, value) in enumerate(zip(bars, insights.values, strict=True)):
        if index not in labelled:
            continue
        is_peak = insights.months[index] == insights.peak_month
        axes.annotate(
            _thousands(value, 0),
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center",
            va="bottom",
            fontsize=8.2 if is_peak else 7.8,
            fontweight="bold" if is_peak else "normal",
            color=_PEAK if is_peak else _MUTED,
        )

    _style_axes(axes)

    handles = [
        plt.Rectangle((0, 0), 1, 1, color=_ACCENT, label="Casos por mês"),
        plt.Rectangle((0, 0), 1, 1, color=_PEAK, label="Mês de pico"),
    ]
    if any(insights.partial_flags):
        handles.append(plt.Rectangle((0, 0), 1, 1, color=_SECONDARY, label="Mês parcial"))
    axes.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.0, 1.10),
        ncol=3,
        frameon=False,
        fontsize=8.4,
        labelcolor=_MUTED,
        handlelength=1.1,
    )

    _headline(figure, insights.headline, _period_subtitle(series))
    _footer(
        figure,
        "Série por data de início de sintomas. Meses em tom claro estão incompletos.",
    )
    figure.savefig(target, bbox_inches=None)
    plt.close(figure)

    logger.info("grafico gerado", extra={"arquivo": str(target), "pontos": len(points)})
    return target


def _parse_iso_date(value: str):
    from datetime import date

    return date.fromisoformat(value)
