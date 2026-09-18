"""Dashboard executivo em HTML (cartoes de KPI, comparador, resumo executivo).

Movido de `report.py`. `render_html` e a fachada publica desta camada; o resto
sao helpers privados de composicao de marcacao.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import (
    DATASUS_DATASET_URL,
    DATASUS_SOURCE_LABEL,
    DELIVERY_LABEL,
    get_settings,
)
from src.guardrails.policies import DISCLAIMER

from .formatting import _analysis_cutoff, _load_quality_report, _pt_int, _pt_number
from .markdown_to_html import _inline, _markdown_to_html, _safe_url
from .theme import _html_document

#: Especificacao das quatro tools de KPI exigidas, na ordem de exibicao.
#: `worse_when` diz que direcao de variacao e desfavoravel -- usado so para
#: colorir a seta de tendencia (fato numerico), nunca para redigir opiniao.
_KPI_SPECS: tuple[dict[str, str], ...] = (
    {
        "key": "case_growth_rate",
        "label": "Taxa de aumento de casos",
        "worse_when": "up",
        "note": "Variação frente à janela anterior de mesmo tamanho.",
    },
    {
        "key": "mortality_rate",
        "label": "Letalidade (casos encerrados)",
        "worse_when": "up",
        "note": "Letalidade entre casos encerrados (óbito ou cura já definidos).",
    },
    {
        "key": "icu_admission_rate",
        "label": "Indicador de UTI",
        "worse_when": "up",
        "note": (
            "Admissão em UTI entre hospitalizados — mede severidade, não ocupação. "
            "A ocupação de leitos é o indicador 3b, com denominador do CNES."
        ),
    },
    {
        "key": "vaccination_coverage_among_cases",
        "label": "Indicador de vacinação",
        "worse_when": "down",
        "note": "Cobertura declarada entre casos notificados — não é cobertura da população.",
    },
)

#: Indicadores de contexto: respondem "isto e normal para esta epoca?", que os
#: quatro exigidos nao respondem sozinhos. Entram numa faixa secundaria, menor,
#: porque a hierarquia importa -- eles qualificam os KPIs, nao competem com eles.
_CONTEXT_SPECS: tuple[dict[str, str], ...] = (
    {
        "key": "icu_bed_occupancy_rate",
        "label": "Ocupação de leitos de UTI",
        "worse_when": "up",
        "note": (
            "PISO da ocupação real: mede só pacientes de SRAG sobre a capacidade do "
            "CNES, em janela deslocada para trás (não coincide com a dos demais "
            "indicadores). Valor baixo NÃO indica rede com folga."
        ),
    },
    {
        "key": "seasonal_excess",
        "label": "Excesso sobre o padrão sazonal",
        "worse_when": "up",
        "note": "Janela atual contra a mediana da mesma época nos anos de referência.",
    },
    {
        "key": "incidence_rate",
        "label": "Incidência por 100 mil hab.",
        "worse_when": "up",
        "note": "Casos notificados sobre a população residente (IBGE), no período.",
    },
)


#: Como ler numerador/denominador de cada indicador, em linguagem de negocio.
#: Repetir "ultimos 30 dias" no card seria redundante -- o periodo ja esta no
#: cabecalho; o que falta no card e a base sobre a qual a taxa foi calculada.
_KPI_BASE_LABELS: dict[str, tuple[str, str]] = {
    "mortality_rate": ("óbitos", "casos encerrados"),
    "icu_admission_rate": ("em UTI", "internados com UTI informado"),
    "vaccination_coverage_among_cases": ("vacinados", "casos com informação vacinal"),
    "incidence_rate": ("casos", "habitantes"),
    "icu_bed_occupancy_rate": ("pacientes de SRAG", "leitos de UTI existentes (CNES)"),
}


def _trend_arrow(value: float, *, worse_when_up: bool) -> tuple[str, str]:
    """Seta de tendencia e a classe CSS que a colore -- fato numerico, nao opiniao."""
    if abs(value) < 1e-9:
        return "→", "trend-neutral"
    up = value > 0
    arrow = "▲" if up else "▼"
    concerning = up == worse_when_up
    return arrow, ("trend-bad" if concerning else "trend-good")


def _kpi_comparison(spec: dict[str, str], metric: dict[str, Any], state: dict[str, Any]) -> str:
    """Linha de comparacao do card: variacao entre execucoes, comparacao propria
    do indicador ou, na falta das duas, a base (numerador/denominador) da taxa.
    """
    history = ((state.get("alerts") or {}).get("historico")) or {}
    variation = (history.get("variacao") or {}).get(spec["key"]) or {}
    delta = variation.get("variacao")
    if history.get("execucao_anterior") and delta is not None and abs(delta) > 1e-9:
        arrow, css_class = _trend_arrow(delta, worse_when_up=spec["worse_when"] == "up")
        return (
            f'<span class="kpi-trend {css_class}">{arrow} {_pt_number(abs(delta))} p.p.</span> '
            "desde a execução anterior"
        )

    components = metric.get("components") or {}
    if spec["key"] == "case_growth_rate":
        atual, anterior = (
            components.get("casos_periodo_atual"),
            components.get("casos_periodo_anterior"),
        )
        if atual is not None and anterior is not None:
            value = metric.get("value") or 0
            arrow, css_class = _trend_arrow(value, worse_when_up=True)
            return (
                f'<span class="kpi-trend {css_class}">{arrow}</span> '
                f"{_pt_int(atual)} vs {_pt_int(anterior)} casos na janela anterior"
            )

    if spec["key"] == "seasonal_excess":
        atual, mediana = (
            components.get("casos_na_janela_atual"),
            components.get("mediana_do_baseline"),
        )
        if atual is not None and mediana is not None:
            return f"{_pt_int(atual)} casos vs mediana de {_pt_int(mediana)} no baseline"

    numerator, denominator = metric.get("numerator"), metric.get("denominator")
    labels = _KPI_BASE_LABELS.get(spec["key"])
    if labels and numerator is not None and denominator is not None:
        return f"{_pt_int(numerator)} {labels[0]} em {_pt_int(denominator)} {labels[1]}"

    period = metric.get("period") or {}
    return html.escape(str(period.get("descricao", "Período analisado")))


def _kpi_card_html(spec: dict[str, str], state: dict[str, Any], *, compact: bool = False) -> str:
    """Card de indicador. `compact` e a variante secundaria (contexto), menor,
    para que a hierarquia entre os quatro exigidos e os dois de apoio seja
    visivel sem precisar ler o rotulo.
    """
    metric = (state.get("metrics") or {}).get(spec["key"])
    label = html.escape(spec["label"])
    note = html.escape(spec["note"])
    card_class = "kpi-card kpi-compact" if compact else "kpi-card"

    if metric is None:
        return (
            f'<div class="{card_class} kpi-empty">'
            f'<div class="kpi-label">{label}</div>'
            '<div class="kpi-value kpi-muted">n/d</div>'
            '<div class="kpi-comparison">Indicador não executado nesta rodada.</div>'
            f'<div class="kpi-note">{note}</div></div>'
        )

    value = metric.get("value")
    if value is None:
        reason = html.escape(str(metric.get("unavailable_reason") or "Não calculável."))
        return (
            f'<div class="{card_class} kpi-empty">'
            f'<div class="kpi-label">{label}</div>'
            '<div class="kpi-value kpi-muted">não calculável</div>'
            f'<div class="kpi-comparison">{reason}</div>'
            f'<div class="kpi-note">{note}</div></div>'
        )

    # Unidade longa (ex.: "por 100 mil hab. no periodo") no mesmo corpo do
    # numero quebrava o valor em duas linhas e matava a leitura de relance:
    # o numero fica grande, a unidade vira sufixo discreto.
    unit = metric.get("unit", "")
    if unit == "%":
        value_text = f"{_pt_number(value)}%"
    elif unit:
        value_text = f'{_pt_number(value)}<span class="kpi-unit">{html.escape(unit)}</span>'
    else:
        value_text = _pt_number(value)
    comparison = _kpi_comparison(spec, metric, state)
    warning = metric.get("reliability_warning")
    warning_html = (
        f'<div class="kpi-warning">⚠ {html.escape(str(warning))}</div>' if warning else ""
    )

    return (
        f'<div class="{card_class}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value_text}</div>'
        f'<div class="kpi-comparison">{comparison}</div>'
        f'<div class="kpi-note">{note}</div>'
        f"{warning_html}"
        "</div>"
    )


#: Os seis indicadores na tabela comparativa: os quatro exigidos primeiro, os
#: dois complementares depois. Sai das mesmas tuplas que alimentam os cartoes,
#: entao um indicador novo aparece nos dois lugares de uma vez.
_COMPARATOR_SPECS: tuple[tuple[dict[str, str], str], ...] = tuple(
    [(spec, "exigido") for spec in _KPI_SPECS] + [(spec, "complementar") for spec in _CONTEXT_SPECS]
)

#: Colunas de contagem da tabela: campo do envelope, rotulo e titulo longo.
#: Sao as tres unicas com barra embutida -- todas na mesma unidade (registros),
#: entao comparar o comprimento delas entre linhas significa alguma coisa. A
#: coluna "Valor" nao tem barra de proposito: ali convivem porcentagem e taxa
#: por 100 mil habitantes, e uma barra que misturasse as duas mentiria.
_COUNT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("numerator", "Numerador", "Casos que entram no numerador do indicador"),
    ("denominator", "Denominador", "Base elegivel contra a qual o numerador e comparado"),
    ("records_used", "Registros na base", "Registros lidos para calcular o indicador"),
)


def _comparator_value_html(metric: dict[str, Any]) -> str:
    value = metric.get("value")
    if value is None:
        return '<span class="cmp-null">não calculável</span>'
    unit = str(metric.get("unit", ""))
    if unit == "%":
        return f"{_pt_number(value)}%"
    if unit:
        return f'{_pt_number(value)}<span class="cmp-unit">{html.escape(unit)}</span>'
    return _pt_number(value)


def _comparator_detail_html(metric: dict[str, Any], note: str) -> str:
    period = metric.get("period") or {}
    limitations = metric.get("limitations") or []
    limitations_html = (
        "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in limitations) + "</ul>"
        if limitations
        else '<p class="cmp-null">Nenhuma limitação declarada.</p>'
    )
    warning = metric.get("reliability_warning")
    warning_html = f'<p class="cmp-warning">⚠ {html.escape(str(warning))}</p>' if warning else ""
    unavailable = metric.get("unavailable_reason")
    unavailable_html = (
        f'<p class="cmp-warning">Indisponível: {html.escape(str(unavailable))}</p>'
        if unavailable
        else ""
    )
    return (
        '<div class="cmp-detail-grid">'
        f'<div><span class="cmp-detail-key">Definição</span>'
        f"<p>{html.escape(str(metric.get('definition', '-')))}</p></div>"
        f'<div><span class="cmp-detail-key">Período</span>'
        f"<p>{html.escape(str(period.get('inicio', '-')))} a "
        f"{html.escape(str(period.get('fim', '-')))} — "
        f"{html.escape(str(period.get('descricao', '')))}</p></div>"
        f'<div><span class="cmp-detail-key">Fonte</span>'
        f"<p>{html.escape(str(metric.get('source', '-')))}</p></div>"
        f'<div><span class="cmp-detail-key">Leitura</span><p>{html.escape(note)}</p></div>'
        f'<div class="cmp-detail-wide"><span class="cmp-detail-key">Limitações declaradas</span>'
        f"{limitations_html}{warning_html}{unavailable_html}</div>"
        "</div>"
    )


def _indicator_table_html(state: dict[str, Any]) -> str:
    """Os sete indicadores lado a lado, ordenavel, com o envelope de cada um.

    Os cartoes acima respondem "como esta cada indicador"; esta tabela responde
    "de que tamanho e a base de cada um" -- que e o que separa um numero solido
    de um numero calculado sobre poucos registros. Numerador, denominador e
    registros lidos ja existiam no anexo tecnico, um indicador por vez; aqui
    ficam na mesma tela, comparaveis, e cada linha abre o resto do envelope
    (definicao, periodo, fonte, limitacoes declaradas) sem sair da pagina.
    """
    metrics = state.get("metrics") or {}
    rows: list[dict[str, Any]] = []
    for spec, kind in _COMPARATOR_SPECS:
        metric = metrics.get(spec["key"])
        if metric is None:
            continue
        rows.append({"spec": spec, "kind": kind, "metric": metric})

    if not rows:
        return ""

    # Escala das barras: o maior valor absoluto de cada coluna vira 100%.
    scales: dict[str, float] = {}
    for field, _label, _title in _COUNT_COLUMNS:
        values = [
            abs(float(row["metric"][field]))
            for row in rows
            if isinstance(row["metric"].get(field), int | float)
        ]
        scales[field] = max(values) if values else 0.0

    body: list[str] = []
    for index, row in enumerate(rows):
        metric, spec = row["metric"], row["spec"]
        value = metric.get("value")
        status = "calculado" if value is not None else "indisponível"
        status_class = "cmp-tag-ok" if value is not None else "cmp-tag-off"

        cells = [
            f'<td class="cmp-name" data-sort="{html.escape(spec["label"])}">'
            f'<span class="cmp-dot cmp-dot-{index}"></span>'
            f'<span class="cmp-name-text">{html.escape(spec["label"])}'
            f"<small>{row['kind']}</small></span></td>",
            f'<td class="cmp-num cmp-value" data-sort="{"" if value is None else value}">'
            f"{_comparator_value_html(metric)}</td>",
        ]
        for field, _label, _title in _COUNT_COLUMNS:
            raw = metric.get(field)
            if isinstance(raw, int | float):
                width = abs(float(raw)) / scales[field] * 100 if scales[field] else 0
                bar = f'<span class="cmp-bar"><i style="width:{width:.1f}%"></i></span>'
                cells.append(f'<td class="cmp-num" data-sort="{raw}">{_pt_int(raw)}{bar}</td>')
            else:
                cells.append(
                    '<td class="cmp-num" data-sort=""><span class="cmp-null">-</span></td>'
                )
        cells.append(
            f'<td data-sort="{status}"><span class="cmp-tag {status_class}">{status}</span></td>'
        )

        key = html.escape(spec["key"])
        body.append(
            f'<tr class="cmp-row" data-key="{key}" tabindex="0" aria-expanded="false">'
            + "".join(cells)
            + "</tr>"
            f'<tr class="cmp-detail" data-detail-for="{key}" hidden>'
            f'<td colspan="{2 + len(_COUNT_COLUMNS) + 1}">'
            f"{_comparator_detail_html(metric, spec['note'])}</td></tr>"
        )

    headers = [
        '<th class="cmp-sortable" data-type="text" aria-sort="none">'
        '<button type="button">Indicador</button></th>',
        '<th class="cmp-sortable cmp-num" data-type="number" aria-sort="none">'
        '<button type="button">Valor</button></th>',
    ]
    headers += [
        f'<th class="cmp-sortable cmp-num" data-type="number" aria-sort="none" '
        f'title="{html.escape(title)}"><button type="button">{html.escape(label)}</button></th>'
        for _field, label, title in _COUNT_COLUMNS
    ]
    headers.append(
        '<th class="cmp-sortable" data-type="text" aria-sort="none">'
        '<button type="button">Status</button></th>'
    )

    return f"""
