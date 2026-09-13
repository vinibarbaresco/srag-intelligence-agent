"""Camada de LLM: planejamento e interpretacao.

O modelo e usado apenas onde linguagem natural e realmente necessaria --
selecionar tools e explicar o cenario. Nenhum numero e produzido aqui: o
contexto entregue ao modelo ja contem os valores calculados, e a saida passa
pelo guardrail de evidencia antes de ser publicada.

Quando nao ha credencial (ou o usuario passa `--no-llm`), entra o
:class:`DeterministicNarrator`: uma redacao por template a partir dos mesmos
resultados de tools. A PoC continua executavel e auditavel, e o relatorio declara
qual das duas vias produziu o texto.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from src.config import get_settings
from src.guardrails.policies import DISCLAIMER
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """Voce e um analista epidemiologico apoiando profissionais de \
saude no monitoramento de SRAG (Sindrome Respiratoria Aguda Grave) no Brasil.

REGRAS INVIOLAVEIS:
1. Todo numero que voce escrever deve aparecer literalmente no contexto \
fornecido. Nunca calcule, estime, arredonde de forma nova nem infira valores.
2. Quando uma metrica vier com valor nulo, declare que nao e possivel calcula-la \
com seguranca e cite o motivo informado. Nunca substitua por zero ou estimativa.
3. Noticias sao contexto externo. Elas ajudam a interpretar o cenario, mas nunca \
corrigem, confirmam numericamente nem substituem os indicadores calculados.
4. Nao emita diagnostico, prescricao, recomendacao terapeutica nem conduta \
clinica individual. A analise e populacional e agregada.
5. Respeite as limitacoes declaradas de cada indicador. Em especial: a taxa de \
admissao em UTI NAO e taxa de ocupacao de leitos, e a cobertura vacinal entre \
casos notificados NAO e cobertura vacinal da populacao.
6. Separe claramente o que e dado observado do que e sua interpretacao.

ESTILO: portugues do Brasil, tecnico, direto, sem alarmismo e sem minimizacao. \
Paragrafos curtos."""

_INTERPRETATION_TEMPLATE = """Gere a interpretacao epidemiologica do cenario \
descrito abaixo.

Estruture em quatro secoes curtas, nesta ordem:
1. Panorama geral - o que os indicadores mostram em conjunto.
2. Severidade e pressao assistencial - mortalidade e UTI, com as limitacoes.
3. Cobertura vacinal declarada - com o vies de selecao explicitado.
4. Leitura do contexto externo - o que as noticias acrescentam, se houver, \
deixando claro que sao contexto e nao dado oficial.

CONTEXTO (unica fonte de numeros permitida):
{context}
"""


class Interpreter(ABC):
    """Interface comum das vias de interpretacao."""

    #: Rotulo da via usada, registrado no relatorio e na auditoria.
    source: str

    @abstractmethod
    def interpret(self, context: dict[str, Any]) -> str:
        """Produz o texto interpretativo a partir do contexto ja calculado."""

    def plan(self, request: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Seleciona as tools necessarias para atender a solicitacao."""
        return {
            "selected_tools": [tool["function"]["name"] for tool in tools],
            "rationale": "Plano padrao: o relatorio exige todos os indicadores.",
            "planner": self.source,
        }


