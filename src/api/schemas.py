"""Contratos de entrada e saida da API HTTP.

Os parametros de indicador reaproveitam os mesmos dominios fechados das tools
(`src/tools/schemas.py`): a API nao abre nenhum caminho novo ate o banco.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.tools.schemas import UFLiteral


class HealthResponse(BaseModel):
    status: Literal["ok", "sem_banco"]
    banco_analitico_pronto: bool
    llm_habilitado: bool
    revisao_semantica_habilitada: bool
    versao_da_api: str = "1"


class ReportRequest(BaseModel):
    """Pedido de relatorio; os mesmos parametros de `python main.py`."""

    model_config = ConfigDict(extra="forbid")

    solicitacao: str | None = Field(
        default=None,
        min_length=3,
        max_length=1000,
        description="Pedido em linguagem natural. Omitir para o relatorio padrao completo.",
    )
    uf: UFLiteral | None = Field(default=None, description="Recorte por UF de notificacao.")
    classificacao: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None, description="Classificacao final do caso (CLASSI_FIN)."
    )
    usar_llm: bool = Field(
        default=True,
        description="Quando falso, usa a via deterministica mesmo havendo credencial.",
    )


class IndicatorSummary(BaseModel):
    metric: str
    value: float | None
    unit: str | None = None
    numerator: int | None = None
    denominator: int | None = None
    unavailable_reason: str | None = None


class ReportResponse(BaseModel):
    run_id: str
    aceito: bool
    motivo_da_recusa: str | None = None
    guardrail_de_entrada: str | None = None
    via_de_interpretacao: str | None = None
    indicadores: list[IndicatorSummary] = Field(default_factory=list)
    alertas: dict[str, Any] = Field(default_factory=dict)
    caminhos: dict[str, str] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list)
    erros: list[str] = Field(default_factory=list)
    custo_estimado_usd: float | None = None


class ErrorResponse(BaseModel):
    detail: str