<section class="block" id="comparador">
  <div class="panel-head">
    <p class="eyebrow">Comparação</p>
    <h2 class="block-headline">Os {len(rows)} indicadores lado a lado</h2>
    <p class="news-intro cmp-hint">Clique num cabeçalho para ordenar; clique numa linha para abrir a
    definição, o período, a fonte e as limitações declaradas daquele indicador. As barras
    comparam contagens de registros entre as linhas — a coluna Valor não tem barra porque
    mistura porcentagem e taxa por 100 mil habitantes.</p>
  </div>
  <div class="cmp-wrap">
    <table>
      <thead><tr>{"".join(headers)}</tr></thead>
      <tbody>{"".join(body)}</tbody>
    </table>
  </div>
</section>
"""


#: Vocabulario do veredito de alerta: rotulo legivel e classe CSS por nivel.
_ALERT_LEVELS: dict[str, tuple[str, str]] = {
    "normal": ("Situação dentro dos limiares", "status-normal"),
    "atencao": ("Atenção", "status-warn"),
    "alerta": ("Alerta", "status-bad"),
}


def _status_strip_html(state: dict[str, Any]) -> str:
    """Faixa de status: o "e agora?" do relatorio.

    O veredito das regras de alerta e DADO -- limiar configurado aplicado a um
    indicador ja calculado --, nao interpretacao. Ficava so no anexo tecnico, o
    que enterrava justamente a informacao que decide se alguem precisa agir.
    """
    alerts = state.get("alerts") or {}
    if not alerts:
        return ""

    level = str(alerts.get("nivel", "normal")).lower()
    label, css_class = _ALERT_LEVELS.get(level, (level.upper(), "status-warn"))
    summary = html.escape(str(alerts.get("resumo", "")))

    triggered = alerts.get("disparados") or []
    attention = alerts.get("atencao") or []
    items = [
        f"<li>{html.escape(str(item.get('mensagem', '')))}</li>"
        for item in [*triggered, *attention]
        if item.get("mensagem")
    ]
    items_html = f'<ul class="status-list">{"".join(items[:3])}</ul>' if items else ""

    history = alerts.get("historico") or {}
    if history.get("execucao_anterior"):
        changed = [
            f"{key} {item['variacao']:+g}"
            for key, item in (history.get("variacao") or {}).items()
            if item.get("variacao")
        ]
        history_text = (
            f"Frente à execução anterior: {html.escape(', '.join(changed))}."
            if changed
            else "Sem variação frente à execução anterior do mesmo recorte."
        )
    else:
        history_text = html.escape(
            str(history.get("mensagem", "Primeira execução registrada para este recorte."))
        )

    return f"""