class OpenAIInterpreter(Interpreter):
    """Planejamento e interpretacao via modelo da OpenAI."""

    source = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.openai_model
        self._api_key = api_key or settings.openai_api_key
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY ausente.")
        self.source = f"openai:{self.model}"
        self._client = self._build_client()

    def _build_client(self) -> Any:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=self.model, api_key=self._api_key, temperature=0)

    def plan(self, request: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Pede ao modelo que escolha as tools pertinentes ao pedido.

        A escolha do modelo e registrada, mas nao reduz o conjunto obrigatorio do
        relatorio: o orquestrador une o plano ao contrato de entrega, de modo que
        uma omissao do modelo nunca produza um relatorio incompleto.
        """
        catalog = [
            {
                "name": tool["function"]["name"],
                "description": tool["function"]["description"],
            }
            for tool in tools
        ]
        prompt = (
            "Solicitacao do usuario:\n"
            f"{request}\n\n"
            "Tools disponiveis:\n"
            f"{json.dumps(catalog, ensure_ascii=False, indent=2)}\n\n"
            "Responda APENAS com um JSON no formato "
            '{"selected_tools": [...], "rationale": "..."} indicando quais tools '
            "sao necessarias para atender a solicitacao."
        )

        try:
            response = self._client.invoke(
                [("system", SYSTEM_PROMPT), ("human", prompt)]
            )
            payload = _extract_json(str(response.content))
            payload["planner"] = self.source
            return payload
        except Exception as exc:  # planejamento e auxiliar; nao pode derrubar o run
            logger.warning("planejamento via LLM falhou", extra={"motivo": str(exc)})
            fallback = super().plan(request, tools)
            fallback["planner"] = f"{self.source} (fallback: {type(exc).__name__})"
            return fallback

    def interpret(self, context: dict[str, Any]) -> str:
        prompt = _INTERPRETATION_TEMPLATE.format(
            context=json.dumps(context, ensure_ascii=False, indent=2, default=str)
        )
        response = self._client.invoke([("system", SYSTEM_PROMPT), ("human", prompt)])
        return str(response.content).strip()


class DeterministicNarrator(Interpreter):
    """Redacao por template, sem modelo de linguagem.

    Garante que `python main.py --no-llm` produza um relatorio completo e
    auditavel sem credencial. Por construcao so escreve numeros que vieram das
    tools, entao atravessa o guardrail de evidencia trivialmente.
    """

    source = "deterministic-template"

    def interpret(self, context: dict[str, Any]) -> str:
        metrics = context.get("indicadores", {})
        series = context.get("series", {})
        news = context.get("contexto_externo", {})

        sections = [
            "### 1. Panorama geral",
            self._growth_paragraph(metrics.get("case_growth_rate"), series),
            "",
            "### 2. Severidade e pressao assistencial",
            self._mortality_paragraph(metrics.get("mortality_rate")),
            self._icu_paragraph(metrics.get("icu_admission_rate")),
            "",
            "### 3. Cobertura vacinal declarada",
            self._vaccination_paragraph(metrics.get("vaccination_coverage_among_cases")),
            "",
            "### 4. Leitura do contexto externo",
            self._news_paragraph(news),
            "",
            DISCLAIMER,
        ]
        return "\n".join(part for part in sections if part is not None)

    def _growth_paragraph(self, metric: dict[str, Any] | None, series: dict[str, Any]) -> str:
        if not metric:
            return "Indicador de variacao de casos nao disponivel nesta execucao."
        if metric.get("value") is None:
            return (
                "Nao e possivel calcular a taxa de aumento de casos com seguranca: "
                f"{metric.get('unavailable_reason')}"
            )

        components = metric.get("components", {})
        direction = "aumento" if metric["value"] > 0 else "reducao"
        monthly = series.get("monthly_cases", {}).get("summary", {})
        total = monthly.get("total_de_casos")

        text = (
            f"A comparacao entre janelas consecutivas de {components.get('janela_dias')} "
            f"dias indica {direction} de {abs(metric['value'])}% no numero de casos de "
            f"SRAG: {components.get('casos_periodo_atual')} casos na janela atual contra "
            f"{components.get('casos_periodo_anterior')} na anterior."
        )
        if total is not None:
            text += f" No acumulado dos ultimos 12 meses foram {total} casos notificados."
        return text

    def _mortality_paragraph(self, metric: dict[str, Any] | None) -> str:
        if not metric:
            return "Indicador de mortalidade nao disponivel nesta execucao."
        if metric.get("value") is None:
            return (
                "Nao e possivel calcular a taxa de mortalidade com seguranca: "
                f"{metric.get('unavailable_reason')}"
            )
        components = metric.get("components", {})
        return (
            f"A letalidade entre casos encerrados foi de {metric['value']}%: "
            f"{metric['numerator']} obitos por SRAG em {metric['denominator']} casos "
            f"encerrados. Havia ainda {components.get('casos_em_aberto')} casos sem "
            "encerramento no periodo, o que torna o indicador instavel na janela "
            "mais recente."
        )

    def _icu_paragraph(self, metric: dict[str, Any] | None) -> str:
        if not metric:
            return "Indicadores de UTI nao disponiveis nesta execucao."
        if metric.get("value") is None:
            return (
                "Nao e possivel calcular a taxa de admissao em UTI com seguranca: "
                f"{metric.get('unavailable_reason')}"
            )
        components = metric.get("components", {})
        return (
            f"Entre os hospitalizados por SRAG, {metric['value']}% foram internados em "
            f"UTI ({metric['numerator']} de {metric['denominator']} com a informacao "
            f"preenchida). O censo diario de pacientes de SRAG em UTI atingiu pico de "
            f"{components.get('censo_diario_pico_pacientes_em_uti')} pacientes no "
            "periodo. Este indicador mede admissao em UTI, nao ocupacao de leitos: o "
            "dataset nao registra capacidade instalada."
        )

    def _vaccination_paragraph(self, metric: dict[str, Any] | None) -> str:
        if not metric:
            return "Indicadores de vacinacao nao disponiveis nesta execucao."
        if metric.get("value") is None:
            return (
                "Nao e possivel calcular a cobertura vacinal com seguranca: "
                f"{metric.get('unavailable_reason')}"
            )
        components = metric.get("components", {})
        covid = components.get("covid19", {})
        influenza = components.get("influenza", {})
        return (
            f"Entre os casos notificados com a informacao preenchida, {metric['value']}% "
            f"declararam vacinacao contra covid-19 (completude de "
            f"{covid.get('completude_da_informacao_pct')}%) e "
            f"{influenza.get('cobertura_declarada_pct')}% contra influenza "
            f"(completude de {influenza.get('completude_da_informacao_pct')}%). "
            "Trata-se de cobertura entre pessoas que adoeceram e foram notificadas, "
            "nao da cobertura vacinal da populacao."
        )

    def _news_paragraph(self, news: dict[str, Any]) -> str:
        articles = news.get("articles") or []
        if not articles:
            return (
                "Nenhuma noticia recente de fonte confiavel foi recuperada para o tema "
                f"nesta execucao. {news.get('unavailable_reason') or ''}".strip()
            )
        headlines = "; ".join(
            f"\"{article['titulo']}\" ({article['fonte']}, {article['data']})"
            for article in articles[:3]
        )
        return (
            f"Foram recuperadas {len(articles)} noticias de fontes confiaveis no periodo. "
            f"Entre elas: {headlines}. Esse material e contexto externo e nao altera "
            "nenhum dos indicadores calculados sobre os dados do DATASUS."
        )


def get_interpreter(use_llm: bool = True) -> Interpreter:
    """Escolhe a via de interpretacao conforme configuracao e credencial."""
    if not use_llm:
        logger.info("interpretacao deterministica solicitada (--no-llm)")
        return DeterministicNarrator()

    settings = get_settings()
    if not settings.llm_enabled:
        logger.warning(
            "OPENAI_API_KEY ausente; usando narrador deterministico. "
            "Defina a chave no .env para habilitar a interpretacao por LLM."
        )
        return DeterministicNarrator()

    try:
        interpreter = OpenAIInterpreter()
        logger.info("interpretador selecionado", extra={"source": interpreter.source})
        return interpreter
    except (ValueError, ImportError) as exc:
        logger.warning(
            "interpretador OpenAI indisponivel; usando fallback deterministico",
            extra={"motivo": str(exc)},
        )
        return DeterministicNarrator()


def _extract_json(text: str) -> dict[str, Any]:
    """Extrai o primeiro objeto JSON de uma resposta textual do modelo."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Resposta do modelo nao contem JSON.")
    return json.loads(text[start : end + 1])
