"""Nos do grafo de orquestracao.

Cada no tem uma responsabilidade unica, registra seu proprio evento de auditoria
e devolve apenas o pedaco do estado que alterou. Os nos orquestram -- quem
calcula sao as tools deterministicas.
"""

from __future__ import annotations

from typing import Any

from src.agent.llm import Interpreter
from src.agent.state import SRAGState
from src.guardrails.input_guard import validate_request as guard_request
from src.guardrails.output_guard import EvidenceSet, build_evidence, validate_output
from src.guardrails.policies import ALL_POLICIES, DISCLAIMER, UNCERTAINTY_STATEMENT
from src.observability.audit import STATUS_BLOCKED, STATUS_DEGRADED, AuditTrail
from src.observability.logging_config import get_logger
from src.tools.registry import call_tool, openai_tool_specs

logger = get_logger(__name__)

#: Tools que o relatorio final exige, independentemente do plano do modelo.
#: O planejamento do LLM pode acrescentar tools, nunca suprimir uma obrigatoria.
MANDATORY_METRIC_TOOLS: tuple[str, ...] = (
    "get_case_growth_rate",
    "get_mortality_rate",
    "get_icu_metrics",
    "get_vaccination_metrics",
    "get_notification_completeness",
)

MANDATORY_SERIES_TOOLS: tuple[str, ...] = ("get_daily_cases", "get_monthly_cases")
MANDATORY_CHART_TOOLS: tuple[str, ...] = (
    "render_daily_cases_chart",
    "render_monthly_cases_chart",
)

#: Consulta usada para recuperar contexto externo no Vector DB.
NEWS_TOPIC = (
    "SRAG sindrome respiratoria aguda grave surto influenza covid-19 "
    "virus respiratorio pressao sobre hospitais e UTIs"
)


class GraphContext:
    """Dependencias injetadas nos nos do grafo.

    Mantem a trilha de auditoria e o interpretador fora do estado do LangGraph,
    que deve conter apenas dados serializaveis.
    """

    def __init__(self, trail: AuditTrail, interpreter: Interpreter) -> None:
        self.trail = trail
        self.interpreter = interpreter
        #: Conjunto de evidencias montado pelo no `validate_evidence`. Vive aqui,
        #: e nao no estado do grafo, porque nao e serializavel.
        self.evidence: EvidenceSet | None = None


def _metric_params(state: SRAGState) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if state.get("uf"):
        params["uf"] = state["uf"]
    if state.get("classification") is not None:
        params["classification"] = state["classification"]
    return params


def make_validate_request(context: GraphContext):
    """No 1 -- valida a solicitacao e monta o plano de execucao."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        with trail.step(node="validate_request", parameters=_metric_params(state)) as audit:
            validation = guard_request(
                state["request"],
                uf=state.get("uf"),
                classification=state.get("classification"),
            )
            audit["summary"] = (
                "solicitacao aceita" if validation.allowed
                else f"solicitacao recusada por {validation.blocked_by}"
            )
            if not validation.allowed:
                audit["status"] = STATUS_BLOCKED

        if not validation.allowed:
            return {
                "validation": validation.to_dict(),
                "errors": [validation.reason or "solicitacao recusada"],
            }

        with trail.step(node="plan_analysis") as audit:
            plan = context.interpreter.plan(state["request"], openai_tool_specs())
            selected = set(plan.get("selected_tools") or [])
            plan["effective_tools"] = sorted(
                selected
                | set(MANDATORY_METRIC_TOOLS)
                | set(MANDATORY_SERIES_TOOLS)
                | set(MANDATORY_CHART_TOOLS)
                | {"search_srag_news"}
            )
            plan["mandatory_note"] = (
                "O plano do modelo e unido ao conjunto obrigatorio do relatorio; "
                "tools exigidas pela entrega nunca sao suprimidas pelo planejamento."
            )
            audit["summary"] = f"{len(plan['effective_tools'])} tools no plano efetivo"

        return {
            "validation": validation.to_dict(),
            "plan": plan,
            "warnings": list(validation.warnings),
        }

    return node


def make_collect_metrics(context: GraphContext):
    """No 2 -- executa as tools dos indicadores epidemiologicos."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        params = _metric_params(state)
        metrics: dict[str, dict[str, Any]] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        with trail.step(node="collect_epidemiological_metrics", parameters=params) as audit:
            for tool_name in MANDATORY_METRIC_TOOLS:
                result = call_tool(tool_name, params, trail=trail)
                if "error" in result:
                    errors.append(result["error"])
                    continue
                # Tools de diagnostico (ex.: completude da notificacao) nao
                # devolvem envelope de metrica e nao sao indicadores do
                # relatorio: ficam num campo proprio do estado.
                if "metric" in result:
                    metrics[result["metric"]] = result
                else:
                    diagnostics[tool_name] = result

            audit["summary"] = (
                f"{len(metrics)} indicadores e {len(diagnostics)} diagnosticos "
                f"calculados, {len(errors)} falhas"
            )
            if errors:
                audit["status"] = STATUS_DEGRADED

        return {"metrics": metrics, "diagnostics": diagnostics, "errors": errors}

    return node


