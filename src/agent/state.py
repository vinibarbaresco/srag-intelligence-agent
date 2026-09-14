"""Estado compartilhado do grafo do agente.

O estado e explicitamente compartimentado: dados calculados, contexto externo e
texto gerado pelo modelo vivem em campos distintos e nunca se misturam. Essa
separacao e o que permite ao renderizador rotular cada bloco do relatorio como
DADO, INFERENCIA ou CONTEXTO EXTERNO -- e o que torna o Guardrail 5 estrutural,
e nao apenas uma instrucao de prompt.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class SRAGState(TypedDict, total=False):
    """Estado percorrido pelos nos do grafo."""

    # --- Identificacao da execucao -------------------------------------------
    run_id: str
    request: str
    uf: str | None
    classification: int | None

    # --- Validacao de entrada ------------------------------------------------
    validation: dict[str, Any]
    plan: dict[str, Any]

    # --- DADO: resultados deterministicos das tools --------------------------
    metrics: dict[str, dict[str, Any]]
    diagnostics: dict[str, dict[str, Any]]
    series: dict[str, dict[str, Any]]
    charts: dict[str, dict[str, Any]]

    # --- CONTEXTO EXTERNO: noticias, isoladas dos calculos -------------------
    external_context: dict[str, Any]

    # --- DADO: alertas deterministicos e comparacao com a execucao anterior ---
    alerts: dict[str, Any]

    # --- Verificacao de evidencia --------------------------------------------
    evidence: dict[str, Any]

    # --- INFERENCIA: texto produzido pelo modelo -----------------------------
    interpretation: str
    interpretation_source: str
    guardrail_report: dict[str, Any]
    llm_usage: dict[str, Any]

    # --- Saida ---------------------------------------------------------------
    report_paths: dict[str, str]
    audit_summary: dict[str, Any]

    # --- Degradacao observavel ------------------------------------------------
    #
    # Os redutores `operator.add` fazem o LangGraph concatenar as listas de cada
    # no em vez de sobrescreve-las: a degradacao de qualquer etapa permanece
    # visivel no relatorio final.
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


def initial_state(
    run_id: str,
    request: str,
    uf: str | None = None,
    classification: int | None = None,
) -> SRAGState:
    """Cria o estado inicial de uma execucao."""
    return SRAGState(
        run_id=run_id,
        request=request,
        uf=uf,
        classification=classification,
        metrics={},
        diagnostics={},
        series={},
        charts={},
        external_context={},
        alerts={},
        evidence={},
        interpretation="",
        interpretation_source="",
        guardrail_report={},
        llm_usage={},
        report_paths={},
        audit_summary={},
        warnings=[],
        errors=[],
    )
