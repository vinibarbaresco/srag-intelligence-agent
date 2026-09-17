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
from src.guardrails.pii import scrub_text
from src.guardrails.policies import DISCLAIMER, UNCERTAINTY_STATEMENT
from src.observability.logging_config import get_logger

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


def _dash(value: Any) -> str:
    return "-" if value is None else str(value)


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


def _format_counts(counts: dict[str, Any] | None) -> str:
    """Resume um mapa `coluna -> contagem`, omitindo zeros."""
    relevant = {column: count for column, count in (counts or {}).items() if count}
    if not relevant:
        return "nenhum"
    return ", ".join(f"`{column}`={count}" for column, count in sorted(relevant.items()))


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


def _pt_number(value: float) -> str:
    """Formata um numero no padrao pt-BR (virgula decimal), sem zeros a mais."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        text = "0"
    return text.replace(".", ",")


def _pt_int(value: Any) -> str:
    """Formata um inteiro com separador de milhar pt-BR."""
    if value is None:
        return "-"
    return f"{int(value):,}".replace(",", ".")


def _load_quality_report() -> dict[str, Any]:
    """Le `quality_report.json`; devolve `{}` se ausente ou ilegivel."""
    settings = get_settings()
    path = settings.quality_report_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "relatorio de qualidade ilegivel; secao omitida",
            extra={"path": str(path), "motivo": f"{type(exc).__name__}: {exc}"},
        )
        return {}


def _analysis_cutoff(state: dict[str, Any]) -> str | None:
    """Data de corte analitica, lida da serie diaria ou de qualquer indicador."""
    series = state.get("series") or {}
    cutoff = ((series.get("daily_cases") or {}).get("period") or {}).get("fim")
    if cutoff:
        return cutoff
    for metric in (state.get("metrics") or {}).values():
        cutoff = (metric.get("period") or {}).get("fim")
        if cutoff:
            return cutoff
    return None


def _trend_arrow(value: float, *, worse_when_up: bool) -> tuple[str, str]:
    """Seta de tendencia e a classe CSS que a colore -- fato numerico, nao opiniao."""
    if abs(value) < 1e-9:
        return "→", "trend-neutral"
    up = value > 0
    arrow = "▲" if up else "▼"
    concerning = up == worse_when_up
    return arrow, ("trend-bad" if concerning else "trend-good")


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
    icu = metrics.get("icu_admission_rate") or {}
    vaccination = metrics.get("vaccination_coverage_among_cases") or {}

    icu_reason = ((icu.get("components") or {}).get("taxa_de_ocupacao_de_leitos_de_uti") or {}).get(
        "unavailable_reason"
    ) or (
        "Nao e possivel calcular taxa de ocupacao de UTI com os dados disponiveis: o "
        "SIVEP-Gripe nao registra capacidade instalada nem leitos ocupados."
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


#: Tokens do tema escuro, aplicados em dois seletores (preferencia do sistema e
#: escolha explicita no alternador). Ficam numa constante propria e sao
#: injetados nos dois lugares por `replace`: repetir a lista de cores no CSS e o
#: caminho classico para os dois seletores divergirem em silencio.
_DARK_TOKENS = """
  color-scheme: dark;
  --canvas: #0D1117;
  --surface: #171C24;
  --surface-2: #1E242E;
  --ink: #E6EDF3;
  --ink-soft: #C7D0DA;
  --muted: #98A2B3;
  --line: #2A313C;
  --line-soft: #232A34;
  --accent: #4FD1C0;
  --accent-soft: #14312E;
  --bad: #F59356;
  --bad-soft: #2E1D14;
  --bad-line: #4A2E1D;
  --warn: #E0B65C;
  --context-bg: #241E14;
  --context-line: #3D3320;
  --chip-bg: #212836;
  --shadow: 0 1px 2px rgba(0,0,0,.4);