def make_collect_series(context: GraphContext):
    """No 3 -- executa as tools de series temporais e de graficos."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        params = _metric_params(state)
        series: dict[str, dict[str, Any]] = {}
        charts: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        with trail.step(node="collect_time_series", parameters=params) as audit:
            for tool_name in MANDATORY_SERIES_TOOLS:
                result = call_tool(tool_name, params, trail=trail)
                if "error" in result:
                    errors.append(result["error"])
                    continue
                series[result.get("metric", tool_name)] = result

            for tool_name in MANDATORY_CHART_TOOLS:
                result = call_tool(tool_name, params, trail=trail)
                if "error" in result:
                    errors.append(result["error"])
                    continue
                # A serie ja esta em `series`; guardar so o metadado do arquivo
                # evita duplicar o payload no estado e no relatorio.
                charts[result["chart"]] = {
                    key: value for key, value in result.items() if key != "series"
                }

            audit["summary"] = (
                f"{len(series)} series e {len(charts)} graficos gerados, "
                f"{len(errors)} falhas"
            )
            if errors:
                audit["status"] = STATUS_DEGRADED

        return {"series": series, "charts": charts, "errors": errors}

    return node


def make_search_news(context: GraphContext):
    """No 4 -- recupera contexto externo no Vector DB de noticias."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        with trail.step(node="search_external_news") as audit:
            result = call_tool(
                "search_srag_news",
                {"query": NEWS_TOPIC, "top_k": 8},
                trail=trail,
            )
            if "error" in result:
                audit["status"] = STATUS_DEGRADED
                audit["summary"] = "busca de noticias indisponivel"
                return {
                    "external_context": {
                        "articles": [],
                        "unavailable_reason": result["error"],
                    },
                    "warnings": [
                        "Contexto externo indisponivel nesta execucao: "
                        f"{result['error']}"
                    ],
                }

            audit["summary"] = f"{result['total']} noticias recuperadas"
            if result["total"] == 0:
                audit["status"] = STATUS_DEGRADED

        warnings = []
        if result.get("unavailable_reason"):
            warnings.append(result["unavailable_reason"])
        return {"external_context": result, "warnings": warnings}

    return node