<section class="status-strip {css_class}">
  <div class="status-head">
    <span class="status-badge">{html.escape(label)}</span>
    <p class="status-summary">{summary}</p>
  </div>
  {items_html}
  <p class="status-history">{history_text}</p>
</section>
"""


def _executive_headline(state: dict[str, Any]) -> str:
    """Headline conclusivo do resumo executivo -- gerado a partir dos dados, nunca fixo."""
    metrics = state.get("metrics") or {}
    growth = metrics.get("case_growth_rate") or {}
    mortality = metrics.get("mortality_rate") or {}
    alerts = state.get("alerts") or {}
    level = str(alerts.get("nivel", "normal")).lower()

    growth_value = growth.get("value")
    if growth_value is None:
        growth_phrase = "sem base suficiente para medir a variação de casos"
    elif growth_value > 8:
        growth_phrase = f"casos avançam {_pt_number(growth_value)}% em relação à janela anterior"
    elif growth_value < -8:
        growth_phrase = (
            f"casos recuam {_pt_number(abs(growth_value))}% em relação à janela anterior"
        )
    else:
        growth_phrase = "casos seguem estáveis em relação à janela anterior"

    mortality_value = mortality.get("value")
    mortality_phrase = (
        "letalidade não calculável no recorte"
        if mortality_value is None
        else f"letalidade em {_pt_number(mortality_value)}% entre os casos encerrados"
    )

    headline = f"{growth_phrase[0].upper()}{growth_phrase[1:]}, enquanto {mortality_phrase}"
    if level != "normal":
        headline += f" — nível de acompanhamento {level.upper()}"
    return headline + "."


def _executive_bullets(state: dict[str, Any]) -> list[tuple[str, str]]:
    """2 a 4 insights do resumo executivo, cada um rotulado DADO ou CONTEXTO."""
    metrics = state.get("metrics") or {}
    bullets: list[tuple[str, str]] = []

    growth = metrics.get("case_growth_rate") or {}
    if growth.get("value") is not None:
        components = growth.get("components") or {}
        bullets.append(
            (
                "DADO",
                f"Casos: {_pt_number(growth['value'])}% frente à janela anterior "
                f"({_pt_int(components.get('casos_periodo_atual'))} vs "
                f"{_pt_int(components.get('casos_periodo_anterior'))} casos).",
            )
        )

    mortality = metrics.get("mortality_rate") or {}
    if mortality.get("value") is not None:
        bullets.append(
            (
                "DADO",
                f"Letalidade entre casos encerrados: {_pt_number(mortality['value'])}% "
                f"({_pt_int(mortality.get('numerator'))} óbitos em "
                f"{_pt_int(mortality.get('denominator'))} casos encerrados).",
            )
        )

    icu = metrics.get("icu_admission_rate") or {}
    if icu.get("value") is not None:
        bullets.append(
            (
                "DADO",
                f"UTI: {_pt_number(icu['value'])}% dos hospitalizados foram admitidos em UTI "
                "(não equivale a ocupação de leitos).",
            )
        )

    occupancy = metrics.get("icu_bed_occupancy_rate") or {}
    if occupancy.get("value") is not None:
        bullets.append(
            (
                "DADO",
                f"Ocupação de UTI: {_pt_number(occupancy['value'])}% da capacidade do CNES "
                "ocupada por pacientes de SRAG — é um PISO da ocupação real (outros "
                "quadros também usam esses leitos); valor baixo não indica rede com folga.",
            )
        )

    vaccination = metrics.get("vaccination_coverage_among_cases") or {}
    if vaccination.get("value") is not None:
        bullets.append(
            (
                "DADO",
                f"Vacinação: {_pt_number(vaccination['value'])}% de cobertura declarada entre "
                "os casos notificados (covid-19).",
            )
        )

    articles = ((state.get("external_context") or {}).get("articles")) or []
    if articles:
        top = articles[0]
        bullets.append(
            (
                "CONTEXTO",
                f"{len(articles)} notícia(s) recente(s) monitorada(s); destaque: "
                f'"{top["titulo"][:90]}" ({top["fonte"]}).',
            )
        )

    # O teto era 3; a ocupacao de UTI entrou como quinto bullet de DADO e nao
    # pode deslocar nenhum dos quatro indicadores exigidos pelo contrato para
    # fora do resumo executivo -- por isso o teto subiu junto.
    dado_bullets = [b for b in bullets if b[0] == "DADO"][:5]
    contexto_bullets = [b for b in bullets if b[0] == "CONTEXTO"][:1]
    return (dado_bullets + contexto_bullets)[:6]


def _daily_chart_section(state: dict[str, Any]) -> str:
    from src.visualization.interactive_charts import daily_chart_component
    from src.visualization.series_insights import daily_insights

    series = (state.get("series") or {}).get("daily_cases")
    chart = (state.get("charts") or {}).get("casos_diarios")
    if not series or not chart:
        return ""

    insights = daily_insights(series)
    bullets_html = "".join(f"<li>{html.escape(b)}</li>" for b in insights.bullets)
    image_source = _chart_image_source(chart)

    return f"""
