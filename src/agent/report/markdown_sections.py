"""Secoes do relatorio em Markdown (o anexo tecnico completo).

Movido de `report.py`. Todo texto aqui usa os tres rotulos DADO / INFERENCIA /
CONTEXTO EXTERNO descritos no docstring do pacote (`__init__.py`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import (
    DATASUS_DATASET_URL,
    DATASUS_SOURCE_LABEL,
    DELIVERY_LABEL,
    get_settings,
)
from src.guardrails.pii import scrub_text
from src.guardrails.policies import DISCLAIMER, UNCERTAINTY_STATEMENT
from src.observability.logging_config import get_logger

from .formatting import (
    _adjustment_meaning,
    _dash,
    _escape_cell,
    _format_counts,
    _format_value,
    _load_quality_report,
)
from .markdown_to_html import _safe_url

logger = get_logger(__name__)


_INDICATOR_TITLES: dict[str, str] = {
    "case_growth_rate": "1. Taxa de aumento de casos",
    "mortality_rate": "2. Letalidade entre casos encerrados",
    "icu_admission_rate": "3. UTI (admissao e censo)",
    "icu_bed_occupancy_rate": "3b. Ocupacao de leitos de UTI por pacientes de SRAG",
    "vaccination_coverage_among_cases": "4. Cobertura vacinal",
    "incidence_rate": "5. Incidencia por 100 mil habitantes (complementar)",
    "seasonal_excess": "6. Excesso sobre o baseline sazonal (complementar)",
}

_INDICATOR_ORDER: tuple[str, ...] = tuple(_INDICATOR_TITLES)


def render_markdown(state: dict[str, Any]) -> str:
    """Monta o relatorio completo em Markdown a partir do estado final."""
    if not (state.get("validation") or {}).get("allowed", True):
        return _render_refusal(state)

    parts = [
        _header(state),
        _summary_table(state),
        _alerts_section(state),
        _indicators_section(state),
        _series_section(state),
        _charts_section(state),
        _currency_section(state),
        _duplicates_section(state),
        _data_quality_section(state),
        _optional_tools_section(state),
        _interpretation_section(state),
        _news_section(state),
        _limitations_section(state),
        _governance_section(state),
    ]
    return "\n\n".join(part for part in parts if part)


def _header(state: dict[str, Any]) -> str:
    filters = (state.get("validation") or {}).get("filters") or {}
    generated = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return "\n".join(
        [
            "# Relatorio epidemiologico de SRAG",
            "",
            f"> **{DELIVERY_LABEL}**",
            "",
            f"- **Execucao (run_id):** `{state.get('run_id')}`",
            f"- **Gerado em:** {generated}",
            f"- **Solicitacao:** {scrub_text(str(state.get('request', '')))}",
            f"- **Recorte:** {filters.get('uf', 'BR (nacional)')} | "
            f"{filters.get('classificacao_final', 'todas as classificacoes finais')}",
            f"- **Fonte dos dados:** {DATASUS_SOURCE_LABEL} ([dataset]({DATASUS_DATASET_URL}))",
            f"- **Via de interpretacao:** `{state.get('interpretation_source')}`",
            "",
            f"> {DISCLAIMER}",
        ]
    )


def _summary_table(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") or {}
    lines = [
        "## Resumo dos indicadores",
        "",
        "| # | Indicador | Valor | Numerador | Denominador | Status |",
        "|---|-----------|-------|-----------|-------------|--------|",
    ]
    occupancy_note = False
    for index, key in enumerate(_INDICATOR_ORDER, start=1):
        metric = metrics.get(key)
        if metric is None:
            lines.append(f"| {index} | {key} | - | - | - | nao executado |")
            continue
        status = "calculado" if metric.get("value") is not None else "indisponivel"
        label = metric["metric"]
        # Marcado aqui, e nao so na secao detalhada abaixo: esta tabela e o
        # trecho mais provavel de ser lido isoladamente, e um valor baixo de
        # ocupacao sem o asterisco convida a leitura errada "rede com folga".
        if key == "icu_bed_occupancy_rate" and metric.get("value") is not None:
            label += " [*]"
            occupancy_note = True
        lines.append(
            f"| {index} | {label} | {_format_value(metric)} | "
            f"{metric.get('numerator', '-')} | {metric.get('denominator', '-')} | {status} |"
        )
    if occupancy_note:
        lines += [
            "",
            "`[*]` mede so a parcela ocupada por pacientes de SRAG (piso da ocupacao "
            "real, nao a ocupacao total) e cobre uma janela deslocada para tras em "
            "relacao aos demais indicadores -- ver secao 3b para o periodo exato.",
        ]
    return "\n".join(lines)


def _alerts_section(state: dict[str, Any]) -> str:
    """Veredito das regras de alerta e variacao desde a execucao anterior."""
    alerts = state.get("alerts") or {}
    if not alerts:
        return ""

    level = str(alerts.get("nivel", "normal")).upper()
    blocks = [
        "## DADO - Alertas e acompanhamento",
        "",
        f"**Nivel consolidado: {level}.** {alerts.get('resumo')}",
        "",
        "| Regra | Indicador | Observado | Limiar | Disparada |",
        "|-------|-----------|-----------|--------|-----------|",
    ]
    for item in alerts.get("regras_avaliadas") or []:
        observed = item.get("valor_observado")
        observed_text = "indisponivel" if observed is None else f"{observed}{item.get('unidade')}"
        blocks.append(
            f"| {item.get('regra')} | {item.get('indicador')} | {observed_text} | "
            f"{item.get('limiar')}{item.get('unidade')} | "
            f"{'**sim**' if item.get('disparada') else 'nao'} |"
        )

    for item in alerts.get("atencao") or []:
        blocks.append("")
        blocks.append(f"> **Atencao ({item.get('regra')}):** {item.get('mensagem')}")

    history = alerts.get("historico") or {}
    blocks += ["", "### Variacao desde a execucao anterior", ""]
    if not history.get("execucao_anterior"):
        blocks.append(history.get("mensagem", "Sem execucao anterior para este recorte."))
        return "\n".join(blocks)

    blocks += [
        f"- **Execucao anterior:** `{history.get('execucao_anterior')}` "
        f"(gerada em {str(history.get('gerada_em', ''))[:19]}, corte "
        f"{history.get('data_corte_anterior')}; corte atual {history.get('data_corte_atual')})",
        "",
        "| Indicador | Anterior | Atual | Variacao |",
        "|-----------|----------|-------|----------|",
    ]
    for key, item in (history.get("variacao") or {}).items():
        blocks.append(
            f"| {key} | {_dash(item.get('anterior'))} | {_dash(item.get('atual'))} | "
            f"{_dash(item.get('variacao'))} |"
        )
    if history.get("mesma_data_de_corte"):
        blocks += [
            "",
            "*As duas execucoes usam a mesma data de corte analitica: a variacao reflete "
            "atualizacao da base pela fonte, nao passagem do tempo.*",
        ]
    return "\n".join(blocks)


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
        if metric.get("records_used") is not None:
            blocks.append(f"- **Registros na base do calculo:** {metric['records_used']}")
        blocks.append(
            f"- **Periodo:** {period.get('inicio')} a {period.get('fim')} "
            f"({period.get('descricao')})"
        )
        blocks.append(f"- **Fonte:** {metric.get('source')}")

        if metric.get("unavailable_reason"):
            blocks.append(f"- **Indisponibilidade:** {metric['unavailable_reason']}")
            blocks.append(f"- **Declaracao:** {UNCERTAINTY_STATEMENT}")

        if metric.get("reliability_warning"):
            blocks.append("")
            blocks.append(f"> **Atencao:** {metric['reliability_warning']}")

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
            f"- **Internados no periodo:** {components.get('internados_no_periodo')}",
            f"- **Admitidos em UTI:** {components.get('admitidos_em_uti')}",
            f"- **UTI ignorado (codigo 9):** {components.get('uti_ignorado')}",
            f"- **Pico do censo diario em UTI:** "
            f"{components.get('censo_diario_pico_pacientes_em_uti')} pacientes em "
            f"{components.get('censo_diario_pico_data')} "
            f"({components.get('censo_diario_pico_percentual_imputado')}% desse valor "
            "depende de imputacao de permanencia)",
            f"- **Com UTI informado e sem HOSPITAL='Sim':** ausente "
            f"{components.get('com_uti_informado_e_hospital_ausente')}, ignorado "
            f"{components.get('com_uti_informado_e_hospital_ignorado')} "
            "(contam como internados nos dois bracos: a base e de SRAG "
            "hospitalizada e ausencia nao e lida como 'Nao')",
            f"- **Admitidos em UTI com internacao negada:** "
            f"{components.get('admitidos_em_uti_com_internacao_negada')} "
            "(contradicao no registro; nao sao resgatados)",
        ]

        if completeness:
            lines += [
                "",
                "**Qualidade da permanencia em UTI usada no censo** "
                f"({completeness.get('populacao')}):",
                "",
                f"- Estadias no censo: {completeness.get('estadias_no_censo')}",
                f"- Com data de saida registrada: "
                f"{completeness.get('saida_registrada')} "
                f"({completeness.get('percentual_com_saida_registrada')}%)",
                f"- Permanencia imputada pela data de evolucao: "
                f"{completeness.get('permanencia_imputada_pela_data_de_evolucao')}",
                f"- **Permanencia imputada em aberto: "
                f"{completeness.get('permanencia_imputada_em_aberto')} "
                f"({completeness.get('percentual_imputado_em_aberto')}%)**",
                f"- Estadias imputadas truncadas pelo teto de "
                f"{completeness.get('teto_de_permanencia_dias')} dias "
                f"({completeness.get('origem_do_teto')}): "
                f"{completeness.get('imputadas_truncadas_pelo_teto')} -- o teto limita "
                f"tanto a imputacao pela data de evolucao quanto a imputacao em aberto",
                f"- {completeness.get('efeito_da_imputacao')}",
            ]

        lines += [
            "",
            f"> **Este indicador nao e ocupacao de leitos.** {occupancy.get('nota')} "
            f"A ocupacao e publicada em separado como `{occupancy.get('indicador')}`.",
        ]
    elif key == "icu_bed_occupancy_rate":
        lines += _icu_occupancy_lines(components)
    elif key == "vaccination_coverage_among_cases":
        covid = components.get("covid19", {})
        influenza = components.get("influenza", {})
        lines += [
            f"- **Covid-19:** {covid.get('cobertura_declarada_pct')}% "
            f"({covid.get('vacinados')} de {covid.get('com_informacao')}), "
            f"completude da informacao {covid.get('completude_da_informacao_pct')}%",
            f"- **Influenza:** {influenza.get('cobertura_declarada_pct')}% "
            f"({influenza.get('vacinados')} de {influenza.get('com_informacao')}), "
            f"completude da informacao {influenza.get('completude_da_informacao_pct')}%",
            "",
            *_population_coverage_lines(components.get("taxa_de_vacinacao_da_populacao") or {}),
        ]
    elif key == "incidence_rate":
        lines += [
            f"- **Casos na janela:** {components.get('casos_na_janela')}",
            f"- **Populacao de referencia:** {components.get('populacao_de_referencia')} "
            f"({components.get('fonte_da_populacao')}, estimativa de "
            f"{components.get('ano_da_estimativa_populacional')})",
        ]
    elif key == "seasonal_excess":
        excluded = components.get("anos_excluidos_por_definicao") or {}
        lines += [
            f"- **Casos na janela atual:** {components.get('casos_na_janela_atual')}",
            f"- **Mediana do baseline:** {components.get('mediana_do_baseline')} "
            f"(media: {components.get('media_do_baseline')})",
            f"- **Anos considerados:** {components.get('anos_considerados')} "
            f"(configurados: {components.get('anos_configurados')}; ausentes na base: "
            f"{components.get('anos_ausentes_na_base')})",
            f"- **Anos excluidos por definicao:** {excluded.get('anos')} -- "
            f"{excluded.get('motivo')}",
        ]
        by_year = components.get("casos_por_ano_de_baseline") or {}
        if by_year:
            lines += [
                "",
                "| Ano | Janela comparada | Casos |",
                "|-----|------------------|-------|",
                *(
                    f"| {year} | {item.get('inicio')} a {item.get('fim')} | {item.get('casos')} |"
                    for year, item in sorted(by_year.items())
                ),
            ]

    return lines


def _icu_occupancy_lines(components: dict[str, Any]) -> list[str]:
    """Numerador, denominador e proveniencia da ocupacao de leitos de UTI."""
    capacity = components.get("capacidade_instalada")
    lines = [f"- **Formula:** `{components.get('formula')}`"]

    if not capacity:
        lines += [
            "",
            "> **Capacidade instalada nao disponivel para este recorte.** O "
            "motivo consta acima, em *Indisponibilidade*. A taxa de admissao em "
            "UTI e o censo diario continuam publicados e **nao** sao ocupacao.",
        ]
        return lines

    window = components.get("janela_madura") or {}
    lines += [
        f"- **Dia publicado:** {components.get('data_do_valor_publicado')} "
        f"({components.get('criterio_do_dia_publicado')})",
        f"- **Janela madura:** {window.get('inicio')} a {window.get('fim')} -- "
        f"deslocada {window.get('deslocamento_dias')} dias para tras porque "
        f"{window.get('motivo_do_deslocamento')}",
        f"- **Numerador (pacientes de SRAG em UTI no dia):** "
        f"{components.get('pacientes_em_uti_no_dia')} "
        f"({components.get('percentual_do_numerador_imputado')}% depende de "
        "imputacao de permanencia)",
        f"- **Denominador (leitos de UTI existentes):** "
        f"{components.get('leitos_disponiveis_no_dia')} "
        f"(dos quais {components.get('leitos_sus_no_denominador')} destinados ao SUS)",
        f"- **Competencia do CNES usada:** {capacity.get('competencia')} "
        f"({capacity.get('defasagem_meses')} mes(es) de distancia da data de "
        f"corte analitica; limite configurado: ICU_CAPACITY_MAX_LAG_MONTHS)",
        f"- **Cobertura geografica da capacidade:** {capacity.get('ufs_cobertas')} "
        f"UF(s), exigidas {capacity.get('ufs_exigidas')}",
        f"- **Tipos de leito no denominador:** {', '.join(capacity.get('tipos_de_leito') or [])}",
        f"- **Ocupacao media na janela madura:** "
        f"{components.get('ocupacao_media_na_janela_madura_pct')}% "
        f"(minimo {components.get('ocupacao_minima_na_janela_madura_pct')}%, "
        f"{components.get('dias_apurados')} dias apurados)",
        f"- **Fonte da capacidade:** {capacity.get('fonte')} "
        f"(extraida em {str(capacity.get('data_extracao'))[:10]})",
        "",
        f"> **Alcance:** {components.get('alcance_do_indicador')}. Um valor baixo "
        "**nao** significa rede com folga.",
        "",
        f"> **Periodo distinto.** {window.get('nota')}",
    ]
    return lines


def _population_coverage_lines(population: dict[str, Any]) -> list[str]:
    """Bloco da taxa de vacinacao da populacao: calculada ou declarada indisponivel."""
    if population.get("value", None) is None and not population.get("campanhas"):
        return [
            f"> **Taxa de vacinacao da populacao: nao calculavel.** "
            f"{population.get('unavailable_reason')}",
        ]

    lines = [
        f"**Taxa de vacinacao da populacao** (referencia externa, ano "
        f"{population.get('ano_da_referencia')}):",
        "",
    ]
    for campaign, item in sorted((population.get("campanhas") or {}).items()):
        if item.get("value") is None:
            lines.append(f"- **{campaign}:** nao calculavel -- {item.get('unavailable_reason')}")
            continue
        lines.append(
            f"- **{campaign}:** {item['value']}% ({item.get('numerator')} doses sobre "
            f"{item.get('denominator')}, {item.get('denominador_descricao')}; fonte: "
            f"{item.get('fonte')})"
        )
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
    settings = get_settings()
    for key, chart in charts.items():
        path = Path(chart["path"])
        try:
            relative = path.resolve().relative_to(settings.outputs_dir.resolve())
            image_source = (Path("..") / relative).as_posix()
        except ValueError:
            image_source = path.as_posix()
        blocks += [
            f"### {titles.get(key, key)}",
            "",
            f"![{titles.get(key, key)}]({image_source})",
            "",
            f"Arquivo: `{path}`",
            "",
        ]
    return "\n".join(blocks)


def _currency_section(state: dict[str, Any]) -> str:
    """As datas que situam o relatorio, lado a lado e com a defasagem explicita.

    Sem esta tabela o leitor supoe que "relatorio gerado hoje" significa "dados
    ate hoje". Nao significa: entre as duas ha a defasagem de publicacao da
    fonte e o desconto do atraso de notificacao, e as duas sao grandes o
    bastante para mudar a leitura do cenario.
    """
    diagnostics = state.get("diagnostics") or {}
    completeness = diagnostics.get("get_notification_completeness") or {}
    currency = completeness.get("atualidade_da_base")
    if not currency:
        return ""

    meaning = currency.get("significado") or {}
    lines = [
        "## DADO - Atualidade da base e datas de referencia",
        "",
        "| Data | Valor | O que e |",
        "|------|-------|---------|",
        f"| Data atual do sistema | {currency.get('data_atual_do_sistema')} | "
        f"{_escape_cell(meaning.get('data_atual_do_sistema', ''))} |",
        f"| Sintomas mais recentes na base | "
        f"{currency.get('data_mais_recente_de_sintomas_na_base')} | "
        f"{_escape_cell(meaning.get('data_mais_recente_de_sintomas_na_base', ''))} |",
        f"| Digitacao mais recente na base | "
        f"{currency.get('data_mais_recente_de_digitacao_na_base')} | "
        f"{_escape_cell(meaning.get('data_mais_recente_de_digitacao_na_base', ''))} |",
        f"| **Corte epidemiologico** | **{currency.get('data_de_corte_epidemiologica')}** | "
        f"{_escape_cell(meaning.get('data_de_corte_epidemiologica', ''))} |",
        "",
        f"- **Atraso de notificacao configurado:** "
        f"{currency.get('atraso_de_notificacao_configurado_dias')} dias "
        "(`REPORTING_LAG_DAYS`)",
        f"- **Defasagem entre hoje e a ultima digitacao:** "
        f"{currency.get('defasagem_ate_a_digitacao_dias')} dias",
        f"- **Defasagem entre hoje e o corte epidemiologico:** "
        f"{currency.get('defasagem_ate_o_corte_dias')} dias",
    ]

    source = currency.get("atualizacao_da_fonte") or {}
    if source.get("disponivel"):
        lines.append(
            f"- **Arquivo bruto obtido da fonte em:** "
            f"{str(source.get('obtido_da_fonte_em'))[:19]} "
            f"(anos {', '.join(source.get('anos_no_manifesto') or [])}) -- "
            f"{source.get('observacao')}"
        )
    else:
        lines.append(f"- **Atualizacao da fonte:** nao declarada ({source.get('motivo')})")

    lines += [
        "",
        "> A data de execucao **nao** e a data ate a qual ha dado. O DATASUS "
        "publica com defasagem, e o sistema ainda desconta o atraso de "
        "notificacao para nao ler digitacao pendente como queda de casos.",
    ]
    return "\n".join(lines)


def _duplicates_section(state: dict[str, Any]) -> str:
    """Sensibilidade a linhas identicas: o que mudaria se fossem colapsadas."""
    diagnostics = state.get("diagnostics") or {}
    sensitivity = diagnostics.get("get_duplicate_sensitivity")
    if not sensitivity:
        return ""

    lines = [
        "## DADO - Sensibilidade a linhas identicas",
        "",
        f"- **Criterio:** {sensitivity.get('criterio')}",
        f"- **Casos na janela:** {sensitivity.get('casos_na_janela')} "
        f"(seriam {sensitivity.get('casos_se_deduplicado')} se colapsadas)",
        f"- **Linhas identicas excedentes:** "
        f"{sensitivity.get('linhas_identicas_excedentes')} "
        f"({sensitivity.get('percentual_excedente')}% da janela)",
        "",
        "| Indicador | Publicado | Se deduplicado | Diferenca |",
        "|-----------|-----------|----------------|-----------|",
    ]
    for payload in (sensitivity.get("indicadores") or {}).values():
        lines.append(
            f"| {payload.get('nome')} | {_dash(payload.get('valor_publicado_pct'))}% | "
            f"{_dash(payload.get('valor_se_deduplicado_pct'))}% | "
            f"{_dash(payload.get('diferenca_pp'))} p.p. |"
        )

    lines += [
        "",
        f"**Veredito:** {sensitivity.get('veredito')}",
        "",
        f"> **Decisao de tratamento.** {sensitivity.get('decisao_de_tratamento')}",
        "",
        f"> **Pseudonimizacao.** {sensitivity.get('pseudonimizacao')}",
    ]
    return "\n".join(lines)


def _optional_tools_section(state: dict[str, Any]) -> str:
    """O que o modelo decidiu aprofundar, e o que dele foi recusado.

    A secao existe mesmo quando o modelo nada escolheu: dizer "o contrato
    bastou" e uma informacao, e sua ausencia deixaria o leitor sem saber se a
    etapa rodou. E a recusa aparece ao lado do aceite -- uma allowlist que nunca
    mostra o que barrou nao pode ser avaliada.
    """
    optional = state.get("optional_tools") or {}
    if not optional:
        return ""

    blocks = [
        "## DADO - Analises adicionais escolhidas pelo modelo",
        "",
        f"- **Modo:** `{optional.get('modo')}` (planejador: `{optional.get('planejador')}`)",
        f"- **Contrato obrigatorio:** {optional.get('contrato_obrigatorio')}",
        f"- **Tools que o modelo pode acionar:** {len(optional.get('allowlist') or [])} "
        "operacoes de leitura, com parametros validados contra dominios fechados",
        f"- **Iteracoes de tool calling:** {optional.get('iteracoes')}",
    ]
    if optional.get("fallback"):
        blocks.append(
            "- **Fallback:** a selecao pelo modelo falhou nesta execucao e o "
            "relatorio saiu com o contrato obrigatorio completo. O detalhe "
            "tecnico esta na trilha de auditoria."
        )

    decisions = optional.get("decisoes") or []
    if not decisions:
        blocks += [
            "",
            "Nenhuma analise adicional foi acionada: a solicitacao ja estava "
            "inteiramente atendida pelos indicadores do contrato.",
        ]
        if optional.get("justificativa_do_modelo"):
            blocks.append("")
            blocks.append(f"> {scrub_text(str(optional['justificativa_do_modelo']))}")
        return "\n".join(blocks)

    blocks += [
        "",
        "| Tool | Parametros | Decisao | Motivo | Resultado |",
        "|------|------------|---------|--------|-----------|",
    ]
    for decision in decisions:
        parameters = decision.get("parametros") or {}
        rendered = (
            ", ".join(f"`{key}={value}`" for key, value in sorted(parameters.items())) or "padrao"
        )
        blocks.append(
            f"| `{decision.get('tool')}` | {rendered} | "
            f"{'**aceita**' if decision.get('aceita') else 'recusada'} | "
            f"{_escape_cell(str(decision.get('motivo', '')))} | "
            f"{_escape_cell(str(decision.get('resumo_do_resultado') or '-'))} |"
        )

    if optional.get("justificativa_do_modelo"):
        blocks += [
            "",
            f"> **Justificativa do modelo:** "
            f"{scrub_text(str(optional['justificativa_do_modelo']))}",
        ]
    return "\n".join(blocks)


def _interpretation_section(state: dict[str, Any]) -> str:
    text = state.get("interpretation") or "Interpretação não disponível nesta execução."
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
    window_days = context.get("janela_dias")

    window_note = (
        f" Janela configurada: ate {window_days} dias antes da execucao."
        if window_days is not None
        else ""
    )
    blocks = [
        "## CONTEXTO EXTERNO - Noticias recentes",
        "",
        "> Noticias complementam a leitura do cenario. Elas **nao** alteram, "
        "corrigem nem substituem os indicadores calculados sobre os dados do "
        f"DATASUS.{window_note}",
        "",
    ]

    if not articles:
        blocks.append(
            "Nenhuma noticia dentro da janela configurada nesta execucao. "
            f"{context.get('unavailable_reason') or ''}".strip()
        )
        return "\n".join(blocks)

    blocks += [
        "| Publicada em | Fonte | Titulo | URL | Recuperada em |",
        "|--------------|-------|--------|-----|---------------|",
    ]
    for article in articles:
        title = article["titulo"].replace("|", "\\|")
        source = article["fonte"].replace("|", "\\|")
        safe_url = _safe_url(article["url"])
        link = f"[link]({safe_url})" if safe_url else "link bloqueado"
        blocks.append(
            f"| {article['data']} | {source} | {title} | {link} | "
            f"{article.get('retrieved_at', 'nao informado')} |"
        )

    newest, oldest = context.get("data_mais_recente"), context.get("data_mais_antiga")
    if newest or oldest:
        blocks += [
            "",
            f"*Noticia mais recente: {newest or 'nao informado'}; mais antiga dentro da "
            f"janela: {oldest or 'nao informado'}.*",
        ]

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
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "relatorio de qualidade ilegivel; secao omitida",
            extra={"path": str(path), "motivo": f"{type(exc).__name__}: {exc}"},
        )
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
        f"- **Linhas identicas nas colunas lidas:** "
        f"{(report.get('rules') or {}).get('linhas_identicas_nas_colunas_lidas')} "
        "(contadas, nao deduplicadas: sem o identificador da notificacao, excluido por "
        "minimizacao, nao ha como distinguir duplicata de pacientes distintos com os mesmos "
        "atributos agregados)",
        f"- **Codigos fora do dominio do dicionario:** "
        f"{_format_counts((report.get('rules') or {}).get('codigo_fora_do_dominio_por_coluna'))} "
        "(contados, nao anulados; a camada de metricas so reconhece codigos validos)",
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
                f"| `{code}` | {count} | {_escape_cell(_adjustment_meaning(code, adjustments))} |"
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


def _limitations_section(state: dict[str, Any]) -> str:
    metrics = state.get("metrics") or {}
    unavailable = [
        (metric["metric"], metric["unavailable_reason"])
        for metric in metrics.values()
        if metric.get("unavailable_reason")
    ]

    blocks = ["## Limitacoes conhecidas", ""]
    blocks += [
        "- **Tres indicadores distintos de UTI, nunca intercambiaveis**: a *taxa "
        "de admissao* mede severidade dos casos notificados; o *censo diario* "
        "conta pacientes; a *taxa de ocupacao* divide o censo pela capacidade "
        "instalada do CNES. So a terceira e ocupacao, e ela depende de uma "
        "referencia externa -- sem capacidade compativel em UF e competencia, "
        "ela e declarada indisponivel, e nunca aproximada pelas outras duas.",
        "- **A ocupacao publicada e a parcela ocupada por pacientes de SRAG**, "
        "um piso da ocupacao total: os mesmos leitos atendem pacientes sem SRAG. "
        "Valor baixo nao significa rede com folga.",
        "- **Taxa de vacinacao da populacao depende de fonte externa** (SI-PNI "
        "para as doses, populacao-alvo da campanha ou IBGE para o denominador). "
        "O SIVEP-Gripe so tem informacao vacinal de pessoas notificadas com "
        "SRAG, um grupo com vies de selecao, e essa cobertura entre casos e "
        "publicada como indicador proprio -- nunca no lugar da populacional. Sem "
        "a referencia, a populacional fica indisponivel.",
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


def _observability_block(state: dict[str, Any]) -> list[str]:
    """Tudo que a execucao precisa declarar sobre si mesma, num so lugar.

    Reconstituir uma execucao antiga exige saber contra **qual** base ela rodou,
    com quais referencias externas, qual modelo e o que degradou. Espalhado por
    cinco secoes, isso existe mas nao se encontra; reunido aqui, uma execucao
    passa a ser reproduzivel a partir do relatorio, sem abrir o repositorio.
    """
    diagnostics = state.get("diagnostics") or {}
    currency = (diagnostics.get("get_notification_completeness") or {}).get(
        "atualidade_da_base"
    ) or {}
    quality = _load_quality_report()
    optional = state.get("optional_tools") or {}
    external = state.get("external_context") or {}
    access = external.get("acesso_ao_acervo") or {}
    store = external.get("vector_db") or {}
    usage = state.get("llm_usage") or {}

    lines = ["### Observabilidade da execucao", ""]

    lines += ["**Versao dos dados e proveniencia**", ""]
    if quality:
        lines += [
            f"- **Carga de origem (run_id):** `{quality.get('run_id')}`",
            f"- **Arquivos de origem:** {', '.join(quality.get('source_files') or []) or '-'}",
        ]
        for item in quality.get("provenance") or []:
            lines.append(
                f"- **{item.get('year')}:** `{item.get('filename')}` "
                f"(sha256 `{str(item.get('sha256'))[:16]}...`, {item.get('origin')})"
            )
    else:
        lines.append("- Relatorio de qualidade da carga nao encontrado nesta instalacao.")
    if currency:
        lines += [
            f"- **Corte epidemiologico:** {currency.get('data_de_corte_epidemiologica')} "
            f"(digitacao mais recente {currency.get('data_mais_recente_de_digitacao_na_base')}, "
            f"atraso configurado {currency.get('atraso_de_notificacao_configurado_dias')} dias)",
        ]

    lines += ["", "**Referencias externas usadas**", ""]
    try:
        from src.data.reference.tables import reference_provenance

        provenance = reference_provenance()
    except Exception:  # proveniencia e acessoria; nao derruba o relatorio
        provenance = {}
    rotulos = {
        "populacao_uf": "Populacao residente (IBGE)",
        "cobertura_vacinal_uf": "Doses aplicadas (SI-PNI)",
        "leitos_uti_uf": "Leitos de UTI (CNES)",
    }
    if provenance:
        lines += [
            "| Referencia | Disponivel | Obtida em | sha256 | Linhas |",
            "|------------|------------|-----------|--------|--------|",
        ]
        for table, entry in provenance.items():
            if entry.get("disponivel"):
                lines.append(
                    f"| {rotulos.get(table, table)} | sim | "
                    f"{str(entry.get('obtido_em'))[:19]} | "
                    f"`{str(entry.get('sha256'))[:16]}...` | {entry.get('linhas')} |"
                )
            else:
                lines.append(f"| {rotulos.get(table, table)} | **nao** | - | - | - |")
    else:
        lines.append("- Proveniencia das referencias externas indisponivel nesta execucao.")

    lines += ["", "**Indicadores indisponiveis e causa**", ""]
    unavailable = [
        (metric.get("metric"), metric.get("unavailable_reason"))
        for metric in (state.get("metrics") or {}).values()
        if metric.get("value") is None
    ]
    if unavailable:
        for key, reason in unavailable:
            lines.append(f"- `{key}`: {_escape_cell(str(reason))}")
    else:
        lines.append("- Nenhum: todos os indicadores do contrato foram calculados.")

    lines += [
        "",
        "**Camada de agente e modelo**",
        "",
        f"- **Modo de selecao de tools:** `{optional.get('modo', 'nao executado')}` "
        f"(planejador `{optional.get('planejador', '-')}`, "
        f"{len(optional.get('tools_aceitas') or [])} aceita(s), "
        f"{len(optional.get('tools_recusadas') or [])} recusada(s))",
        f"- **Via de interpretacao:** `{state.get('interpretation_source')}`",
    ]
    for label, key in (("Interpretador", "interpretador"), ("Revisor", "revisor_semantico")):
        item = usage.get(key)
        if item:
            lines.append(
                f"- **{label}:** `{item.get('modelo')}`, {item.get('tokens_total')} tokens, "
                f"custo estimado US$ {item.get('custo_estimado_usd')}"
            )
    if not usage.get("interpretador") and not usage.get("revisor_semantico"):
        lines.append("- Nenhuma chamada a modelo nesta execucao (via deterministica).")

    lines += [
        "",
        "**Contexto externo e resiliencia**",
        "",
        f"- **Backend de embedding:** `{store.get('embedding_backend', 'nao aplicavel')}`",
        f"- **Acervo consultado:** {store.get('noticias_armazenadas', 0)} noticias "
        f"(ultima ingestao {store.get('ultima_ingestao', 'nao informada')})",
        f"- **Acesso ao acervo:** {access.get('resultado', 'nao registrado')}, "
        f"{access.get('tentativas', 0)} tentativa(s), "
        f"{access.get('retentativas', 0)} retentativa(s)",
    ]
    if external.get("unavailable_reason"):
        lines.append(f"- **Degradacao declarada:** {external['unavailable_reason']}")
    if optional.get("fallback"):
        lines.append(
            "- **Fallback da selecao de tools:** aplicado; o contrato obrigatorio "
            "saiu completo e o detalhe tecnico esta na trilha de auditoria."
        )

    lines.append("")
    return lines


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
        "Consulta por SQL, sobre todas as execucoes: "
        "`SELECT * FROM audit_events WHERE run_id = '<run_id>' ORDER BY seq;` "
        "no banco analitico. Ou, no terminal: "
        f"`python main.py --audit {state.get('run_id')}`.",
        "",
        "### Planejamento",
        "",
        f"- **Planejador:** `{plan.get('planner')}`",
        f"- **Tools no plano efetivo:** {', '.join(plan.get('effective_tools', []))}",
        "",
        *_observability_block(state),
        "### Verificacao de evidencia",
        "",
        f"- **Valores lastreados pelas tools:** {evidence.get('valores_lastreados', 0)}",
        f"- **Indicadores calculados:** {evidence.get('indicadores_calculados', 0)}",
        f"- **Indicadores indisponiveis:** {len(evidence.get('indicadores_indisponiveis', []))}",
        "",
        "### Guardrails ativos",
        "",
        "| Politica | Verificada em | Descricao |",
        "|----------|---------------|-----------|",
    ]

    for policy in guardrails.get("politicas_ativas", []):
        blocks.append(f"| {policy['name']} | {policy['enforced_at']} | {policy['description']} |")

    result = guardrails.get("resultado") or {}
    blocks += [
        "",
        f"**Resultado da validacao de saida:** "
        f"{'aprovada' if result.get('allowed') else 'bloqueada'}"
        + (f" ({', '.join(result.get('blocked_by', []))})" if not result.get("allowed") else ""),
    ]

    if guardrails.get("fallback"):
        blocks.append(f"**Fallback aplicado:** {guardrails['fallback']['motivo']}.")

    semantic = guardrails.get("revisao_semantica") or {}
    if semantic:
        if semantic.get("executada"):
            verdict = "aprovado" if semantic.get("aprovado") else "com achados"
            status = f"executada por `{semantic.get('revisor')}` - {verdict}"
            if semantic.get("achados"):
                unique = dict.fromkeys(
                    f"{item.get('categoria')}: {item.get('motivo')}" for item in semantic["achados"]
                )
                status += "; " + "; ".join(unique)
        elif semantic.get("erro"):
            status = f"indisponivel ({semantic['erro']}); prevaleceram as verificacoes lexicais"
        elif semantic.get("revisor") == "nao aplicavel":
            status = "nao aplicavel: texto produzido pela via deterministica, sem modelo"
        else:
            status = "nao executada (sem credencial ou desativada); somente verificacoes lexicais"
        blocks.append(f"**Revisao semantica independente:** {status}.")

    usage = state.get("llm_usage") or {}
    blocks += ["", "### Consumo do modelo de linguagem", ""]
    if not usage.get("interpretador") and not usage.get("revisor_semantico"):
        blocks.append("Nenhuma chamada a modelo nesta execucao (via deterministica).")
    else:
        for label, key in (("Interpretador", "interpretador"), ("Revisor", "revisor_semantico")):
            item = usage.get(key)
            if item:
                blocks.append(
                    f"- **{label}** (`{item.get('modelo')}`): {item.get('chamadas')} chamada(s), "
                    f"{item.get('tokens_entrada')} tokens de entrada, "
                    f"{item.get('tokens_saida')} de saida, custo estimado "
                    f"US$ {item.get('custo_estimado_usd')}"
                )
        blocks.append(
            f"- **Custo total estimado:** US$ {usage.get('custo_total_estimado_usd')} "
            "(precos de lista configurados; estimativa, nao fatura)"
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
            *_injection_block(validation),
            f"> {DISCLAIMER}",
        ]
    )


def _injection_block(validation: dict[str, Any]) -> list[str]:
    """Achados da analise de prompt injection, quando houver.

    Publicados na recusa porque quem escreveu a solicitacao precisa saber
    exatamente o que foi lido como tentativa de controle -- caso contrario a
    recusa vira um "nao" opaco e o usuario tenta de novo no escuro.
    """
    injection = validation.get("prompt_injection") or {}
    findings = injection.get("achados") or []
    if not findings:
        return []
    lines = [
        "## Analise de prompt injection",
        "",
        f"- **Risco classificado:** `{injection.get('risco')}`",
        "",
        "| Padrao | Risco | O que ele descreve |",
        "|--------|-------|--------------------|",
    ]
    for finding in findings:
        lines.append(
            f"| `{finding.get('key')}` | {finding.get('risco', finding.get('risk'))} | "
            f"{_escape_cell(str(finding.get('description', '')))} |"
        )
    if injection.get("neutralizacoes_no_texto"):
        lines += [
            "",
            "Trechos neutralizados antes da analise: "
            + ", ".join(f"`{item}`" for item in injection["neutralizacoes_no_texto"])
            + ".",
        ]
    lines.append("")
    return lines
