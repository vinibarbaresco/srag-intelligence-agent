"""Leituras programaticas das series temporais (casos diarios e mensais).

Cada funcao aqui devolve fatos **calculados** sobre a serie -- pico, ultimo
valor, media movel, direcao da tendencia -- para alimentar tanto o headline
executivo do grafico quanto o bloco "Principais leituras". Nada aqui e redigido
por um modelo de linguagem: sao apenas estatisticas simples sobre os mesmos
pontos que o grafico desenha, o que garante que o texto nunca diverge do
desenho.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Abaixo desta variacao percentual entre as duas metades da janela, o quadro
#: e lido como estavel em vez de alta/queda -- evita rotular ruido de poucos
#: casos como tendencia.
_STABLE_BAND_PCT = 8.0

_MONTH_LABELS = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)


def _format_date_br(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}/{month}/{year[2:]}"


def _format_month_label(iso_month: str) -> str:
    year, month = iso_month.split("-")
    return f"{_MONTH_LABELS[int(month) - 1]}/{year[2:]}"


def _moving_average(values: list[int], window: int) -> list[float | None]:
    """Media movel de janela `window`, com janela expansiva no inicio da serie."""
    result: list[float | None] = []
    for index in range(len(values)):
        start = max(0, index - window + 1)
        chunk = values[start : index + 1]
        result.append(round(sum(chunk) / len(chunk), 2) if chunk else None)
    return result


def _trend_label(current_avg: float, previous_avg: float) -> tuple[str, float]:
    """Classifica a variacao entre duas medias em alta/queda/estavel.

    Returns:
        Tupla (rotulo, variacao percentual). Variacao e `0.0` quando a media
        anterior e zero (evita divisao por zero); o rotulo usa apenas o sinal
        da diferenca absoluta nesse caso.
    """
    if previous_avg == 0:
        if current_avg == 0:
            return "estavel", 0.0
        return "alta", 100.0
    delta_pct = (current_avg - previous_avg) / previous_avg * 100
    if abs(delta_pct) < _STABLE_BAND_PCT:
        return "estavel", delta_pct
    return ("alta" if delta_pct > 0 else "queda"), delta_pct


@dataclass(slots=True)
class DailyInsights:
    """Leituras programaticas da serie diaria de casos."""

    dates: list[str]
    values: list[int]
    moving_average: list[float | None]
    peak_date: str | None
    peak_value: int
    last_date: str | None
    last_value: int
    last_week_avg: float
    previous_week_avg: float
    trend: str
    trend_pct: float
    headline: str
    bullets: list[str] = field(default_factory=list)


def daily_insights(series: dict[str, Any]) -> DailyInsights:
    """Calcula pico, ultimo valor, media movel e tendencia da serie diaria."""
    points = series.get("points") or []
    dates = [point["data"] for point in points]
    values = [int(point["casos"]) for point in points]

    if not values:
        return DailyInsights([], [], [], None, 0, None, 0, 0.0, 0.0, "estavel", 0.0, "", [])

    moving_average = _moving_average(values, 7)

    peak_index = max(range(len(values)), key=lambda i: values[i])
    last_index = len(values) - 1

    last_week = values[-7:]
    previous_week = values[-14:-7] if len(values) >= 14 else []
    last_week_avg = round(sum(last_week) / len(last_week), 1) if last_week else 0.0
    previous_week_avg = round(sum(previous_week) / len(previous_week), 1) if previous_week else 0.0

    trend, trend_pct = (
        _trend_label(last_week_avg, previous_week_avg) if previous_week else ("estavel", 0.0)
    )

    peak_date_br = _format_date_br(dates[peak_index])
    last_date_br = _format_date_br(dates[last_index])

    if trend == "alta":
        headline = (
            f"Casos diários em alta: média dos últimos 7 dias sobe "
            f"{abs(trend_pct):.0f}% frente à semana anterior"
        )
    elif trend == "queda":
        headline = (
            f"Casos diários em queda: média dos últimos 7 dias recua "
            f"{abs(trend_pct):.0f}% frente à semana anterior"
        )
    else:
        headline = "Casos diários estáveis nas duas últimas semanas"

    bullets = [
        f"Pico de {values[peak_index]} caso(s) em {peak_date_br}.",
        f"Último dia analisável ({last_date_br}): {values[last_index]} caso(s); "
        f"média dos últimos 7 dias em {last_week_avg}.",
    ]
    if previous_week:
        bullets.append(
            f"Média dos 7 dias anteriores: {previous_week_avg} — variação de "
            f"{trend_pct:+.0f}% na comparação semanal."
        )

    return DailyInsights(
        dates=dates,
        values=values,
        moving_average=moving_average,
        peak_date=dates[peak_index],
        peak_value=values[peak_index],
        last_date=dates[last_index],
        last_value=values[last_index],
        last_week_avg=last_week_avg,
        previous_week_avg=previous_week_avg,
        trend=trend,
        trend_pct=trend_pct,
        headline=headline,
        bullets=bullets,
    )


@dataclass(slots=True)
class MonthlyInsights:
    """Leituras programaticas da serie mensal de casos."""

    months: list[str]
    values: list[int]
    partial_flags: list[bool]
    peak_month: str | None
    peak_value: int
    trend: str
    trend_pct: float
    headline: str
    bullets: list[str] = field(default_factory=list)


def monthly_insights(series: dict[str, Any]) -> MonthlyInsights:
    """Calcula pico, sazonalidade aproximada e tendencia da serie mensal."""
    points = series.get("points") or []
    months = [point["mes"] for point in points]
    values = [int(point["casos"]) for point in points]
    partial_flags = [bool(point.get("parcial")) for point in points]

    if not values:
        return MonthlyInsights([], [], [], None, 0, "estavel", 0.0, "", [])

    # O pico ignora o mes parcial quando ha alternativa: um mes ainda em
    # andamento nunca deveria ser lido como "o mes de maior volume do ano".
    complete_indices = [i for i, partial in enumerate(partial_flags) if not partial] or list(
        range(len(values))
    )
    peak_index = max(complete_indices, key=lambda i: values[i])

    # Tendencia: compara a segunda metade da janela com a primeira, excluindo
    # o mes parcial de ambos os lados (janela ainda incompleta nao deveria
    # empurrar a leitura de tendencia para baixo por si so).
    complete_values = [values[i] for i in complete_indices]
    half = len(complete_values) // 2
    if half >= 2:
        first_half_avg = sum(complete_values[:half]) / half
        second_half_avg = sum(complete_values[half : half * 2]) / half
        trend, trend_pct = _trend_label(second_half_avg, first_half_avg)
    else:
        trend, trend_pct = "estavel", 0.0

    peak_label = _format_month_label(months[peak_index])

    if trend == "alta":
        headline = f"Casos mensais em trajetória de alta no último ano, com pico em {peak_label}"
    elif trend == "queda":
        headline = f"Casos mensais em trajetória de queda no último ano, após pico em {peak_label}"
    else:
        headline = f"Casos mensais sem tendência definida no último ano, com pico em {peak_label}"

    bullets = [
        f"Pico de {values[peak_index]} casos em {peak_label}"
        + (" (mês parcial)" if partial_flags[peak_index] else "")
        + ".",
        f"Total de {sum(values)} casos nos {len(values)} meses exibidos.",
    ]
    if partial_flags and partial_flags[-1]:
        bullets.append(
            f"O último mês ({_format_month_label(months[-1])}) está parcial: a janela "
            "analisável termina antes do fim do mês."
        )

    return MonthlyInsights(
        months=months,
        values=values,
        partial_flags=partial_flags,
        peak_month=months[peak_index],
        peak_value=values[peak_index],
        trend=trend,
        trend_pct=trend_pct,
        headline=headline,
        bullets=bullets,
    )


def format_month_label(iso_month: str) -> str:
    """Ponto de entrada publico de :func:`_format_month_label`, para reuso externo."""
    return _format_month_label(iso_month)


def format_date_br(iso: str) -> str:
    """Ponto de entrada publico de :func:`_format_date_br`, para reuso externo."""
    return _format_date_br(iso)