<section class="block" id="series">
  <div class="panel-head">
    <p class="eyebrow">Evolução recente — últimos {len(series.get("points") or [])} dias</p>
    <h2 class="block-headline">{html.escape(insights.headline)}</h2>
  </div>
  <div class="chart-wrap">
    {daily_chart_component(series)}
    <img class="chart-print-only" src="{html.escape(image_source)}"
         alt="{html.escape(insights.headline)}">
  </div>
  <div class="insights-box">
    <p class="insights-title">Principais leituras</p>
    <ul>{bullets_html}</ul>
  </div>
</section>
"""


def _monthly_chart_section(state: dict[str, Any]) -> str:
    from src.visualization.interactive_charts import monthly_chart_component
    from src.visualization.series_insights import monthly_insights

    series = (state.get("series") or {}).get("monthly_cases")
    chart = (state.get("charts") or {}).get("casos_mensais")
    if not series or not chart:
        return ""

    insights = monthly_insights(series)
    bullets_html = "".join(f"<li>{html.escape(b)}</li>" for b in insights.bullets)
    image_source = _chart_image_source(chart)

    return f"""
<section class="block" id="series-mensal">
  <div class="panel-head">
    <p class="eyebrow">Visão estrutural — últimos {len(series.get("points") or [])} meses</p>
    <h2 class="block-headline">{html.escape(insights.headline)}</h2>
  </div>
  <div class="chart-wrap">
    {monthly_chart_component(series)}
    <img class="chart-print-only" src="{html.escape(image_source)}"
         alt="{html.escape(insights.headline)}">
  </div>
  <div class="insights-box">
    <p class="insights-title">Principais leituras</p>
    <ul>{bullets_html}</ul>
  </div>
