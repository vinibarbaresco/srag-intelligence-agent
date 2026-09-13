"""Schemas de entrada das tools.

Os schemas sao o contrato entre o modelo e a camada deterministica. Todo
parametro tem tipo, dominio e valor padrao explicitos; nada chega as consultas
como texto livre (Guardrail 4). Os mesmos modelos geram as descricoes de
function calling entregues ao LLM, de modo que a validacao executada e a
documentacao apresentada nunca divirjam.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.data.schema import UF_CODES

UFLiteral = Literal[tuple(sorted(UF_CODES))]  # type: ignore[valid-type]


class _StrictModel(BaseModel):
    """Base que recusa parametros nao declarados."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricQuery(_StrictModel):
    """Parametros comuns aos indicadores epidemiologicos."""

    uf: UFLiteral | None = Field(
        default=None,
        description=(
            "Sigla da unidade federativa de notificacao (ex.: 'SP'). "
            "Omitir para analise nacional."
        ),
    )
    classification: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None,
        description=(
            "Classificacao final do caso (CLASSI_FIN): 1=SRAG por influenza, "
            "2=SRAG por outro virus respiratorio, 3=SRAG por outro agente "
            "etiologico, 4=SRAG nao especificado, 5=SRAG por covid-19. "
            "Omitir para todas as classificacoes."
        ),
    )
    window_days: int | None = Field(
        default=None,
        ge=7,
        le=365,
        description=(
            "Tamanho da janela de analise em dias. Omitir para usar o padrao "
            "configurado (30 dias)."
        ),
    )


class DailySeriesQuery(_StrictModel):
    """Parametros da serie diaria de casos."""

    uf: UFLiteral | None = Field(default=None, description="UF de notificacao; omitir para Brasil.")
    classification: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None, description="Classificacao final do caso (CLASSI_FIN)."
    )
    window_days: int = Field(
        default=30,
        ge=7,
        le=180,
        description="Numero de dias da serie. Padrao 30, conforme o relatorio exigido.",
    )


class MonthlySeriesQuery(_StrictModel):
    """Parametros da serie mensal de casos."""

    uf: UFLiteral | None = Field(default=None, description="UF de notificacao; omitir para Brasil.")
    classification: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None, description="Classificacao final do caso (CLASSI_FIN)."
    )
    window_months: int = Field(
        default=12,
        ge=3,
        le=36,
        description="Numero de meses da serie. Padrao 12, conforme o relatorio exigido.",
    )


class NewsQuery(_StrictModel):
    """Parametros da busca de noticias no Vector DB."""

    query: str = Field(
        min_length=3,
        max_length=300,
        description=(
            "Tema a pesquisar no acervo de noticias ja coletado, em linguagem "
            "natural (ex.: 'aumento de casos de SRAG em criancas')."
        ),
    )
    top_k: int = Field(
        default=5, ge=1, le=20, description="Numero maximo de noticias retornadas."
    )
    max_age_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Limita a busca a noticias publicadas nesta janela.",
    )


class ChartRequest(_StrictModel):
    """Parametros da geracao de graficos."""

    uf: UFLiteral | None = Field(default=None, description="UF de notificacao; omitir para Brasil.")
    classification: Literal[1, 2, 3, 4, 5] | None = Field(
        default=None, description="Classificacao final do caso (CLASSI_FIN)."
    )
