"""Geracao dos graficos do relatorio.

Os graficos consomem exatamente as mesmas series produzidas por
`src.metrics.timeseries` -- a camada de visualizacao nao recalcula nada. Isso
garante que o numero impresso no texto e a altura da barra no grafico venham da
mesma consulta.
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

logger = get_logger(__name__)

_LINE_COLOR = "#1f4e79"
_BAR_COLOR = "#2e75b6"
_PARTIAL_COLOR = "#bdd7ee"
_GRID_COLOR = "#d9d9d9"
_FIGSIZE = (11, 4.8)
_DPI = 130


def _thousands(value: float, _position: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _style_axes(axes: Any, title: str, ylabel: str, subtitle: str) -> None:
    axes.set_title(title, fontsize=13, fontweight="bold", loc="left", pad=18)
    axes.text(
        0.0,
        1.03,
        subtitle,
        transform=axes.transAxes,
        fontsize=8.5,
        color="#595959",
    )
    axes.set_ylabel(ylabel, fontsize=9)
    axes.yaxis.set_major_formatter(FuncFormatter(_thousands))
    axes.grid(axis="y", color=_GRID_COLOR, linewidth=0.7)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    axes.tick_params(labelsize=8.5)


def _footer(figure: Any, note: str) -> None:
    figure.text(
        0.01,
        0.01,
        f"Fonte: {DATASUS_SOURCE_LABEL}. {note}",
        fontsize=7.5,
        color="#595959",
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

    dates = [_parse_iso_date(point["data"]) for point in points]
    values = [point["casos"] for point in points]

    figure, axes = plt.subplots(figsize=_FIGSIZE, dpi=_DPI)
    axes.plot(dates, values, color=_LINE_COLOR, linewidth=2, marker="o", markersize=3.5)
    axes.fill_between(dates, values, color=_LINE_COLOR, alpha=0.12)

    period = series["period"]
    _style_axes(
        axes,
        f"Numero diario de casos de SRAG - ultimos {len(points)} dias",
        "Casos por data dos primeiros sintomas",
        f"{period['inicio']} a {period['fim']} | {series['filters']['uf']} | "
        f"{series['filters']['classificacao_final']}",
    )
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    axes.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(points) // 12)))

    _footer(
        figure,
        "Serie por data de inicio de sintomas, ja descontado o periodo sujeito a "
        "atraso de notificacao.",
    )
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(target, bbox_inches="tight")
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

    labels = [point["mes"] for point in points]
    values = [point["casos"] for point in points]
    colors = [_PARTIAL_COLOR if point.get("parcial") else _BAR_COLOR for point in points]

    figure, axes = plt.subplots(figsize=_FIGSIZE, dpi=_DPI)
    bars = axes.bar(labels, values, color=colors, width=0.68)

    for bar, point in zip(bars, points, strict=True):
        axes.annotate(
            _thousands(point["casos"], 0),
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center",
            va="bottom",
            fontsize=7.5,
            color="#404040",
        )

    period = series["period"]
    _style_axes(
        axes,
        f"Numero mensal de casos de SRAG - ultimos {len(points)} meses",
        "Casos por mes dos primeiros sintomas",
        f"{period['inicio']} a {period['fim']} | {series['filters']['uf']} | "
        f"{series['filters']['classificacao_final']}",
    )

    if any(point.get("parcial") for point in points):
        axes.bar(0, 0, color=_PARTIAL_COLOR, label="mes parcial (janela incompleta)")
        axes.legend(frameon=False, fontsize=8, loc="upper left")

    _footer(
        figure,
        "Serie por data de inicio de sintomas. Meses em tom claro estao incompletos.",
    )
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(target, bbox_inches="tight")
    plt.close(figure)

    logger.info("grafico gerado", extra={"arquivo": str(target), "pontos": len(points)})
    return target


def _parse_iso_date(value: str):
    from datetime import date

    return date.fromisoformat(value)
