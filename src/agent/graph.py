"""Montagem do grafo de orquestracao em LangGraph.

Fluxo linear e explicito, sem loops:

    START
      -> validate_request
      -> (recusado) END | collect_epidemiological_metrics
      -> collect_time_series
      -> search_external_news
      -> select_optional_tools      (tool calling: so analises ADICIONAIS)
      -> evaluate_alerts
      -> validate_evidence
      -> generate_interpretation
      -> generate_report
      -> END

O grafo nao tem ciclos, e a decisao merece ser dita com precisao porque e o
ponto em que a arquitetura foi escolhida (ver `src/agent/tool_calling.py`):

* o **contrato de entrega** -- indicadores, series e graficos -- e uma sequencia
  fixa. Nao ha ciclo aqui de proposito: dois relatorios do mesmo recorte
  precisam ser comparaveis, e um agente que decide sozinho quantas vezes repetir
  uma etapa obrigatoria destroi essa garantia sem nada em troca;
* o **aprofundamento** acontece em `select_optional_tools`, onde o modelo faz
  function calling de verdade sobre uma allowlist de tools de leitura. O laco de
  iteracoes vive dentro do no, limitado por configuracao, em vez de virar aresta
  do grafo -- assim o teto e explicito e a topologia continua auditavel numa
  olhada.

Classificacao honesta da solucao: **workflow LangGraph deterministico com uma
etapa de agente com tool calling real**, e nao um agente autonomo de ponta a
ponta.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    GraphContext,
    make_collect_metrics,
    make_collect_series,
    make_evaluate_alerts,
    make_generate_interpretation,
    make_search_news,
    make_select_optional_tools,
    make_validate_evidence,
    make_validate_request,
)
from src.agent.state import SRAGState

NODE_VALIDATE_REQUEST = "validate_request"
NODE_COLLECT_METRICS = "collect_epidemiological_metrics"
NODE_COLLECT_SERIES = "collect_time_series"
NODE_SEARCH_NEWS = "search_external_news"
NODE_SELECT_OPTIONAL_TOOLS = "select_optional_tools"
NODE_EVALUATE_ALERTS = "evaluate_alerts"
NODE_VALIDATE_EVIDENCE = "validate_evidence"
NODE_INTERPRETATION = "generate_interpretation"
NODE_REPORT = "generate_report"

#: Ordem canonica dos nos, usada pela documentacao e pelo diagrama.
NODE_SEQUENCE: tuple[str, ...] = (
    NODE_VALIDATE_REQUEST,
    NODE_COLLECT_METRICS,
    NODE_COLLECT_SERIES,
    NODE_SEARCH_NEWS,
    NODE_SELECT_OPTIONAL_TOOLS,
    NODE_EVALUATE_ALERTS,
    NODE_VALIDATE_EVIDENCE,
    NODE_INTERPRETATION,
    NODE_REPORT,
)


def _route_after_validation(state: SRAGState) -> str:
    """Interrompe a execucao quando a solicitacao e recusada na entrada.

    Uma solicitacao bloqueada nao deve acionar nenhuma consulta ao banco: a
    recusa e final e o relatorio explica o motivo.
    """
    validation = state.get("validation") or {}
    return NODE_COLLECT_METRICS if validation.get("allowed") else END


def build_graph(
    context: GraphContext,
    report_node: Callable[[SRAGState], dict[str, Any]],
) -> Any:
    """Compila o grafo do agente.

    Args:
        context: dependencias (trilha de auditoria e interpretador).
        report_node: no final de renderizacao, injetado para manter a montagem
            do grafo independente do formato de saida.

    Returns:
        Grafo compilado, pronto para `invoke`.
    """
    graph = StateGraph(SRAGState)

    graph.add_node(NODE_VALIDATE_REQUEST, make_validate_request(context))
    graph.add_node(NODE_COLLECT_METRICS, make_collect_metrics(context))
    graph.add_node(NODE_COLLECT_SERIES, make_collect_series(context))
    graph.add_node(NODE_SEARCH_NEWS, make_search_news(context))
    graph.add_node(NODE_SELECT_OPTIONAL_TOOLS, make_select_optional_tools(context))
    graph.add_node(NODE_EVALUATE_ALERTS, make_evaluate_alerts(context))
    graph.add_node(NODE_VALIDATE_EVIDENCE, make_validate_evidence(context))
    graph.add_node(NODE_INTERPRETATION, make_generate_interpretation(context))
    graph.add_node(NODE_REPORT, report_node)

    graph.add_edge(START, NODE_VALIDATE_REQUEST)
    graph.add_conditional_edges(
        NODE_VALIDATE_REQUEST,
        _route_after_validation,
        {NODE_COLLECT_METRICS: NODE_COLLECT_METRICS, END: END},
    )
    graph.add_edge(NODE_COLLECT_METRICS, NODE_COLLECT_SERIES)
    graph.add_edge(NODE_COLLECT_SERIES, NODE_SEARCH_NEWS)
    graph.add_edge(NODE_SEARCH_NEWS, NODE_SELECT_OPTIONAL_TOOLS)
    graph.add_edge(NODE_SELECT_OPTIONAL_TOOLS, NODE_EVALUATE_ALERTS)
    graph.add_edge(NODE_EVALUATE_ALERTS, NODE_VALIDATE_EVIDENCE)
    graph.add_edge(NODE_VALIDATE_EVIDENCE, NODE_INTERPRETATION)
    graph.add_edge(NODE_INTERPRETATION, NODE_REPORT)
    graph.add_edge(NODE_REPORT, END)

    return graph.compile()