"""

#: Tokens claros, repetidos dentro de `@media print`: a impressao ignora o tema
#: escolhido na tela -- fundo escuro em papel gasta tinta e apaga o texto.
_LIGHT_TOKENS = """
  color-scheme: light;
  --canvas: #FFFFFF;
  --surface: #FFFFFF;
  --surface-2: #F7F8FA;
  --ink: #111827;
  --ink-soft: #374151;
  --muted: #5B6675;
  --line: #D9DEE4;
  --line-soft: #EDF0F3;
  --accent: #0F6B62;
  --accent-soft: #E6F1EF;
  --bad: #A8451A;
  --bad-soft: #FBEDE4;
  --bad-line: #F0CBB2;
  --warn: #8A6318;
  --context-bg: #FCF8F0;
  --context-line: #EDE0C8;
  --chip-bg: #F1F3F5;
  --shadow: none;
"""

_HTML_STYLE = """
:root {
  color-scheme: light;
  --canvas: #F1F3F5;
  --surface: #FFFFFF;
  --surface-2: #F7F8FA;
  --ink: #111827;
  --ink-soft: #374151;
  --muted: #6B7280;
  --line: #E3E7EB;
  --line-soft: #EDF0F3;
  --accent: #0F6B62;
  --accent-soft: #E6F1EF;
  --bad: #C2521B;
  --bad-soft: #FBEDE4;
  --bad-line: #F0CBB2;
  --warn: #B5822A;
  --context-bg: #FCF8F0;
  --context-line: #EDE0C8;
  --chip-bg: #F1F3F5;
  --shadow: 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06);
  --radius: 14px;
  --radius-sm: 10px;
  --topbar-h: 62px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {__DARK__} }
:root[data-theme="dark"] {__DARK__}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  color: var(--ink);
  background: var(--canvas);
  margin: 0;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); }