</section>
"""


def _chart_image_source(chart: dict[str, Any]) -> str:
    settings = get_settings()
    path = Path(chart["path"])
    try:
        relative = path.resolve().relative_to(settings.outputs_dir.resolve())
        return (Path("..") / relative).as_posix()
    except ValueError:
        return path.as_posix()


def _news_cards_html(state: dict[str, Any]) -> str:
    context = state.get("external_context") or {}
    articles = (context.get("articles") or [])[:5]

    if not articles:
        reason = html.escape(str(context.get("unavailable_reason") or "Sem notícias nesta rodada."))
        return f'<p class="news-empty">{reason}</p>'

    cards = []
    for article in articles:
        safe_url = _safe_url(article["url"])
        title = html.escape(article["titulo"])
        source = html.escape(article["fonte"])
        published = html.escape(article.get("data", ""))
        link = (
            f'<a class="news-link" href="{html.escape(safe_url)}" '
            'target="_blank" rel="noopener noreferrer">Ler notícia ↗</a>'
            if safe_url
            else '<span class="news-link news-link-disabled">Link indisponível</span>'
        )
        cards.append(
            '<article class="news-card">'
            f'<p class="news-title">{title}</p>'
            f'<p class="news-meta">{source} · {published}</p>'
            f"{link}"
            "</article>"
        )
    return "".join(cards)


def _population_component(components: dict[str, Any], campaign: str) -> dict[str, Any]:
    population = components.get("taxa_de_vacinacao_da_populacao") or {}
    campaigns = population.get("campanhas") or {}
    return campaigns.get(campaign) or population


def _limitation_callouts_html(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") or {}
    occupancy = metrics.get("icu_bed_occupancy_rate") or {}
    vaccination = metrics.get("vaccination_coverage_among_cases") or {}

    # Bug corrigido: este callout buscava `unavailable_reason` dentro do
    # componente `taxa_de_ocupacao_de_leitos_de_uti` de `icu_admission_rate`,
    # que so tem `indicador`/`nota` (nunca `unavailable_reason` -- ver
    # `src/metrics/epidemiology.py`). O lookup sempre falhava e caia no texto
    # fixo abaixo, que afirma a ocupacao ser incalculavel mesmo quando o
    # indicador `icu_bed_occupancy_rate` foi de fato calculado alhures no
    # mesmo relatorio. Agora le o proprio indicador de ocupacao.
    if occupancy.get("value") is not None:
        icu_reason = (
            "A ocupação de leitos de UTI por pacientes de SRAG É calculada nesta "
            "execução (ver seção 3b/indicador de contexto), com denominador do CNES -- "
            "nunca a partir deste indicador de admissão."
        )
    else:
        icu_reason = occupancy.get("unavailable_reason") or (
            "Ocupação de leitos de UTI indisponível nesta execução: o SIVEP-Gripe não "
            "registra capacidade instalada, e nenhuma referência de leitos (CNES) "
            "compatível com este recorte foi carregada."
        )
    vaccination_reason = _population_component(vaccination.get("components") or {}, "covid19").get(
        "unavailable_reason"
    ) or (
        "O SIVEP-Gripe so contem informacao vacinal de pessoas notificadas com SRAG, o que "
        "nao representa a populacao geral."
    )

    callouts = [
        (
            "Nota metodológica — UTI",
            "Este indicador representa admissão em UTI entre pacientes de SRAG hospitalizados. "
            f"{icu_reason}",
        ),
        (
            "Nota metodológica — Vacinação",
            "Este indicador representa cobertura vacinal declarada entre casos notificados de "
            f"SRAG, não a cobertura vacinal da população. {vaccination_reason}",
        ),
    ]

    blocks = [
        '<div class="callout">'
        f'<p class="callout-title">ℹ {html.escape(title)}</p>'
        f"<p>{html.escape(text)}</p></div>"
        for title, text in callouts
    ]

    extra = [
        *(state.get("warnings") or []),
        *(f"Falha registrada: {e}" for e in state.get("errors") or []),
    ]
    if extra:
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in extra)
        blocks.append(
            '<div class="callout callout-neutral">'
            '<p class="callout-title">ℹ Avisos desta execução</p>'
            f"<ul>{items}</ul></div>"
        )
    return "".join(blocks)


def _interpretation_html(text: str) -> str:
    """Converte o texto da interpretacao (que pode trazer `### ` e `- ` do
    proprio estilo do redator) em HTML leve, reaproveitando `_inline` para
    negrito/italico -- sem isso, um `### 1. Panorama geral` aparecia como
    texto cru em vez de subtitulo.
    """
    blocks: list[str] = []
    list_open = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            if list_open:
                blocks.append("</ul>")
                list_open = False
            blocks.append(f"<h4>{_inline(heading.group(1))}</h4>")
            continue
        if stripped.startswith("- "):
            if not list_open:
                blocks.append("<ul>")
                list_open = True
            blocks.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if list_open:
            blocks.append("</ul>")
            list_open = False
        blocks.append(f"<p>{_inline(stripped)}</p>")
    if list_open:
        blocks.append("</ul>")
    return "".join(blocks)


def _methodology_html(state: dict[str, Any]) -> str:
    quality = _load_quality_report()
    rows = [
        ("Fonte dos dados", DATASUS_SOURCE_LABEL),
        ("Dataset", f'<a href="{DATASUS_DATASET_URL}">{DATASUS_DATASET_URL}</a>'),
        (
            "Registros processados na carga",
            _pt_int(quality.get("rows_read")) if quality else "n/d",
        ),
        (
            "Registros descartados",
            f"{quality.get('rows_dropped', 'n/d')} (nada é removido silenciosamente)"
            if quality
            else "n/d",
        ),
        ("Via de interpretação", str(state.get("interpretation_source", "n/d"))),
    ]
    rows_html = "".join(
        f'<tr><td class="meta-key">{html.escape(k)}</td><td>{v}</td></tr>' for k, v in rows
    )
    return f"""