def make_validate_evidence(context: GraphContext):
    """No 5 -- consolida o conjunto de valores que o relatorio pode citar."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        with trail.step(node="validate_evidence") as audit:
            payloads: dict[str, Any] = {
                **state.get("metrics", {}),
                **state.get("diagnostics", {}),
                **state.get("series", {}),
            }
            evidence = build_evidence(payloads)
            context.evidence = evidence

            unavailable = [
                {
                    "metric": key,
                    "reason": metric.get("unavailable_reason"),
                }
                for key, metric in state.get("metrics", {}).items()
                if metric.get("value") is None
            ]

            summary = {
                "valores_lastreados": len(evidence.values),
                "indicadores_calculados": len(state.get("metrics", {})),
                "indicadores_indisponiveis": unavailable,
                "series_coletadas": sorted(state.get("series", {})),
                "noticias_no_contexto": len(
                    state.get("external_context", {}).get("articles") or []
                ),
                "declaracao_de_incerteza": UNCERTAINTY_STATEMENT,
            }
            audit["summary"] = (
                f"{summary['valores_lastreados']} valores lastreados; "
                f"{len(unavailable)} indicadores indisponiveis"
            )

        return {"evidence": summary}

    return node


def make_generate_interpretation(context: GraphContext):
    """No 6 -- produz a interpretacao e a submete aos guardrails de saida."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        interpreter = context.interpreter

        llm_context = {
            "solicitacao": state["request"],
            "recorte": state.get("validation", {}).get("filters"),
            "indicadores": state.get("metrics", {}),
            "diagnosticos": state.get("diagnostics", {}),
            "series": _compact_series(state.get("series", {})),
            "contexto_externo": state.get("external_context", {}),
            "avisos": state.get("warnings", []),
        }

        with trail.step(
            node="generate_interpretation",
            parameters={"interpreter": interpreter.source},
            source=interpreter.source,
        ) as audit:
            try:
                text = interpreter.interpret(llm_context)
                source = interpreter.source
            except Exception as exc:  # degradacao para a via deterministica
                from src.agent.llm import DeterministicNarrator

                logger.warning(
                    "interpretacao pelo modelo falhou; usando via deterministica",
                    extra={"motivo": str(exc)},
                )
                narrator = DeterministicNarrator()
                text = narrator.interpret(llm_context)
                source = f"{narrator.source} (fallback: {type(exc).__name__})"
                audit["status"] = STATUS_DEGRADED

            audit["summary"] = f"interpretacao gerada por {source} ({len(text)} caracteres)"

        evidence = context.evidence
        if evidence is None:
            evidence = build_evidence(
                {
                    **state.get("metrics", {}),
                    **state.get("diagnostics", {}),
                    **state.get("series", {}),
                }
            )

        with trail.step(node="apply_output_guardrails") as audit:
            validation = validate_output(text, evidence)
            audit["summary"] = (
                "saida aprovada" if validation.allowed
                else f"saida bloqueada por {validation.blocked_by}"
            )
            if not validation.allowed:
                audit["status"] = STATUS_BLOCKED

        guardrail_report = {
            "politicas_ativas": [policy.to_dict() for policy in ALL_POLICIES],
            "resultado": validation.to_dict(),
            "disclaimer": DISCLAIMER,
        }

        if validation.allowed:
            return {
                "interpretation": text,
                "interpretation_source": source,
                "guardrail_report": guardrail_report,
            }

        # Saida reprovada: o texto do modelo e descartado e a via deterministica
        # -- que so escreve valores vindos das tools -- assume a redacao.
        from src.agent.llm import DeterministicNarrator

        fallback_text = DeterministicNarrator().interpret(llm_context)
        fallback_validation = validate_output(fallback_text, evidence)
        guardrail_report["fallback"] = {
            "motivo": "interpretacao original reprovada pelos guardrails de saida",
            "resultado": fallback_validation.to_dict(),
        }

        return {
            "interpretation": fallback_text,
            "interpretation_source": f"deterministic-template (substituiu {source})",
            "guardrail_report": guardrail_report,
            "warnings": [
                "A interpretacao gerada pelo modelo foi bloqueada pelos guardrails "
                f"({', '.join(validation.blocked_by)}) e substituida pela redacao "
                "deterministica."
            ],
        }

    return node


def _compact_series(series: dict[str, Any]) -> dict[str, Any]:
    """Reduz as series antes de enviar ao modelo.

    O modelo precisa da forma da curva e dos agregados, nao de cada ponto. Enviar
    a serie inteira gastaria contexto e aumentaria a chance de o modelo citar um
    ponto isolado sem necessidade.
    """
    compact: dict[str, Any] = {}
    for key, payload in series.items():
        points = payload.get("points", [])
        compact[key] = {
            "period": payload.get("period"),
            "summary": payload.get("summary"),
            "primeiros_pontos": points[:3],
            "ultimos_pontos": points[-3:],
            "limitations": payload.get("limitations"),
        }
    return compact
