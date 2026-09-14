"""Renderizacao do relatorio epidemiologico.

O relatorio e organizado em tres rotulos que nunca se misturam:

* **DADO** -- valores calculados pelas tools sobre o DATASUS, com numerador,
  denominador, periodo e fonte;
* **INFERENCIA** -- a leitura do cenario produzida pela camada de interpretacao;
* **CONTEXTO EXTERNO** -- noticias, com titulo, fonte, data e URL.

Essa separacao e o que torna cada afirmacao rastreavel e impede que uma leitura
interpretativa seja lida como medicao.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import DATASUS_DATASET_URL, DATASUS_SOURCE_LABEL, get_settings
from src.guardrails.policies import DISCLAIMER, UNCERTAINTY_STATEMENT
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

_INDICATOR_TITLES: dict[str, str] = {
    "case_growth_rate": "1. Taxa de aumento de casos",
    "mortality_rate": "2. Taxa de mortalidade",
    "icu_admission_rate": "3. UTI (admissao e censo)",
    "vaccination_coverage_among_cases": "4. Cobertura vacinal",
}

_INDICATOR_ORDER: tuple[str, ...] = tuple(_INDICATOR_TITLES)


def render_markdown(state: dict[str, Any]) -> str:
    """Monta o relatorio completo em Markdown a partir do estado final."""
    if not (state.get("validation") or {}).get("allowed", True):
        return _render_refusal(state)

    parts = [
        _header(state),
        _summary_table(state),
        _indicators_section(state),
        _series_section(state),
        _charts_section(state),
        _data_quality_section(state),
        _interpretation_section(state),
        _news_section(state),
        _limitations_section(state),
        _governance_section(state),
    ]
    return "\n\n".join(part for part in parts if part)


def _header(state: dict[str, Any]) -> str:
    filters = (state.get("validation") or {}).get("filters") or {}
    generated = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return "\n".join(
        [
            "# Relatorio epidemiologico de SRAG",
            "",
            f"- **Execucao (run_id):** `{state.get('run_id')}`",
            f"- **Gerado em:** {generated}",
            f"- **Solicitacao:** {state.get('request')}",
            f"- **Recorte:** {filters.get('uf', 'BR (nacional)')} | "
            f"{filters.get('classificacao_final', 'todas as classificacoes finais')}",
            f"- **Fonte dos dados:** {DATASUS_SOURCE_LABEL} "
            f"([dataset]({DATASUS_DATASET_URL}))",
            f"- **Via de interpretacao:** `{state.get('interpretation_source')}`",
            "",
            f"> {DISCLAIMER}",
        ]
    )


def _format_value(metric: dict[str, Any]) -> str:
    if metric.get("value") is None:
        return "nao calculavel"
    unit = metric.get("unit", "")
    suffix = "%" if unit == "%" else f" {unit}" if unit else ""
    return f"{metric['value']}{suffix}"


def _summary_table(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") or {}
    lines = [
        "## Resumo dos indicadores",
        "",
        "| # | Indicador | Valor | Numerador | Denominador | Status |",
        "|---|-----------|-------|-----------|-------------|--------|",
    ]
    for index, key in enumerate(_INDICATOR_ORDER, start=1):
        metric = metrics.get(key)
        if metric is None:
            lines.append(f"| {index} | {key} | - | - | - | nao executado |")
            continue
        status = "calculado" if metric.get("value") is not None else "indisponivel"
        lines.append(
            f"| {index} | {metric['metric']} | {_format_value(metric)} | "
            f"{metric.get('numerator', '-')} | {metric.get('denominator', '-')} | {status} |"
        )
    return "\n".join(lines)


def _indicators_section(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") or {}
    blocks = ["## DADO - Indicadores epidemiologicos", ""]

    for key in _INDICATOR_ORDER:
        metric = metrics.get(key)
        if metric is None:
            continue

        period = metric.get("period", {})
        blocks.append(f"### {_INDICATOR_TITLES[key]}")
        blocks.append("")
        blocks.append(f"**Valor:** {_format_value(metric)}")
        blocks.append("")
        blocks.append(f"- **Definicao:** {metric.get('definition')}")
        blocks.append(f"- **Numerador:** {metric.get('numerator')}")
        blocks.append(f"- **Denominador:** {metric.get('denominator')}")
        blocks.append(
            f"- **Periodo:** {period.get('inicio')} a {period.get('fim')} "
            f"({period.get('descricao')})"
        )
        blocks.append(f"- **Fonte:** {metric.get('source')}")

        if metric.get("unavailable_reason"):
            blocks.append(f"- **Indisponibilidade:** {metric['unavailable_reason']}")
            blocks.append(f"- **Declaracao:** {UNCERTAINTY_STATEMENT}")

        blocks.extend(_components_block(key, metric.get("components") or {}))
        blocks.append("")
        blocks.append("<details><summary>Limitacoes declaradas</summary>")
        blocks.append("")
        blocks.extend(f"- {limitation}" for limitation in metric.get("limitations", []))
        blocks.append("")
        blocks.append("</details>")
        blocks.append("")

    return "\n".join(blocks)


def _components_block(key: str, components: dict[str, Any]) -> list[str]:
    """Detalha os componentes relevantes de cada indicador."""
    if not components:
        return []

    lines: list[str] = []

    if key == "case_growth_rate":
        completeness = components.get("completude_da_notificacao", {})
        lines += [
            f"- **Casos na janela atual:** {components.get('casos_periodo_atual')}",
            f"- **Casos na janela anterior:** {components.get('casos_periodo_anterior')}",
            f"- **Data de corte analitica:** {components.get('data_corte_analitica')} "
            f"(atraso mediano observado: {completeness.get('atraso_mediano_dias')} dias, "
            f"p90: {completeness.get('atraso_p90_dias')} dias)",
        ]
    elif key == "mortality_rate":
        lines += [
            f"- **Casos no periodo:** {components.get('casos_no_periodo')}",
            f"- **Obitos por SRAG:** {components.get('obitos_por_srag')}",
            f"- **Obitos por outras causas:** {components.get('obitos_por_outras_causas')}",
            f"- **Casos ainda em aberto:** {components.get('casos_em_aberto')}",
            f"- **Evolucao ignorada (codigo 9):** {components.get('evolucao_ignorada')}",
        ]
    elif key == "icu_admission_rate":
        occupancy = components.get("taxa_de_ocupacao_de_leitos_de_uti", {})
        completeness = components.get("completude_da_permanencia_em_uti", {})
        lines += [
            f"- **Hospitalizados no periodo:** {components.get('hospitalizados_no_periodo')}",
            f"- **Admitidos em UTI:** {components.get('admitidos_em_uti')}",
            f"- **UTI ignorado (codigo 9):** {components.get('uti_ignorado')}",
            f"- **Pico do censo diario em UTI:** "
            f"{components.get('censo_diario_pico_pacientes_em_uti')} pacientes",
        ]

        if completeness:
            lines += [
                "",
                "**Qualidade da permanencia em UTI usada no censo:**",
                "",
                f"- Estadias utilizaveis: "
                f"{completeness.get('estadias_utilizaveis_no_censo')} de "
                f"{completeness.get('admissoes_em_uti')} admissoes "
                f"({completeness.get('excluidas_por_inconsistencia')} excluidas por "
                "datas incoerentes)",
                f"- Com data de saida registrada: "
                f"{completeness.get('saida_registrada')} "
                f"({completeness.get('percentual_com_saida_registrada')}%)",
                f"- Permanencia imputada pela data de evolucao: "
                f"{completeness.get('permanencia_imputada_pela_data_de_evolucao')}",
                f"- **Permanencia imputada ate a data de corte: "
                f"{completeness.get('permanencia_imputada_ate_a_data_de_corte')} "
                f"({completeness.get('percentual_imputado_ate_o_corte')}%)** - "
                f"{completeness.get('efeito_da_imputacao')}",
            ]

        lines += [
            "",
            f"> **Taxa de ocupacao de leitos de UTI: nao calculavel.** "
            f"{occupancy.get('unavailable_reason')}",
        ]
    elif key == "vaccination_coverage_among_cases":
        covid = components.get("covid19", {})
        influenza = components.get("influenza", {})
        population = components.get("taxa_de_vacinacao_da_populacao", {})
        lines += [
            f"- **Covid-19:** {covid.get('cobertura_declarada_pct')}% "
            f"({covid.get('vacinados')} de {covid.get('com_informacao')}), "
            f"completude da informacao {covid.get('completude_da_informacao_pct')}%",
            f"- **Influenza:** {influenza.get('cobertura_declarada_pct')}% "
            f"({influenza.get('vacinados')} de {influenza.get('com_informacao')}), "
            f"completude da informacao {influenza.get('completude_da_informacao_pct')}%",
            "",
            f"> **Taxa de vacinacao da populacao: nao calculavel.** "
            f"{population.get('unavailable_reason')}",
        ]

    return lines


def _series_section(state: dict[str, Any]) -> str:
    series = state.get("series") or {}
    if not series:
        return ""

    blocks = ["## DADO - Series temporais", ""]

    daily = series.get("daily_cases")
    if daily:
        summary = daily["summary"]
        blocks += [
            f"### Casos diarios ({daily['period']['inicio']} a {daily['period']['fim']})",
            "",
            f"- Total no periodo: **{summary['total_de_casos']}** casos",
            f"- Media diaria: {summary['media_por_ponto']} | "
            f"maximo: {summary['maximo']} | minimo: {summary['minimo']}",
            "",
        ]

    monthly = series.get("monthly_cases")
    if monthly:
        summary = monthly["summary"]
        blocks += [
            f"### Casos mensais ({monthly['period']['inicio']} a {monthly['period']['fim']})",
            "",
            f"- Total no periodo: **{summary['total_de_casos']}** casos",
            "",
            "| Mes | Casos | Observacao |",
            "|-----|-------|------------|",
        ]
        for point in monthly["points"]:
            note = "mes parcial" if point.get("parcial") else ""
            blocks.append(f"| {point['mes']} | {point['casos']} | {note} |")
        blocks.append("")

    return "\n".join(blocks)


def _charts_section(state: dict[str, Any]) -> str:
    charts = state.get("charts") or {}
    if not charts:
        return ""

    blocks = ["## Visualizacoes", ""]
    titles = {
        "casos_diarios": "Numero diario de casos de SRAG - ultimos 30 dias",
        "casos_mensais": "Numero mensal de casos de SRAG - ultimos 12 meses",
    }
    for key, chart in charts.items():
        path = Path(chart["path"])
        blocks += [
            f"### {titles.get(key, key)}",
            "",
            f"![{titles.get(key, key)}]({path.as_posix()})",
            "",
            f"Arquivo: `{path}`",
            "",
        ]
    return "\n".join(blocks)


def _interpretation_section(state: dict[str, Any]) -> str:
    text = state.get("interpretation") or "Interpretacao nao disponivel nesta execucao."
    return "\n".join(
        [
            "## INFERENCIA - Interpretacao do cenario",
            "",
            f"*Texto produzido por `{state.get('interpretation_source')}` a partir "
            "exclusivamente dos resultados das tools, validado pelo guardrail de "
            "evidencia.*",
            "",
            text,
        ]
    )


def _news_section(state: dict[str, Any]) -> str:
    context = state.get("external_context") or {}
    articles = context.get("articles") or []

    blocks = [
        "## CONTEXTO EXTERNO - Noticias recentes",
        "",
        "> Noticias complementam a leitura do cenario. Elas **nao** alteram, "
        "corrigem nem substituem os indicadores calculados sobre os dados do DATASUS.",
        "",
    ]

    if not articles:
        blocks.append(
            "Nenhuma noticia recuperada nesta execucao. "
            f"{context.get('unavailable_reason') or ''}".strip()
        )
        return "\n".join(blocks)

    blocks += ["| Data | Fonte | Titulo | URL |", "|------|-------|--------|-----|"]
    for article in articles:
        title = article["titulo"].replace("|", "\\|")
        blocks.append(
            f"| {article['data']} | {article['fonte']} | {title} | "
            f"[link]({article['url']}) |"
        )

    store = context.get("vector_db") or {}
    blocks += [
        "",
        f"*Acervo consultado: {store.get('noticias_armazenadas', 0)} noticias no "
        f"Vector DB (backend de embedding: `{store.get('embedding_backend')}`); "
        f"ultima ingestao em {store.get('ultima_ingestao')}.*",
    ]
    return "\n".join(blocks)


def _data_quality_section(state: dict[str, Any]) -> str:
    """Resumo do tratamento aplicado aos dados brutos.

    Le `data/processed/quality_report.json`, produzido pela ingestao. Sem este
    bloco, o tratamento de dados ficaria invisivel no entregavel -- e uma regra
    de limpeza que ninguem consegue auditar equivale a nao ter regra.
    """
    settings = get_settings()
    path = settings.quality_report_path
    if not path.exists():
        return ""

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    adjustments = report.get("adjustments") or {}
    by_code = adjustments.get("por_codigo") or {}

    lines = [
        "## Qualidade e tratamento dos dados",
        "",
        f"- **Carga (run_id):** `{report.get('run_id')}`",
        f"- **Processada em:** {str(report.get('generated_at', ''))[:19]}",
        f"- **Arquivos de origem:** {', '.join(report.get('source_files', []))}",
        f"- **Registros lidos:** {report.get('rows_read'):,}".replace(",", "."),
        f"- **Registros descartados:** {report.get('rows_dropped')} "
        "(nenhum registro e removido silenciosamente)",
        f"- **Registros com valor alterado:** {report.get('rows_adjusted')}",
        "",
        "### Proveniencia dos dados",
        "",
        "| Ano | Arquivo | sha256 | Origem |",
        "|-----|---------|--------|--------|",
        *(
            f"| {item.get('year')} | `{item.get('filename')}` | "
            f"`{str(item.get('sha256'))[:16]}...` | {item.get('origin')} |"
            for item in (report.get("provenance") or [])
        ),
        "",
        "### Alteracoes de valor aplicadas",
        "",
        "Toda alteracao e registrada no proprio registro, na coluna "
        "`ajustes_aplicados`. Os registros afetados podem ser localizados "
        "individualmente: "
        "`SELECT * FROM srag_cases WHERE ajustes_aplicados <> ''`.",
        "",
    ]

    if by_code:
        lines += [
            "| Codigo | Registros | Significado |",
            "|--------|-----------|-------------|",
            *(
                f"| `{code}` | {count} | "
                f"{_escape_cell(_adjustment_meaning(code, adjustments))} |"
                for code, count in sorted(by_code.items())
            ),
        ]
    else:
        lines.append("Nenhum valor foi alterado nesta carga.")

    lines += [
        "",
        "### Flags de coerencia",
        "",
        "Marcadas por dimensao, nao como um unico veredito. Apenas a flag do "
        "eixo temporal exclui o registro da camada analitica; as demais sao "
        "respeitadas somente pelas metricas que dependem daquela dimensao.",
        "",
        "| Flag | Registros | % | Exclui da analise | Significado |",
        "|------|-----------|---|-------------------|-------------|",
    ]

    for name, detail in (report.get("coherence_flags") or {}).items():
        exclui = "sim" if detail.get("exclui_da_view_analitica") else "nao"
        significado = str(detail.get("significado", "")).replace("|", "\\|")
        lines.append(
            f"| `{name}` | {detail.get('registros')} | "
            f"{detail.get('percentual')}% | {exclui} | {significado} |"
        )

    rules = report.get("rules") or {}
    ignored = rules.get("codigo_9_ignorado_por_coluna") or {}
    relevantes = {
        column: count
        for column, count in ignored.items()
        if count and column in {"UTI", "EVOLUCAO", "VACINA_COV", "VACINA", "HOSPITAL"}
    }
    if relevantes:
        lines += [
            "",
            "### Codigo 9 (Ignorado) nos campos usados pelos indicadores",
            "",
            "Preservado como esta e excluido de numeradores e denominadores; "
            "nunca convertido em `Nao` nem em zero.",
            "",
            "| Campo | Registros com codigo 9 |",
            "|-------|------------------------|",
        ]
        lines += [f"| `{column}` | {count} |" for column, count in relevantes.items()]

    return "\n".join(lines)


def _escape_cell(text: str) -> str:
    """Escapa o separador de coluna do Markdown."""
    return str(text).replace("|", "\\|")


def _adjustment_meaning(code: str, adjustments: dict[str, Any]) -> str:
    """Descricao do codigo de ajuste, tolerando o sufixo de coluna."""
    meanings = adjustments.get("significado") or {}
    base = code.split(":", 1)[0]
    return meanings.get(base, base)


def _limitations_section(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") or {}
    unavailable = [
        (metric["metric"], metric["unavailable_reason"])
        for metric in metrics.values()
        if metric.get("unavailable_reason")
    ]

    blocks = ["## Limitacoes conhecidas", ""]
    blocks += [
        "- **Taxa de ocupacao de UTI nao e calculavel** com o SIVEP-Gripe: o "
        "dataset registra se houve admissao em UTI, nao a capacidade instalada "
        "nem os leitos ocupados. O relatorio apresenta a taxa de admissao em UTI "
        "entre hospitalizados e o censo diario de pacientes, ambos nomeados pelo "
        "que de fato medem.",
        "- **Taxa de vacinacao da populacao nao e calculavel** com este dataset: "
        "ha informacao vacinal apenas de pessoas notificadas com SRAG, um grupo "
        "com vies de selecao. O relatorio apresenta a cobertura declarada entre "
        "casos notificados.",
        "- **Atraso de notificacao**: a janela recente e incompleta; as analises "
        "descontam os dias mais recentes e ancoram o periodo na maior data de "
        "digitacao da base, nao na data de hoje.",
        "- **Escopo**: o SIVEP-Gripe cobre casos de SRAG notificados, "
        "majoritariamente hospitalizados; nenhum indicador representa a "
        "populacao geral.",
    ]

    for name, reason in unavailable:
        blocks.append(f"- **{name}**: {reason}")

    for warning in state.get("warnings") or []:
        blocks.append(f"- {warning}")

    for error in state.get("errors") or []:
        blocks.append(f"- **Falha registrada:** {error}")

    return "\n".join(blocks)


def _governance_section(state: dict[str, Any]) -> str:
    guardrails = state.get("guardrail_report") or {}
    evidence = state.get("evidence") or {}
    audit = state.get("audit_summary") or {}
    plan = state.get("plan") or {}

    blocks = ["## Governanca, auditoria e guardrails", ""]

    blocks += [
        "### Trilha de auditoria",
        "",
        f"- **run_id:** `{state.get('run_id')}`",
        f"- **Eventos registrados:** {audit.get('total_events', 0)}",
        f"- **Duracao total:** {audit.get('total_duration_ms', 0)} ms",
        f"- **Status dos eventos:** {json.dumps(audit.get('by_status', {}), ensure_ascii=False)}",
        f"- **Arquivo:** `{audit.get('audit_file')}`",
        "",
        "### Planejamento",
        "",
        f"- **Planejador:** `{plan.get('planner')}`",
        f"- **Tools no plano efetivo:** {', '.join(plan.get('effective_tools', []))}",
        "",
        "### Verificacao de evidencia",
        "",
        f"- **Valores lastreados pelas tools:** {evidence.get('valores_lastreados', 0)}",
        f"- **Indicadores calculados:** {evidence.get('indicadores_calculados', 0)}",
        f"- **Indicadores indisponiveis:** "
        f"{len(evidence.get('indicadores_indisponiveis', []))}",
        "",
        "### Guardrails ativos",
        "",
        "| Politica | Verificada em | Descricao |",
        "|----------|---------------|-----------|",
    ]

    for policy in guardrails.get("politicas_ativas", []):
        blocks.append(
            f"| {policy['name']} | {policy['enforced_at']} | {policy['description']} |"
        )

    result = guardrails.get("resultado") or {}
    blocks += [
        "",
        f"**Resultado da validacao de saida:** "
        f"{'aprovada' if result.get('allowed') else 'bloqueada'}"
        + (f" ({', '.join(result.get('blocked_by', []))})" if not result.get("allowed") else ""),
    ]

    if guardrails.get("fallback"):
        blocks.append(
            f"**Fallback aplicado:** {guardrails['fallback']['motivo']}."
        )

    return "\n".join(blocks)


def _render_refusal(state: dict[str, Any]) -> str:
    """Relatorio de recusa: a solicitacao nao passou na validacao de entrada."""
    validation = state.get("validation") or {}
    return "\n".join(
        [
            "# Solicitacao nao processada",
            "",
            f"- **Execucao (run_id):** `{state.get('run_id')}`",
            f"- **Solicitacao:** {state.get('request')}",
            f"- **Guardrail acionado:** `{validation.get('blocked_by')}`",
            "",
            "## Motivo",
            "",
            validation.get("reason", "Solicitacao recusada na validacao de entrada."),
            "",
            f"> {DISCLAIMER}",
        ]
    )


def render_html(markdown_text: str, state: dict[str, Any]) -> str:
    """Gera uma versao HTML navegavel do relatorio.

    A conversao e intencionalmente minima e sem dependencia externa: cobre os
    elementos efetivamente usados pelo renderizador Markdown (titulos, tabelas,
    listas, imagens, links, citacoes e blocos recolhiveis).
    """
    body = _markdown_to_html(markdown_text)
    title = f"Relatorio SRAG - {state.get('run_id', '')}"
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         max-width: 960px; margin: 0 auto; padding: 2rem 1.25rem;
         line-height: 1.6; color: #1a1a1a; background: #fbfbfa; }}
  h1 {{ border-bottom: 3px solid #1f4e79; padding-bottom: .4rem; }}
  h2 {{ margin-top: 2.5rem; color: #1f4e79; border-bottom: 1px solid #dcdcdc;
        padding-bottom: .3rem; }}
  h3 {{ margin-top: 1.8rem; color: #2e75b6; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0;
           display: block; overflow-x: auto; }}
  th, td {{ border: 1px solid #d9d9d9; padding: .5rem .7rem; text-align: left;
            font-size: .92rem; vertical-align: top; }}
  th {{ background: #eef3f8; }}
  blockquote {{ border-left: 4px solid #2e75b6; margin: 1rem 0; padding: .5rem 1rem;
                background: #eef3f8; color: #333; }}
  img {{ max-width: 100%; border: 1px solid #e0e0e0; border-radius: 4px; }}
  code {{ background: #f0f0ef; padding: .1rem .35rem; border-radius: 3px;
          font-size: .88em; }}
  details {{ margin: .6rem 0; }}
  summary {{ cursor: pointer; color: #1f4e79; font-weight: 600; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _markdown_to_html(text: str) -> str:
    """Conversor Markdown -> HTML restrito aos elementos usados no relatorio."""
    import re

    lines = text.split("\n")
    output: list[str] = []
    in_table = False
    in_list = False

    def close_blocks() -> None:
        nonlocal in_table, in_list
        if in_table:
            output.append("</table>")
            in_table = False
        if in_list:
            output.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("<details") or stripped.startswith("</details"):
            close_blocks()
            output.append(stripped)
            continue
        if stripped.startswith("<summary"):
            output.append(stripped)
            continue

        if not stripped:
            close_blocks()
            continue

        if re.fullmatch(r"\|[\s:|-]+\|", stripped):
            continue  # linha separadora do cabecalho da tabela

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not in_table:
                close_blocks()
                output.append("<table>")
                in_table = True
                output.append(
                    "<tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in cells) + "</tr>"
                )
            else:
                output.append(
                    "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>"
                )
            continue

        close_blocks() if in_table else None

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_blocks()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        if stripped.startswith("> "):
            close_blocks()
            output.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
            continue

        if stripped.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{_inline(stripped[2:])}</li>")
            continue

        close_blocks()
        output.append(f"<p>{_inline(stripped)}</p>")

    close_blocks()
    return "\n".join(output)


def _inline(text: str) -> str:
    """Aplica formatacao inline (negrito, italico, codigo, link, imagem)."""
    import re

    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1">', escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def write_report(state: dict[str, Any]) -> dict[str, str]:
    """Grava o relatorio em Markdown e HTML e devolve os caminhos.

    Args:
        state: estado final do grafo.

    Returns:
        Mapa com as chaves `markdown` e `html`.
    """
    settings = get_settings()
    settings.ensure_directories()
    run_id = state.get("run_id", "sem-run-id")

    markdown_text = render_markdown(state)
    markdown_path = settings.reports_dir / f"relatorio_srag_{run_id}.md"
    markdown_path.write_text(markdown_text, encoding="utf-8")

    html_path = settings.reports_dir / f"relatorio_srag_{run_id}.html"
    html_path.write_text(render_html(markdown_text, state), encoding="utf-8")

    logger.info(
        "relatorio gravado",
        extra={"markdown": str(markdown_path), "html": str(html_path)},
    )
    return {"markdown": str(markdown_path), "html": str(html_path)}