<details class="methodology">
  <summary>Metodologia e limitações gerais</summary>
  <table>{rows_html}</table>
  <p>Definição completa de cada indicador (numerador, denominador, campos, tratamento de
  ausência e limitações), o contrato de colunas e o relatório de qualidade da carga estão no
  <a href="#anexo-tecnico">anexo técnico</a>, abaixo.</p>
</details>
"""


#: Secoes da barra de navegacao: ancora -> rotulo. A ordem e a da pagina.
_NAV_SECTIONS: tuple[tuple[str, str], ...] = (
    ("resumo", "Resumo"),
    ("indicadores", "Indicadores"),
    ("series", "Séries"),
    ("contexto", "Contexto externo"),
    ("interpretacao", "Interpretação"),
    ("transparencia", "Transparência"),
)


def _topbar_html(sections: tuple[tuple[str, str], ...] = _NAV_SECTIONS) -> str:
    """Barra fixa: marca, navegacao por secao e as acoes da pagina.

    A navegacao existe porque o relatorio e longo e tem uma ordem de leitura
    declarada (dado, depois contexto, depois inferencia). O botao de
    rastreabilidade abre o anexo tecnico -- que fica recolhido -- em vez de
    obrigar quem audita a procurar o `<details>` no fim da pagina.

    `sections` e vazio na pagina de recusa: um pedido bloqueado nao tem
    indicador nem serie, e um link de navegacao que nao leva a lugar nenhum e
    pior do que nenhuma navegacao.
    """
    links = "".join(
        f'<a class="nav-link" href="#{anchor}">{html.escape(label)}</a>'
        for anchor, label in sections
    )
    trace_button = '<a class="btn" href="#anexo-tecnico">Rastreabilidade</a>' if sections else ""
    return f"""
