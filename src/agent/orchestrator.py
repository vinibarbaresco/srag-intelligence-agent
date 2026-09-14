"""Orquestrador da execucao: monta o contexto, roda o grafo e grava a saida."""

from __future__ import annotations

import uuid
from typing import Any

from src.agent.graph import build_graph
from src.agent.llm import get_interpreter
from src.agent.nodes import GraphContext
from src.agent.report import write_report
from src.agent.state import SRAGState, initial_state
from src.guardrails.semantic_judge import get_semantic_judge
from src.news.ingest import ingest_news
from src.observability.audit import STATUS_ERROR, AuditTrail
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_REQUEST = (
    "Gere o relatorio de monitoramento de SRAG com os indicadores de aumento de "
    "casos, mortalidade, UTI e vacinacao, as series diaria e mensal, e o contexto "
    "de noticias recentes."
)


def _make_report_node(context: GraphContext):
    """No final: grava o relatorio e fecha o resumo de auditoria."""

    def node(state: SRAGState) -> dict[str, Any]:
        trail = context.trail
        with trail.step(node="generate_report") as audit:
            enriched = dict(state)
            enriched["audit_summary"] = trail.summary()
            paths = write_report(enriched)
            audit["summary"] = f"relatorio gravado em {paths['markdown']}"

        return {"report_paths": paths, "audit_summary": trail.summary()}

    return node


def run_report(
    request: str = DEFAULT_REQUEST,
    *,
    uf: str | None = None,
    classification: int | None = None,
    use_llm: bool = True,
    run_id: str | None = None,
) -> SRAGState:
    """Executa o fluxo completo e devolve o estado final.

    Args:
        request: solicitacao em linguagem natural.
        uf: sigla da UF para recortar a analise; `None` para nacional.
        classification: codigo de `CLASSI_FIN`; `None` para todas.
        use_llm: quando `False`, usa a via de interpretacao deterministica.
        run_id: identificador da execucao; gerado se omitido.

    Returns:
        Estado final do grafo, com indicadores, series, graficos, contexto
        externo, interpretacao, relatorio e resumo de auditoria.
    """
    trail = AuditTrail(run_id=run_id or str(uuid.uuid4()))
    interpreter = get_interpreter(use_llm=use_llm)
    context = GraphContext(
        trail=trail,
        interpreter=interpreter,
        news_refresher=ingest_news,
        judge=get_semantic_judge(use_llm=use_llm),
    )

    graph = build_graph(context, _make_report_node(context))
    state = initial_state(trail.run_id, request, uf=uf, classification=classification)

    logger.info(
        "execucao iniciada",
        extra={"run_id": trail.run_id, "uf": uf, "interpreter": interpreter.source},
    )
    try:
        final_state: SRAGState = graph.invoke(state)
    except Exception as exc:
        # Falha em um NO (nao em tool) chegaria aqui sem relatorio nem trilha
        # replicada. Registrar e devolver um estado de erro preserva a
        # auditabilidade: o que houve fica no relatorio e no banco.
        logger.error(
            "execucao do grafo interrompida",
            extra={"run_id": trail.run_id, "motivo": f"{type(exc).__name__}: {exc}"},
        )
        trail.record(
            node="graph",
            status=STATUS_ERROR,
            result_summary="execucao interrompida por excecao em no do grafo",
            error=f"{type(exc).__name__}: {exc}",
        )
        final_state = dict(state)  # type: ignore[assignment]
        final_state["errors"] = [
            *state.get("errors", []),
            f"Execucao interrompida: {type(exc).__name__}: {exc}",
        ]

    if not final_state.get("report_paths"):
        # Caminho de recusa: o grafo termina em validate_request e o relatorio
        # explicando o bloqueio ainda precisa ser gravado.
        enriched = dict(final_state)
        enriched["audit_summary"] = trail.summary()
        final_state["report_paths"] = write_report(enriched)
        final_state["audit_summary"] = trail.summary()

    # A replica no banco e o ultimo passo: o relatorio ja esta gravado e a
    # trilha ja esta em disco, entao uma falha aqui nao compromete a entrega.
    trail.persisted_events = trail.persist_to_database()
    final_state["audit_summary"] = trail.summary()

    logger.info(
        "execucao concluida",
        extra={
            "run_id": trail.run_id,
            "eventos": len(trail.events),
            "relatorio": final_state.get("report_paths", {}).get("markdown"),
        },
    )
    return final_state