/* ---- Barra fixa -------------------------------------------------------- */
.topbar {
  position: sticky;
  top: 0;
  z-index: 50;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.topbar-inner {
  max-width: 1240px;
  margin: 0 auto;
  padding: .55rem 1.25rem;
  display: flex;
  align-items: center;
  gap: 1.25rem;
  min-height: var(--topbar-h);
}
.brand { display: flex; align-items: center; gap: .65rem; text-decoration: none; color: inherit; }
.brand-mark {
  width: 34px; height: 34px; border-radius: 9px;
  background: var(--accent); color: #fff;
  display: grid; place-items: center;
  font-size: .6rem; font-weight: 800; letter-spacing: .04em;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; }
.brand-text b { font-size: .92rem; font-weight: 700; }
.brand-text small { font-size: .72rem; color: var(--muted); }
.navlinks { display: flex; gap: .15rem; flex: 1; flex-wrap: wrap; }
.nav-link {
  font-size: .82rem;
  font-weight: 600;
  color: var(--muted);
  text-decoration: none;
  padding: .4rem .7rem;
  border-radius: 999px;
  white-space: nowrap;
}
.nav-link:hover { color: var(--ink); background: var(--surface-2); }
.nav-link.is-active { color: var(--accent); background: var(--accent-soft); }
.topbar-actions { display: flex; align-items: center; gap: .5rem; }
.btn {
  display: inline-flex; align-items: center; gap: .4rem;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  border-radius: 999px;
  padding: .45rem 1rem;
  font-size: .8rem;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
}
.btn:hover { border-color: var(--accent); color: var(--accent); }
.icon-btn {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  border-radius: 999px;
  width: 34px; height: 34px;
  display: grid; place-items: center;
  cursor: pointer;
  font-size: .95rem;
  line-height: 1;
  font-family: inherit;
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }

/* ---- Grade da pagina --------------------------------------------------- */
.report-shell { max-width: 1240px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
section[id], details[id] { scroll-margin-top: calc(var(--topbar-h) + 14px); }
.eyebrow {
  text-transform: uppercase;
  letter-spacing: .09em;
  font-size: .7rem;
  font-weight: 700;
  color: var(--accent);
  margin: 0 0 .35rem;
}
.block {
  margin-top: 1.1rem;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.5rem 1.6rem;
}
.block-bare { background: none; border: 0; box-shadow: none; padding: 0; margin-top: 1.9rem; }
.block-headline {
  font-size: 1.24rem; font-weight: 700; margin: 0 0 .25rem; letter-spacing: -.01em;
}
.panel-head { margin-bottom: 1rem; }

/* ---- Capa -------------------------------------------------------------- */
.report-header {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.6rem 1.7rem;
  margin-top: 1.5rem;
}
.report-header h1 {
  font-size: 1.72rem; margin: 0 0 1rem; letter-spacing: -.02em; line-height: 1.22;
}
.delivery-label {
  margin: 0 0 .55rem;
  font-size: .74rem;
  font-weight: 700;
  letter-spacing: .04em;
  color: var(--ink);
}
.report-footer .delivery-label { margin: 0 0 .5rem; color: var(--ink); }
.hero-chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0; padding: 0; list-style: none; }
.chip {
  display: inline-flex; align-items: baseline; gap: .4rem;
  background: var(--chip-bg);
  border: 1px solid transparent;
  border-radius: 999px;
  padding: .32rem .8rem;
  font-size: .78rem;
  color: var(--ink-soft);
  white-space: nowrap;
}
.chip b { font-weight: 700; color: var(--ink); }
.chip-key {
  color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
}
.chip-accent { background: var(--accent-soft); color: var(--accent); }
.chip-accent .chip-key, .chip-accent b { color: var(--accent); }
.chip-warn { background: var(--bad-soft); border-color: var(--bad-line); color: var(--bad); }

/* ---- Resumo executivo e status ----------------------------------------- */
.exec-summary { background: var(--accent-soft); border-color: transparent; }
.exec-summary .block-headline { color: var(--ink); }
.exec-list { list-style: none; margin: 1rem 0 0; padding: 0; display: grid; gap: .55rem; }
.exec-list li { display: flex; gap: .6rem; align-items: baseline; font-size: .94rem; }
.tag {
  font-size: .62rem;
  font-weight: 800;
  letter-spacing: .06em;
  padding: .14rem .45rem;
  border-radius: 5px;
  flex: 0 0 auto;
}
.tag-dado { background: var(--accent); color: #fff; }
.tag-contexto { background: var(--warn); color: #fff; }

.status-strip {
  margin-top: 1.1rem;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 5px solid var(--muted);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.1rem 1.35rem;
}
.status-normal { border-left-color: var(--accent); }
.status-warn { border-left-color: var(--warn); }
.status-bad { border-left-color: var(--bad); }
.status-head { display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }
.status-badge {
  font-size: .66rem;
  font-weight: 800;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--ink);
}
.status-summary { margin: 0; font-size: .9rem; color: var(--ink); }
.status-list { margin: .6rem 0 0; padding-left: 1.1rem; font-size: .86rem; }
.status-history { margin: .55rem 0 0; font-size: .78rem; color: var(--muted); }

/* ---- Indicadores ------------------------------------------------------- */
.context-label { margin: 1.4rem 0 .6rem; font-size: .78rem; color: var(--muted); }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: .9rem; margin-top: .9rem; }
.kpi-grid-context { grid-template-columns: repeat(2, 1fr); margin-top: 0; }
.kpi-card {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.15rem 1.25rem;
  background: var(--surface);
}
.kpi-compact { background: var(--surface-2); box-shadow: none; padding: .95rem 1.1rem; }
.kpi-compact .kpi-value { font-size: 1.3rem; }
.kpi-compact .kpi-label { font-size: .7rem; }
.kpi-empty { background: var(--surface-2); box-shadow: none; }
.kpi-label {
  font-size: .71rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: .5rem;
}
.kpi-value { font-size: 2rem; font-weight: 800; letter-spacing: -.025em; line-height: 1.1; }
.kpi-unit {
  display: block;
  font-size: .72rem;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0;
  margin-top: .15rem;
}
.kpi-muted { color: var(--muted); font-size: 1.1rem; font-weight: 700; }
.kpi-comparison { font-size: .8rem; color: var(--muted); margin-top: .4rem; min-height: 1.1em; }
.kpi-note {
  font-size: .73rem;
  color: var(--muted);
  margin-top: .6rem;
  border-top: 1px solid var(--line-soft);
  padding-top: .55rem;
}
.kpi-warning { font-size: .73rem; color: var(--bad); margin-top: .4rem; }
.kpi-trend { font-weight: 800; }
.trend-bad { color: var(--bad); }
.trend-good { color: var(--accent); }
.trend-neutral { color: var(--muted); }

/* ---- Comparador de indicadores ----------------------------------------- */
.cmp-wrap { margin-top: 1rem; overflow-x: auto; }
.cmp-wrap table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .86rem; }
.cmp-wrap thead th {
  text-align: left;
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
  padding: 0 .7rem .5rem;
  white-space: nowrap;
  vertical-align: bottom;
}
.cmp-wrap thead th.cmp-num { text-align: right; }
.cmp-sortable button {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  color: inherit;
  letter-spacing: inherit;
  text-transform: inherit;
  cursor: pointer;
}
.cmp-sortable button::after { content: " ↕"; opacity: .35; }
.cmp-sortable[aria-sort="ascending"] button,
.cmp-sortable[aria-sort="descending"] button { color: var(--accent); }
.cmp-sortable[aria-sort="ascending"] button::after { content: " ↑"; opacity: 1; }
.cmp-sortable[aria-sort="descending"] button::after { content: " ↓"; opacity: 1; }