<nav class="topbar no-print">
  <div class="topbar-inner">
    <a class="brand" href="#topo">
      <span class="brand-mark">SRAG</span>
      <span class="brand-text">
        <b>SRAG Intelligence Agent</b>
        <small>Relatório epidemiológico</small>
      </span>
    </a>
    <div class="navlinks">{links}</div>
    <div class="topbar-actions">
      {trace_button}
      <button class="icon-btn" type="button" id="print-button"
              aria-label="Imprimir ou exportar em PDF">&#x2399;</button>
      <button class="icon-btn" type="button" id="theme-toggle"
              aria-label="Alternar tema">&#x263E;</button>
    </div>
  </div>
</nav>
"""


def _chip(key: str, value: str, css_class: str = "") -> str:
    classes = f"chip {css_class}".strip()
    return (
        f'<li class="{classes}"><span class="chip-key">{html.escape(key)}</span>'
        f"<b>{html.escape(value)}</b></li>"
    )


def _html_header(state: dict[str, Any]) -> str:
    """Capa: titulo e a procedencia da rodada em chips.

    Os metadados viram chips, e nao uma lista de definicoes, porque sao todos
    da mesma natureza -- de onde veio e ate quando vale este relatorio -- e
    porque cabem numa linha so, logo abaixo do titulo, em vez de empurrar o
    primeiro numero para baixo da dobra.
    """
    filters = (state.get("validation") or {}).get("filters") or {}
    generated = datetime.now(tz=UTC).astimezone().strftime("%d/%m/%Y %H:%M")
    cutoff = _analysis_cutoff(state) or "n/d"
    quality = _load_quality_report()
    updated = str(quality.get("generated_at", ""))[:16].replace("T", " ") or "n/d"
    scope = filters.get("uf") or "BR (nacional)"
    classification = filters.get("classificacao_final") or "todas as classificações"
    run_id = str(state.get("run_id", ""))
    source = str(state.get("interpretation_source", "")) or "n/d"

    chips = "".join(
        [
            _chip("run", run_id.split("-")[0] or "n/d", "chip-accent"),
            _chip("recorte", f"{scope} · {classification}"),
            _chip("corte analítico", cutoff),
            _chip("base atualizada", updated),
            _chip("gerado em", generated),
            _chip("via", source),
            _chip("PoC", "não é conduta clínica", "chip-warn"),
        ]
    )

    return f"""
<header class="report-header" id="topo">
  <p class="delivery-label">{html.escape(DELIVERY_LABEL)}</p>
  <p class="eyebrow">SRAG Intelligence Report · {html.escape(DATASUS_SOURCE_LABEL)}</p>
  <h1>Monitoramento de Síndrome Respiratória Aguda Grave</h1>
  <ul class="hero-chips">{chips}</ul>
</header>
"""


def _html_footer(state: dict[str, Any]) -> str:
    generated = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f"""
