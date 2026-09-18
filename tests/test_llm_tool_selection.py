"""Comportamento agentic real de `OpenAIInterpreter.select_tools`.

Os testes de `tests/test_tool_calling.py` usam `FakeSelector`, que nunca chama
`OpenAIInterpreter` de verdade -- ele so mocka a interface `select_tools`.
Este arquivo cobre o que fica descoberto: o laco de `max_iterations`, a
retentativa de `_invoke_with_retry` e o que acontece quando as duas se
esgotam, tudo sem nenhuma chamada de rede real.

O padrao de mock segue `tests/test_semantic_judge.py::TestRevisorOpenAI`:
constroi `OpenAIInterpreter` via `__new__` (pulando `__init__`, que exige
`OPENAI_API_KEY`) e substitui `_client` por um cliente falso que implementa
apenas `bind_tools` e `invoke` -- a mesma superficie que `select_tools` usa do
`ChatOpenAI` real.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.llm import OpenAIInterpreter
from src.agent.tool_calling import run_tool_selection


def _response(
    *, content: str = "", tool_calls: list[dict[str, Any]] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata={"input_tokens": 10, "output_tokens": 5},
    )


def _interpreter(client: Any) -> OpenAIInterpreter:
    """Instancia `OpenAIInterpreter` sem credencial, com o cliente substituido."""
    interpreter = OpenAIInterpreter.__new__(OpenAIInterpreter)
    interpreter.model = "modelo-teste"
    interpreter.source = "openai:modelo-teste"
    interpreter._usage = {"chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0}
    interpreter._client = client
    return interpreter


class _QueueClient:
    """Cliente falso: consome uma fila fixa de respostas/erros, uma por `invoke`."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)
        self.invocations = 0

    def bind_tools(self, tools: Any) -> _QueueClient:
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        if not self._items:
            raise AssertionError("cliente falso chamado mais vezes do que a fila previa")
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _AlwaysFailsClient:
    """Cliente falso: toda chamada levanta a mesma excecao."""

    def __init__(self, exc_factory: Any) -> None:
        self._exc_factory = exc_factory
        self.invocations = 0

    def bind_tools(self, tools: Any) -> _AlwaysFailsClient:
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        raise self._exc_factory()


class _AlwaysProposesToolClient:
    """Cliente falso: sempre propoe mais uma chamada de tool, nunca conclui."""

    def __init__(self, tool: str = "get_mortality_rate") -> None:
        self._tool = tool
        self.invocations = 0

    def bind_tools(self, tools: Any) -> _AlwaysProposesToolClient:
        return self

    def invoke(self, messages: Any) -> Any:
        self.invocations += 1
        return _response(
            content=f"chamando {self._tool}",
            tool_calls=[{"name": self._tool, "args": {}, "id": f"call-{self.invocations}"}],
        )


class TestRetentativaComSucesso:
    """1: cliente falha uma vez e depois tem sucesso -- a retentativa cobre isso."""

    def test_segunda_tentativa_bem_sucedida_e_o_resultado_final(self):
        client = _QueueClient([RuntimeError("rate limit"), _response(content="sucesso apos retry")])
        interpreter = _interpreter(client)

        proposals, rationale, iterations = interpreter.select_tools(
            request="qualquer",
            computed={},
            tools=[],
            max_iterations=1,
            max_retries=1,
        )

        assert proposals == []
        assert "sucesso apos retry" in rationale
        assert iterations == 1
        # So a chamada bem-sucedida entra no consumo: a que falhou nao produziu
        # tokens de resposta.
        assert interpreter._usage["chamadas"] == 1
        assert client.invocations == 2


class TestEsgotamentoDeRetentativas:
    """2 e 4: cliente sempre falha -- RuntimeError apos esgotar o orcamento,
    capturada pelo chamador sem derrubar a execucao."""

    @pytest.mark.parametrize("excecao", [RuntimeError, TimeoutError])
    def test_select_tools_levanta_runtimeerror_com_numero_de_tentativas(self, excecao):
        client = _AlwaysFailsClient(lambda: excecao("provedor indisponivel"))
        interpreter = _interpreter(client)

        with pytest.raises(RuntimeError) as excinfo:
            interpreter.select_tools(
                request="qualquer", computed={}, tools=[], max_iterations=1, max_retries=2
            )

        mensagem = str(excinfo.value)
        assert "apos" in mensagem
        assert "3 tentativa" in mensagem  # max_retries=2 -> 3 tentativas no total
        assert client.invocations == 3

    @pytest.mark.parametrize("excecao", [RuntimeError, TimeoutError])
    def test_run_tool_selection_cai_no_fallback_deterministico(self, excecao):
        client = _AlwaysFailsClient(lambda: excecao("provedor indisponivel"))
        interpreter = _interpreter(client)

        outcome = run_tool_selection(interpreter, request="qualquer", computed={})

        assert outcome.mode == "fallback_deterministico"
        assert outcome.results == {}
        assert "apos" in outcome.fallback_reason
        assert "contrato obrigatorio completo" in outcome.rationale


class TestLimiteDeIteracoes:
    """3: um modelo que sempre propoe mais uma chamada para exatamente no teto."""

    def test_laco_para_no_max_iterations_configurado(self, synthetic_database):
        client = _AlwaysProposesToolClient()
        interpreter = _interpreter(client)

        proposals, _, iterations = interpreter.select_tools(
            request="qualquer",
            computed={},
            tools=[],
            max_iterations=3,
            max_retries=0,
        )

        assert iterations == 3
        assert len(proposals) == 3
        assert client.invocations == 3

    def test_execucao_completa_via_run_tool_selection_tambem_respeita_o_teto(
        self, synthetic_database
    ):
        client = _AlwaysProposesToolClient(tool="get_mortality_rate")
        interpreter = _interpreter(client)

        outcome = run_tool_selection(interpreter, request="qualquer", computed={})

        assert outcome.mode == "tool_calling"
        assert outcome.iterations <= 5  # teto maximo de AGENT_MAX_TOOL_ITERATIONS
        assert outcome.iterations == client.invocations
