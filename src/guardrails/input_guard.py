"""Validacao da solicitacao recebida (no `validate_request` do grafo).

Duas verificacoes:

1. **Escopo clinico** -- pedidos de diagnostico, prescricao ou conduta individual
   sao recusados antes de qualquer consulta (Guardrail 1).
2. **Parametros** -- UF e classificacao final sao validadas contra dominios
   fechados antes de virarem filtro, de modo que nenhum texto livre alcance a
   camada de dados (Guardrail 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.guardrails.policies import (
    CLINICAL_REQUEST_PATTERNS,
    INDIVIDUAL_DATA_REQUEST_PATTERNS,
    MEDICAL_ADVICE,
    SENSITIVE_DATA,
)
from src.metrics.filters import AnalyticFilters, InvalidFilterError


@dataclass(slots=True)
class RequestValidation:
    """Resultado da validacao de uma solicitacao."""

    allowed: bool
    request: str
    filters: AnalyticFilters | None = None
    blocked_by: str | None = None
    reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "request": self.request,
            "filters": self.filters.to_dict() if self.filters else None,
            "blocked_by": self.blocked_by,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


def validate_request(
    request: str,
    *,
    uf: str | None = None,
    classification: int | None = None,
) -> RequestValidation:
    """Valida o pedido do usuario e os parametros de recorte.

    Args:
        request: texto da solicitacao.
        uf: sigla da UF, opcional.
        classification: codigo de `CLASSI_FIN`, opcional.

    Returns:
        Resultado da validacao. Quando `allowed` e `False`, o grafo encerra sem
        consultar dados e o motivo e registrado na trilha de auditoria.
    """
    text = (request or "").strip()
    if not text:
        return RequestValidation(
            allowed=False,
            request=request,
            blocked_by="empty_request",
            reason="A solicitacao esta vazia; nao ha o que analisar.",
        )

    for pattern in INDIVIDUAL_DATA_REQUEST_PATTERNS:
        if pattern.search(text):
            return RequestValidation(
                allowed=False,
                request=request,
                blocked_by=SENSITIVE_DATA.key,
                reason=(
                    "A solicitacao pede dados de individuos. O sistema opera apenas "
                    "sobre agregados por periodo, UF e classificacao final; nao existe "
                    "caminho para recuperar registros, nomes ou identificadores de "
                    "pacientes. Reformule o pedido em termos agregados."
                ),
            )

    for pattern in CLINICAL_REQUEST_PATTERNS:
        if pattern.search(text):
            return RequestValidation(
                allowed=False,
                request=request,
                blocked_by=MEDICAL_ADVICE.key,
                reason=(
                    "A solicitacao pede orientacao clinica individual. "
                    f"{MEDICAL_ADVICE.description} Reformule o pedido em termos "
                    "epidemiologicos agregados (ex.: evolucao de casos, "
                    "letalidade, admissoes em UTI por periodo ou UF)."
                ),
            )

    try:
        filters = AnalyticFilters(uf=uf, classification=classification)
    except InvalidFilterError as exc:
        return RequestValidation(
            allowed=False,
            request=request,
            blocked_by="invalid_filter",
            reason=str(exc),
        )

    warnings: list[str] = []
    if filters.uf is not None:
        warnings.append(
            f"Analise restrita a UF {filters.uf}: recortes menores tem menos "
            "casos e indicadores mais instaveis."
        )

    return RequestValidation(allowed=True, request=text, filters=filters, warnings=warnings)