<footer class="report-footer">
  <p class="delivery-label">{html.escape(DELIVERY_LABEL)}</p>
  <p>Fonte: {html.escape(DATASUS_SOURCE_LABEL)} &middot;
  <a href="{DATASUS_DATASET_URL}">dataset</a> &middot; gerado em {html.escape(generated)}
  &middot; run_id {html.escape(str(state.get("run_id", "")))} &middot;
  SRAG Intelligence Agent — Prova de Conceito (PoC), Indicium HealthCare.</p>
  <p class="disclaimer">{html.escape(DISCLAIMER)}</p>
</footer>
"""


def _render_refusal_html(state: dict[str, Any]) -> str:
    validation = state.get("validation") or {}
    blocked_by = html.escape(str(validation.get("blocked_by")))
    reason = html.escape(
        str(validation.get("reason", "Solicitação recusada na validação de entrada."))
    )
    return f"""
{_topbar_html(sections=())}
<div class="report-shell">
  <header class="report-header" id="topo">
    <p class="delivery-label">{html.escape(DELIVERY_LABEL)}</p>
    <p class="eyebrow">SRAG Intelligence Report</p>
    <h1>Solicitação não processada</h1>
  </header>
  <div class="callout callout-bad">
    <p class="callout-title">Guardrail acionado: {blocked_by}</p>
    <p>{reason}</p>
  </div>
  {_html_footer(state)}
</div>
"""


def render_html(markdown_text: str, state: dict[str, Any]) -> str:
    """Gera o relatorio executivo em HTML: dashboard + narrativa + anexo tecnico.

    A pagina combina uma camada executiva construida diretamente do estado
    (headline calculado, KPIs, graficos interativos com leituras programaticas,
    contexto externo separado, interpretacao e notas metodologicas) com um
    anexo tecnico completo -- a mesma conversao Markdown->HTML de sempre,
    recolhido por padrao -- para quem quer o detalhamento indicador a
    indicador, a trilha de auditoria e o relatorio de qualidade dos dados.
    """
    title = f"SRAG Intelligence Report - {state.get('run_id', '')}"
    validation = state.get("validation") or {}

    if not validation.get("allowed", True):
        body = _render_refusal_html(state)
        return _html_document(title, body)

    appendix_body = _markdown_to_html(markdown_text)
    exec_bullets_html = "".join(
        f'<li><span class="tag tag-{tag.lower()}">{tag}</span><span>{html.escape(text)}</span></li>'
        for tag, text in _executive_bullets(state)
    )
    interpretation_source = html.escape(str(state.get("interpretation_source", "")))
    interpretation_text = (
        state.get("interpretation") or "Interpretação não disponível nesta execução."
    )
    interpretation_html = _interpretation_html(interpretation_text)
    body = f"""
{_topbar_html()}
<div class="report-shell">
  {_html_header(state)}

  <section class="block exec-summary" id="resumo">
    <p class="eyebrow">Resumo executivo</p>
    <h2 class="block-headline">{html.escape(_executive_headline(state))}</h2>
    <ul class="exec-list">
      {exec_bullets_html}
    </ul>
  </section>

  {_status_strip_html(state)}

  <section class="block block-bare" id="indicadores">
    <p class="eyebrow">Indicadores principais</p>
    <div class="kpi-grid">
      {"".join(_kpi_card_html(spec, state) for spec in _KPI_SPECS)}
    </div>
    <p class="context-label">Indicadores de contexto — situam os quatro acima no padrão
    histórico e no tamanho da população</p>
    <div class="kpi-grid kpi-grid-context">
      {"".join(_kpi_card_html(spec, state, compact=True) for spec in _CONTEXT_SPECS)}
    </div>
  </section>

  {_indicator_table_html(state)}

  {_daily_chart_section(state)}
  {_monthly_chart_section(state)}

  <section class="block" id="contexto">
    <div class="panel-head">
      <p class="eyebrow">Contexto externo</p>
      <h2 class="block-headline">Notícias recentes sobre SRAG</h2>
      <p class="news-intro">Evidência contextual (notícias) — nunca altera, corrige ou substitui os
      indicadores calculados sobre os dados oficiais do DATASUS (evidência epidemiológica).</p>
    </div>
    <div class="news-grid">{_news_cards_html(state)}</div>
  </section>

  <section class="block" id="interpretacao">
    <div class="panel-head">
      <p class="eyebrow">Interpretação</p>
      <h2 class="block-headline">Interpretação do cenário</h2>
    </div>
    <div class="interpretation">
      <p class="interpretation-note">Texto produzido por
      <code>{interpretation_source}</code> exclusivamente a
      partir dos resultados das tools acima, validado pelo guardrail de evidência.</p>
      {interpretation_html}
    </div>
  </section>

  <section class="block" id="transparencia">
    <div class="panel-head">
      <p class="eyebrow">Transparência</p>
      <h2 class="block-headline">Limitações e notas metodológicas</h2>
    </div>
    {_limitation_callouts_html(state)}
    {_methodology_html(state)}
  </section>

  <details class="block technical-appendix" id="anexo-tecnico">
    <summary>Anexo técnico completo (indicadores detalhados, séries, qualidade dos dados,
    governança e auditoria)</summary>
    <div class="technical-appendix-body">{appendix_body}</div>
  </details>

  {_html_footer(state)}
</div>
"""
    return _html_document(title, body)