.cmp-row { cursor: pointer; }
.cmp-row td {
  padding: .7rem;
  border-bottom: 1px solid var(--line-soft);
  vertical-align: top;
}
.cmp-row:hover td { background: var(--surface-2); }
.cmp-row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.cmp-num { text-align: right; font-variant-numeric: tabular-nums; }
.cmp-value { font-weight: 700; font-size: 1rem; white-space: nowrap; }
.cmp-unit { display: block; font-size: .66rem; font-weight: 600; color: var(--muted); }
.cmp-null { color: var(--muted); font-weight: 400; }

.cmp-name { min-width: 190px; display: flex; align-items: flex-start; gap: .55rem; }
.cmp-dot {
  flex: 0 0 auto;
  width: 9px; height: 9px; border-radius: 50%;
  margin-top: .38rem; background: var(--muted);
}
.cmp-dot-0 { background: #0F6B62; }
.cmp-dot-1 { background: #C2521B; }
.cmp-dot-2 { background: #3B6FB6; }
.cmp-dot-3 { background: #7A5AA6; }
.cmp-dot-4 { background: #4B7B2E; }
.cmp-dot-5 { background: #B5822A; }
.cmp-dot-6 { background: #A33B5E; }
.cmp-name-text { display: inline-block; font-weight: 600; }
.cmp-name-text small {
  display: block;
  font-size: .66rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
}
.cmp-bar {
  display: block;
  height: 4px;
  margin-top: .35rem;
  border-radius: 999px;
  background: var(--line-soft);
  overflow: hidden;
}
.cmp-bar i { display: block; height: 100%; background: var(--accent); opacity: .75; }
.cmp-tag {
  display: inline-block;
  font-size: .66rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  padding: .18rem .5rem;
  border-radius: 999px;
}
.cmp-tag-ok { background: var(--accent-soft); color: var(--accent); }
.cmp-tag-off { background: var(--bad-soft); color: var(--bad); }

.cmp-detail td { padding: 0 .7rem 1.1rem; border-bottom: 1px solid var(--line-soft); }
.cmp-detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: .9rem 1.5rem;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
}
.cmp-detail-wide { grid-column: 1 / -1; }
.cmp-detail-key {
  display: block;
  font-size: .66rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
  margin-bottom: .2rem;
}
.cmp-detail-grid p { margin: 0; font-size: .84rem; }
.cmp-detail-grid ul { margin: .2rem 0 0; padding-left: 1.1rem; font-size: .84rem; }
.cmp-detail-grid li { margin-bottom: .25rem; }
.cmp-warning { color: var(--bad); font-size: .82rem; margin-top: .5rem !important; }

/* ---- Graficos ---------------------------------------------------------- */
.chart-wrap {
  margin-top: 1rem;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: var(--surface);
  padding: .75rem .9rem .9rem;
}
.chart-interactive { width: 100%; min-height: 370px; }
.chart-print-only { display: none; width: 100%; border-radius: 6px; }
.chart-empty { color: var(--muted); font-size: .9rem; }

.insights-box {
  margin-top: 1rem;
  padding: 1rem 1.2rem;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}
.insights-title {
  font-weight: 700;
  font-size: .71rem;
  margin: 0 0 .5rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .06em;
}
.insights-box ul { margin: 0; padding-left: 1.1rem; font-size: .88rem; color: var(--ink); }
.insights-box li { margin-bottom: .3rem; }

/* ---- Contexto externo --------------------------------------------------- */
.news-intro { color: var(--muted); font-size: .87rem; margin: .1rem 0 0; }
.news-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: .9rem;
  margin-top: 1.2rem;
}
.news-card {
  background: var(--context-bg);
  border: 1px solid var(--context-line);
  border-radius: var(--radius-sm);
  padding: 1rem 1.1rem;
}
.news-title { font-weight: 700; font-size: .89rem; margin: 0 0 .5rem; }
.news-meta { font-size: .75rem; color: var(--muted); margin: 0 0 .6rem; }
.news-link { font-size: .78rem; font-weight: 700; color: var(--accent); text-decoration: none; }
.news-link-disabled { color: var(--muted); }
.news-empty { color: var(--muted); font-size: .9rem; }

/* ---- Interpretacao e transparencia -------------------------------------- */
.interpretation {
  background: var(--surface-2);
  border-left: 4px solid var(--accent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  padding: 1.2rem 1.4rem;
  margin-top: 1.2rem;
}
.interpretation p { margin: 0 0 .8rem; }
.interpretation p:last-child { margin-bottom: 0; }
.interpretation h4 { font-size: .92rem; color: var(--accent); margin: 1.1rem 0 .4rem; }
.interpretation h4:first-child { margin-top: 0; }
.interpretation ul { margin: 0 0 .8rem; padding-left: 1.2rem; }
.interpretation li { margin-bottom: .3rem; }
.interpretation-note {
  font-size: .78rem;
  color: var(--muted);
  margin-bottom: 1rem !important;
  font-style: italic;
}

.callout {
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
  margin-top: 1rem;
  background: var(--bad-soft);
  border: 1px solid var(--bad-line);
}
.callout-neutral { background: var(--surface-2); border-color: var(--line); }
.callout-bad { background: var(--bad-soft); border-color: var(--bad-line); }
.callout-title { font-weight: 700; font-size: .85rem; margin: 0 0 .35rem; }
.callout p { font-size: .87rem; margin: 0; }
.callout ul { margin: .3rem 0 0; padding-left: 1.1rem; font-size: .85rem; }

.methodology {
  margin-top: 1.5rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
}
.methodology summary { cursor: pointer; font-weight: 700; font-size: .9rem; }
.methodology table { width: 100%; border-collapse: collapse; margin-top: .9rem; font-size: .85rem; }
.methodology table td {
  padding: .4rem 0; border-bottom: 1px solid var(--line-soft); vertical-align: top;
}
.meta-key { color: var(--muted); width: 40%; }

/* ---- Anexo tecnico ------------------------------------------------------ */
.technical-appendix { margin-top: 1.1rem; }
.technical-appendix summary {
  cursor: pointer; font-weight: 700; font-size: 1rem; color: var(--ink);
}
.technical-appendix-body { margin-top: 1.2rem; }
.technical-appendix-body h1 { display: none; }
.technical-appendix-body h2 {
  font-size: 1.04rem;
  color: var(--ink);
  border-bottom: 1px solid var(--line);
  padding-bottom: .35rem;
  margin-top: 2rem;
}
.technical-appendix-body h3 { font-size: .95rem; color: var(--accent); margin-top: 1.5rem; }
.technical-appendix-body table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  margin: .9rem 0;
  display: block;
  overflow-x: auto;
  font-size: .84rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
.technical-appendix-body th, .technical-appendix-body td {
  border-bottom: 1px solid var(--line-soft);
  padding: .5rem .7rem;
  text-align: left;
  vertical-align: top;
}
.technical-appendix-body tr:last-child td { border-bottom: 0; }
.technical-appendix-body th {
  background: var(--surface-2);
  color: var(--muted);
  font-size: .72rem;
  text-transform: uppercase;
  letter-spacing: .05em;
  white-space: nowrap;
}
.technical-appendix-body tbody tr:hover td { background: var(--surface-2); }
.technical-appendix-body blockquote {
  border-left: 3px solid var(--accent);
  margin: .8rem 0;
  padding: .5rem .9rem;
  background: var(--surface-2);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.technical-appendix-body img {
  max-width: 100%; border: 1px solid var(--line); border-radius: 8px;
}
.technical-appendix-body code {
  background: var(--surface-2); padding: .1rem .3rem; border-radius: 4px; font-size: .88em;
}

.report-footer {
  margin-top: 1.5rem;
  padding: 1.2rem 1.6rem;
  border-top: 1px solid var(--line);
  font-size: .78rem;
  color: var(--muted);
}
.report-footer .disclaimer { margin-top: .4rem; font-style: italic; }

@media (max-width: 1080px) { .navlinks { display: none; } }
@media (max-width: 860px) {
  .kpi-grid, .kpi-grid-context { grid-template-columns: repeat(2, 1fr); }
  .block { padding: 1.2rem 1.1rem; }
  .report-header { padding: 1.3rem 1.2rem; }
}
@media print {
  :root, :root[data-theme="dark"], :root[data-theme="light"] {__LIGHT__}
  .no-print, .topbar { display: none !important; }
  body { background: #fff; }
  .report-shell { max-width: none; padding: 0; }
  .chart-interactive { display: none !important; }
  .chart-print-only { display: block !important; }
  /* So o que fica ilegivel partido ao meio e que nao pode quebrar. Aplicar
     isso a `.block` inteiro empurrava a secao seguinte para a proxima folha e
     deixava um terco de pagina em branco a cada corte. */
  .report-header, .status-strip, .chart-wrap, .insights-box { page-break-inside: avoid; }
  /* Titulo nunca fica sozinho no pe da folha nem partido ao meio. O
     `break-after` sozinho nao basta: quando o que vem depois e uma caixa
     indivisivel que nao cabe no resto da folha, o navegador ignora a regra --
     por isso os dois paineis de grafico sao indivisiveis por inteiro. */
  .panel-head, .block-headline, .eyebrow { page-break-after: avoid; break-after: avoid; }
  .block-headline { break-inside: avoid; }
  #series, #series-mensal { page-break-inside: avoid; }
  .callout, .kpi-card, .news-card, .cmp-row, .cmp-detail { break-inside: avoid; }
  .cmp-sortable button::after { content: "" !important; }
  .cmp-wrap { overflow-x: visible; }
  .cmp-hint { display: none; }
}
""".replace("__DARK__", _DARK_TOKENS).replace("__LIGHT__", _LIGHT_TOKENS)

#: Roda no `<head>`, antes do primeiro pixel: aplica o tema salvo na visita
#: anterior. Depois da pagina pintada, a troca apareceria como um flash branco.
_THEME_BOOTSTRAP = """
<script>
(function () {
  try {
    var saved = localStorage.getItem("srag-theme");
    if (saved === "dark" || saved === "light") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (error) {
    /* armazenamento bloqueado: segue a preferencia do sistema */
  }
})();
</script>
"""

#: Script da pagina: tema, repintura dos graficos, navegacao e o fallback de
#: rede. Vai no fim do `<body>` -- quando ele roda, os componentes de grafico
#: ja se registraram em `window.__sragCharts`.
_PAGE_SCRIPT = """
<script>
(function () {
  var root = document.documentElement;
  var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function currentTheme() {
    return root.getAttribute("data-theme") || (query && query.matches ? "dark" : "light");
  }

  // Um SVG do Plotly nao herda cor de CSS: cada grafico registra o ajuste das
  // duas paletas e a troca de tema reaplica o do tema corrente.
  window.__sragPaintCharts = function () {
    if (typeof Plotly === "undefined") { return; }
    var registry = window.__sragCharts || {};
    var theme = currentTheme();
    Object.keys(registry).forEach(function (id) {
      var element = document.getElementById(id);
      var spec = registry[id][theme];
      if (!element || !element.data || !spec) { return; }
      Plotly.relayout(element, spec.layout);
      (spec.traces || []).forEach(function (patch) {
        // Cada valor vai embrulhado numa lista de um item: e assim que o
        // restyle diz "este valor e do trace tal". Sem o embrulho, uma cor que
        // ja e lista -- as barras do grafico mensal, uma cor por mes -- seria
        // lida como uma cor por TRACE, e o mes de pico perdia o destaque.
        var update = {};
        Object.keys(patch[1]).forEach(function (key) { update[key] = [patch[1][key]]; });
        Plotly.restyle(element, update, [patch[0]]);
      });
    });
  };

  function syncToggle() {
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    var dark = currentTheme() === "dark";
    button.innerHTML = dark ? "&#x2600;" : "&#x263E;";
    button.setAttribute("aria-label", dark ? "Usar tema claro" : "Usar tema escuro");
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    try { localStorage.setItem("srag-theme", theme); } catch (error) { /* sem storage */ }
    syncToggle();
    window.__sragPaintCharts();
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) { return; }
    if (target.closest("#theme-toggle")) {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
      return;
    }
    if (target.closest("#print-button")) {
      window.print();
      return;
    }
    // O anexo tecnico e recolhido: o link de rastreabilidade tem de abri-lo,
    // senao a ancora leva para um titulo fechado.
    if (target.closest('a[href="#anexo-tecnico"]')) {
      var appendix = document.getElementById("anexo-tecnico");
      if (appendix) { appendix.open = true; }
    }
  });

  if (query && query.addEventListener) {
    query.addEventListener("change", function () {
      if (!root.getAttribute("data-theme")) { syncToggle(); window.__sragPaintCharts(); }
    });
  }

  // Tabela comparativa: ordenacao por coluna e detalhe por linha.
  //
  // Cada linha de dado vem seguida da sua linha de detalhe; ordenar move o par
  // junto, senao o detalhe de um indicador apareceria sob outro. Celula sem
  // valor (`data-sort` vazio) vai sempre para o fim, nas duas direcoes: um
  // indicador indisponivel nao "vale zero", ele nao tem valor.
  (function () {
    var table = document.querySelector(".cmp-wrap table");
    if (!table) { return; }
    var body = table.tBodies[0];

    function pares() {
      return Array.prototype.map.call(body.querySelectorAll(".cmp-row"), function (linha) {
        return [linha, body.querySelector('[data-detail-for="' + linha.dataset.key + '"]')];
      });
    }

    function ordenar(indice, tipo, crescente) {
      var linhas = pares();
      linhas.sort(function (a, b) {
        var ta = a[0].cells[indice].getAttribute("data-sort") || "";
        var tb = b[0].cells[indice].getAttribute("data-sort") || "";
        if (ta === "" || tb === "") { return ta === tb ? 0 : (ta === "" ? 1 : -1); }
        if (tipo === "number") {
          return (crescente ? 1 : -1) * (parseFloat(ta) - parseFloat(tb));
        }
        return (crescente ? 1 : -1) * ta.localeCompare(tb, "pt-BR");
      });
      linhas.forEach(function (par) {
        body.appendChild(par[0]);
        if (par[1]) { body.appendChild(par[1]); }
      });
    }

    Array.prototype.forEach.call(table.querySelectorAll("th.cmp-sortable"), function (th, indice) {
      th.addEventListener("click", function () {
        var crescente = th.getAttribute("aria-sort") !== "ascending";
        Array.prototype.forEach.call(table.querySelectorAll("th"), function (outro) {
          outro.setAttribute("aria-sort", "none");
        });
        th.setAttribute("aria-sort", crescente ? "ascending" : "descending");
        ordenar(indice, th.getAttribute("data-type"), crescente);
      });
    });

    function alternar(linha) {
      var detalhe = body.querySelector('[data-detail-for="' + linha.dataset.key + '"]');
      if (!detalhe) { return; }
      var aberto = !detalhe.hidden;
      detalhe.hidden = aberto;
      linha.setAttribute("aria-expanded", String(!aberto));
    }

    body.addEventListener("click", function (evento) {
      var linha = evento.target.closest ? evento.target.closest(".cmp-row") : null;
      if (linha) { alternar(linha); }
    });
    body.addEventListener("keydown", function (evento) {
      if (evento.key !== "Enter" && evento.key !== " ") { return; }
      var linha = evento.target.closest ? evento.target.closest(".cmp-row") : null;
      if (linha) { evento.preventDefault(); alternar(linha); }
    });
  })();

  var links = Array.prototype.slice.call(document.querySelectorAll(".nav-link"));
  var targets = links.map(function (link) {
    return document.querySelector(link.getAttribute("href"));
  });
  if (window.IntersectionObserver && targets.length) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        var index = targets.indexOf(entry.target);
        links.forEach(function (link, position) {
          link.classList.toggle("is-active", position === index);
        });
      });
    }, { rootMargin: "-80px 0px -60% 0px", threshold: 0 });
    targets.forEach(function (target) { if (target) { observer.observe(target); } });
  }

  syncToggle();

  window.addEventListener("load", function () {
    if (typeof Plotly === "undefined") {
      // Sem rede: o PNG estatico de cada grafico assume o lugar do interativo.
      document.querySelectorAll(".chart-interactive").forEach(function (element) {
        element.style.display = "none";
      });
      document.querySelectorAll(".chart-print-only").forEach(function (element) {
        element.style.display = "block";
      });
      return;
    }
    window.__sragPaintCharts();
  });
})();
</script>
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


def _html_document(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>{_HTML_STYLE}</style>
{_THEME_BOOTSTRAP}
</head>
<body>
{body}
{_PAGE_SCRIPT}
</body>
</html>
"""


def _markdown_to_html(text: str) -> str:
    """Conversor Markdown -> HTML restrito aos elementos usados no relatorio."""
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

        if in_table:
            close_blocks()

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
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _render_safe_image, escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _render_safe_link, escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def _safe_url(raw: str) -> str | None:
    """Aceita apenas URLs HTTP(S) ou caminhos relativos sem esquema.

    Titulos e links de noticias sao conteudo externo. Esta validacao impede que
    um link malicioso vire `javascript:` ou outro esquema executavel no HTML
    gerado, mesmo se uma fonte upstream for comprometida.
    """
    from urllib.parse import urlparse

    value = html.unescape(raw).strip()
    if not value or any(character in value for character in ("\x00", "\r", "\n")):
        return None

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    if not parsed.scheme and not parsed.netloc and not value.startswith("//"):
        return value
    return None


def _render_safe_link(match: Any) -> str:
    label, raw_url = match.group(1), match.group(2)
    safe = _safe_url(raw_url)
    if safe is None:
        return label
    return f'<a href="{html.escape(safe, quote=True)}">{label}</a>'


def _render_safe_image(match: Any) -> str:
    alt, raw_url = match.group(1), match.group(2)
    safe = _safe_url(raw_url)
    if safe is None:
        return alt
    return f'<img src="{html.escape(safe, quote=True)}" alt="{alt}">'


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
