"""Registro central das tools deterministicas.

O registro cumpre tres papeis:

* **catalogo** -- lista unica de tudo que o agente pode acionar;
* **contrato** -- gera as descricoes de function calling a partir dos mesmos
  schemas Pydantic usados na validacao;
* **fronteira de seguranca** -- o despacho so aceita nomes registrados e valida
  os parametros antes da execucao. Nao ha caminho pelo qual o modelo alcance o
  banco fora desta lista (Guardrail 4).

Falhas de tool nao derrubam a execucao: sao convertidas em um envelope de erro
auditado, para que o relatorio possa declarar o que faltou em vez de terminar
abruptamente.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from src.observability.audit import STATUS_ERROR, AuditTrail
from src.observability.logging_config import get_logger
from src.tools import chart_tools, metric_tools, news_tools, series_tools
from src.tools.schemas import (
    ChartRequest,
    DailySeriesQuery,
    MetricQuery,
    MonthlySeriesQuery,
    NewsQuery,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Descricao de uma tool disponivel ao agente."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., dict[str, Any]]
    category: str

    def to_openai_schema(self) -> dict[str, Any]:
        """Converte a tool para o formato de function calling da OpenAI."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


TOOLS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="get_case_growth_rate",
        description=(
            "Taxa de aumento de casos de SRAG: variacao percentual entre a janela "
            "mais recente analisavel e a janela anterior de mesmo tamanho, pela "
            "data dos primeiros sintomas."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_case_growth_rate,
        category="indicador",
    ),
    ToolSpec(
        name="get_mortality_rate",
        description=(
            "Taxa de mortalidade por SRAG: obitos (EVOLUCAO=2) sobre casos "
            "encerrados elegiveis (EVOLUCAO em 1,2,3), no periodo analisado."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_mortality_rate,
        category="indicador",
    ),
    ToolSpec(
        name="get_icu_metrics",
        description=(
            "Indicadores de UTI: taxa de admissao em UTI entre hospitalizados por "
            "SRAG e censo diario de pacientes em UTI. A taxa de ocupacao de "
            "leitos NAO e calculavel com este dataset e retorna nula com o motivo."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_icu_metrics,
        category="indicador",
    ),
    ToolSpec(
        name="get_vaccination_metrics",
        description=(
            "Cobertura vacinal declarada (covid-19 e influenza) entre casos "
            "notificados de SRAG. A taxa de vacinacao da POPULACAO nao e "
            "calculavel com este dataset e retorna nula com o motivo."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_vaccination_metrics,
        category="indicador",
    ),
    ToolSpec(
        name="get_incidence_rate",
        description=(
            "Incidencia de SRAG notificada por 100 mil habitantes na janela "
            "analisada, com denominador populacional do IBGE. Permite comparar "
            "UFs de tamanhos diferentes."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_incidence_rate,
        category="indicador",
    ),
    ToolSpec(
        name="get_seasonal_baseline",
        description=(
            "Excesso de casos sobre o baseline sazonal: variacao da janela atual "
            "em relacao a mediana da mesma janela de calendario nos anos de "
            "referencia (2020-2021 excluidos). Distingue surto de sazonalidade."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_seasonal_baseline,
        category="indicador",
    ),
    ToolSpec(
        name="get_notification_completeness",
        description=(
            "Perfil do atraso de notificacao observado na base (percentis em "
            "dias) e verificacao de suficiencia do corte analitico configurado."
        ),
        input_model=MetricQuery,
        handler=metric_tools.get_notification_completeness,
        category="diagnostico",
    ),
    ToolSpec(
        name="get_daily_cases",
        description="Serie diaria de casos de SRAG na janela analisavel (padrao: 30 dias).",
        input_model=DailySeriesQuery,
        handler=series_tools.get_daily_cases,
        category="serie",
    ),
    ToolSpec(
        name="get_monthly_cases",
        description="Serie mensal de casos de SRAG na janela analisavel (padrao: 12 meses).",
        input_model=MonthlySeriesQuery,
        handler=series_tools.get_monthly_cases,
        category="serie",
    ),
    ToolSpec(
        name="render_daily_cases_chart",
        description="Gera o grafico PNG do numero diario de casos dos ultimos 30 dias.",
        input_model=ChartRequest,
        handler=chart_tools.build_daily_cases_chart,
        category="grafico",
    ),
    ToolSpec(
        name="render_monthly_cases_chart",
        description="Gera o grafico PNG do numero mensal de casos dos ultimos 12 meses.",
        input_model=ChartRequest,
        handler=chart_tools.build_monthly_cases_chart,
        category="grafico",
    ),
    ToolSpec(
        name="search_srag_news",
        description=(
            "Busca semantica de noticias recentes sobre SRAG, surtos "
            "respiratorios, influenza, covid-19 e pressao hospitalar, no acervo "
            "de fontes confiaveis atualizado antes de cada relatorio. Em falha "
            "de rede, consulta o cache persistido. Contexto externo apenas: "
            "nao altera nenhum indicador."
        ),
        input_model=NewsQuery,
        handler=news_tools.search_srag_news,
        category="contexto_externo",
    ),
)

REGISTRY: Final[dict[str, ToolSpec]] = {tool.name: tool for tool in TOOLS}


class UnknownToolError(KeyError):
    """Tentativa de acionar uma tool que nao existe no registro."""


def openai_tool_specs() -> list[dict[str, Any]]:
    """Descricoes de function calling de todas as tools registradas."""
    return [tool.to_openai_schema() for tool in TOOLS]


def call_tool(
    name: str,
    parameters: dict[str, Any] | None = None,
    *,
    trail: AuditTrail | None = None,
) -> dict[str, Any]:
    """Aciona uma tool registrada, com validacao e auditoria.

    Args:
        name: nome exato da tool no registro.
        parameters: parametros brutos, validados contra o schema da tool.
        trail: trilha de auditoria da execucao corrente.

    Returns:
        Retorno da tool, ou um envelope de erro (`{"error": ...}`) quando os
        parametros sao invalidos ou a execucao falha. Nunca levanta excecao de
        execucao para o chamador: a degradacao precisa ser visivel no relatorio.

    Raises:
        UnknownToolError: se o nome nao estiver registrado. Este caso e erro de
            programacao ou tentativa de acionar algo fora do catalogo, e deve
            interromper o fluxo.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise UnknownToolError(f"Tool nao registrada: {name!r}. Disponiveis: {sorted(REGISTRY)}")

    raw = parameters or {}
    try:
        validated = spec.input_model(**raw)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        message = f"Parametros invalidos para {name}: {detail}"
        logger.warning("parametros rejeitados", extra={"tool": name, "motivo": detail})
        if trail is not None:
            trail.record(
                tool=name,
                parameters=raw,
                status=STATUS_ERROR,
                result_summary="parametros rejeitados pelo schema",
                error=message,
            )
        return {"error": message, "tool": name, "parameters": raw}

    try:
        return spec.handler(**validated.model_dump(), trail=trail)
    except Exception as exc:  # degradacao controlada, nunca silenciosa
        # O evento de auditoria ja foi gravado pelo decorator `@audited` do
        # proprio handler, que mede a duracao real e entao repassa a excecao.
        # Registrar aqui de novo duplicaria a falha na trilha.
        message = f"Falha ao executar {name}: {type(exc).__name__}: {exc}"
        logger.error("tool falhou", extra={"tool": name, "motivo": str(exc)})
        return {"error": message, "tool": name, "parameters": validated.model_dump()}
