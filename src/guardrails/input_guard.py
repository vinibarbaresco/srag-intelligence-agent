"""Validacao da solicitacao recebida (no `validate_request` do grafo).

Quatro verificacoes, nesta ordem:

1. **Escopo clinico** -- pedidos de diagnostico, prescricao ou conduta individual
   sao recusados antes de qualquer consulta (Guardrail 1).
2. **Dado individual** -- pedidos de registro, nome ou identificador de paciente
   sao recusados (Guardrail 2).
3. **Prompt injection** -- tentativas de sobrescrever instrucoes, extrair o
   prompt ou segredos, executar SQL ou arbitrar o valor de um indicador sao
   classificadas por risco e, no risco alto, recusadas (Guardrail 8). Ver
   `src/guardrails/injection.py`.
4. **Parametros** -- UF e classificacao final sao validadas contra dominios
   fechados antes de virarem filtro, de modo que nenhum texto livre alcance a
   camada de dados (Guardrail 4).

A ordem importa: as recusas de conteudo (1 e 2) vem antes da de controle (3)
porque descrevem melhor o que o usuario pediu. Um texto que pede conduta clinica
*e* tenta sobrescrever instrucoes e recusado pelo motivo mais informativo para
quem escreveu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.guardrails.injection import RISK_MEDIUM, classify_injection
from src.guardrails.policies import (
    CLINICAL_REQUEST_PATTERNS,
    INDIVIDUAL_DATA_REQUEST_PATTERNS,
    MEDICAL_ADVICE,
    PROMPT_INJECTION,
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
    #: Veredito da analise de prompt injection, sempre presente -- inclusive
    #: quando o risco e nenhum. A auditoria precisa poder afirmar que a
    #: verificacao ocorreu, e nao apenas que ela nao bloqueou.
    injection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "request": self.request,
            "filters": self.filters.to_dict() if self.filters else None,
            "blocked_by": self.blocked_by,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "prompt_injection": dict(self.injection),
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
    verdict = classify_injection(text)
    injection = verdict.to_dict()

    if not text:
        return RequestValidation(
            allowed=False,
            request=request,
            blocked_by="empty_request",
            reason="A solicitacao esta vazia; nao ha o que analisar.",
            injection=injection,
        )

    for pattern in INDIVIDUAL_DATA_REQUEST_PATTERNS:
        if pattern.search(text):
            return RequestValidation(
                allowed=False,
                request=request,
                blocked_by=SENSITIVE_DATA.key,
                injection=injection,
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
                injection=injection,
                reason=(
                    "A solicitacao pede orientacao clinica individual. "
                    f"{MEDICAL_ADVICE.description} Reformule o pedido em termos "
                    "epidemiologicos agregados (ex.: evolucao de casos, "
                    "letalidade, admissoes em UTI por periodo ou UF)."
                ),
            )

    if verdict.blocked:
        return RequestValidation(
            allowed=False,
            request=request,
            blocked_by=PROMPT_INJECTION.key,
            injection=injection,
            reason=verdict.reason,
        )

    try:
        filters = AnalyticFilters(uf=uf, classification=classification)
    except InvalidFilterError as exc:
        return RequestValidation(
            allowed=False,
            request=request,
            blocked_by="invalid_filter",
            reason=str(exc),
            injection=injection,
        )

    warnings: list[str] = []
    if filters.uf is not None:
        warnings.append(
            f"Analise restrita a UF {filters.uf}: recortes menores tem menos "
            "casos e indicadores mais instaveis."
        )
    if verdict.risk == RISK_MEDIUM:
        # Risco medio segue, mas nao em silencio: o aviso aparece no relatorio e
        # o achado fica na auditoria, de modo que um padrao que hoje e ambiguo
        # possa ser reclassificado com evidencia de uso real.
        warnings.append(
            "A solicitacao contem padrao de risco baixo para prompt injection "
            "(instrucao dirigida ao agente, e nao pergunta sobre os dados). Ela "
            "foi executada normalmente; o achado esta na trilha de auditoria."
        )

    return RequestValidation(
        allowed=True,
        request=text,
        filters=filters,
        warnings=warnings,
        injection=injection,
    )
