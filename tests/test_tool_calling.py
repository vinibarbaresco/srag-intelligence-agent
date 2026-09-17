"""Agente com tool calling real: allowlist, schemas, limites e fallback.

A decisao de arquitetura esta em `src/agent/tool_calling.py`. Estes testes
verificam que ela e o que o codigo faz, e nao apenas o que a documentacao
afirma: o contrato obrigatorio nao muda, o que o modelo pede passa por
allowlist e schema antes de executar, os limites seguram um modelo em laco, e
qualquer falha devolve o fluxo deterministico completo.
"""

from __future__ import annotations

import pytest

from src.agent.tool_calling import (
    OPTIONAL_TOOLS,
    ToolCallingOutcome,
    optional_tool_specs,
    resolve,
    run_tool_selection,
)
from src.config import reset_settings_cache
from src.observability.audit import AuditTrail


class FakeSelector:
    """Interpretador que devolve propostas fixas, sem chamar modelo algum."""

    source = "fake:selector"

    def __init__(self, proposals, rationale="porque sim", iterations=1, raises=None):
        self._proposals = proposals
        self._rationale = rationale
        self._iterations = iterations
        self._raises = raises
        self.calls: list[dict] = []

    def select_tools(self, *, request, computed, tools, max_iterations, max_retries):
        self.calls.append(
            {
                "request": request,
                "computed": computed,
                "tools": tools,
                "max_iterations": max_iterations,
                "max_retries": max_retries,
            }
        )
        if self._raises is not None:
            raise self._raises
        return list(self._proposals), self._rationale, self._iterations


class SemSelecao:
    """Via deterministica: nao tem `select_tools`."""

    source = "deterministic-template"


class TestAllowlist:
    def test_allowlist_e_subconjunto_estrito_do_registro(self):
        from src.tools.registry import REGISTRY

        assert set(OPTIONAL_TOOLS) < set(REGISTRY)

    def test_geradores_de_grafico_ficam_de_fora(self):
        """Efeito colateral em disco nao pertence a uma decisao amostrada."""
        assert "render_daily_cases_chart" not in OPTIONAL_TOOLS
        assert "render_monthly_cases_chart" not in OPTIONAL_TOOLS
        assert resolve("render_daily_cases_chart") is None

    def test_especificacoes_oferecidas_sao_so_as_da_allowlist(self):
        nomes = {spec["function"]["name"] for spec in optional_tool_specs()}
        assert nomes == set(OPTIONAL_TOOLS)

    def test_tool_inexistente_e_recusada_sem_executar(self, synthetic_database):
        selector = FakeSelector([("execute_sql_livre", {"query": "SELECT 1"})])
        outcome = run_tool_selection(selector, request="qualquer", computed={})

        assert outcome.results == {}
        assert len(outcome.rejected) == 1
        assert "fora da allowlist" in outcome.rejected[0].reason

    def test_tool_do_registro_fora_da_allowlist_tambem_e_recusada(self, synthetic_database):
        """Estar no registro nao basta: a allowlist e mais estreita."""
        selector = FakeSelector([("render_daily_cases_chart", {})])
        outcome = run_tool_selection(selector, request="grafico", computed={})

        assert outcome.results == {}
        assert outcome.rejected[0].tool == "render_daily_cases_chart"


class TestValidacaoDeParametros:
    def test_parametro_fora_do_dominio_e_rejeitado_pelo_schema(self, synthetic_database):
        selector = FakeSelector([("get_mortality_rate", {"uf": "XX"})])
        outcome = run_tool_selection(selector, request="letalidade em XX", computed={})

        assert outcome.results == {}
        assert "rejeitados" in outcome.rejected[0].reason

    def test_parametro_desconhecido_e_rejeitado(self, synthetic_database):
        selector = FakeSelector([("get_mortality_rate", {"tabela": "srag_cases"})])
        outcome = run_tool_selection(selector, request="letalidade", computed={})
        assert outcome.results == {}
        assert not outcome.accepted

    def test_chamada_valida_executa_e_entra_no_estado(self, synthetic_database):
        selector = FakeSelector([("get_mortality_rate", {"uf": "RJ"})])
        outcome = run_tool_selection(selector, request="compare com o RJ", computed={})

        assert len(outcome.accepted) == 1
        chave = next(iter(outcome.results))
        assert chave == "get_mortality_rate:uf=RJ"
        assert outcome.results[chave]["metric"] == "mortality_rate"
        assert outcome.results[chave]["filters"]["uf"] == "RJ"


class TestLimites:
    def test_orcamento_de_chamadas_e_respeitado(self, synthetic_database, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "2")
        reset_settings_cache()
        try:
            selector = FakeSelector(
                [
                    ("get_mortality_rate", {"uf": "SP"}),
                    ("get_mortality_rate", {"uf": "RJ"}),
                    ("get_case_growth_rate", {"uf": "SP"}),
                    ("get_case_growth_rate", {"uf": "RJ"}),
                ]
            )
            outcome = run_tool_selection(selector, request="tudo", computed={})
        finally:
            monkeypatch.undo()
            reset_settings_cache()

        assert len(outcome.accepted) == 2
        assert len(outcome.rejected) == 2
        assert all("Orcamento" in item.reason for item in outcome.rejected)

    def test_limites_sao_repassados_ao_selecionador(self, synthetic_database, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_TOOL_ITERATIONS", "3")
        monkeypatch.setenv("AGENT_MAX_TOOL_RETRIES", "2")
        reset_settings_cache()
        try:
            selector = FakeSelector([])
            run_tool_selection(selector, request="x", computed={})
        finally:
            monkeypatch.undo()
            reset_settings_cache()

        assert selector.calls[0]["max_iterations"] == 3
        assert selector.calls[0]["max_retries"] == 2

    def test_desativar_por_configuracao_devolve_modo_deterministico(
        self, synthetic_database, monkeypatch
    ):
        monkeypatch.setenv("AGENT_TOOL_CALLING_ENABLED", "false")
        reset_settings_cache()
        try:
            selector = FakeSelector([("get_mortality_rate", {})])
            outcome = run_tool_selection(selector, request="x", computed={})
        finally:
            monkeypatch.undo()
            reset_settings_cache()

        assert outcome.mode == "deterministico"
        assert outcome.results == {}
        assert selector.calls == []


class TestFallback:
    def test_via_sem_tool_calling_cai_no_modo_deterministico(self, synthetic_database):
        outcome = run_tool_selection(SemSelecao(), request="x", computed={})
        assert outcome.mode == "deterministico"
        assert "nao faz tool calling" in outcome.rationale

    def test_falha_do_modelo_nao_derruba_a_execucao(self, synthetic_database, tmp_path):
        trail = AuditTrail(audit_dir=tmp_path)
        selector = FakeSelector([], raises=RuntimeError("rate limit"))
        outcome = run_tool_selection(selector, request="x", computed={}, trail=trail)

        assert outcome.mode == "fallback_deterministico"
        assert outcome.results == {}
        assert "rate limit" in outcome.fallback_reason
        # A publicacao do relatorio nao pode depender desta etapa.
        assert "contrato obrigatorio completo" in outcome.rationale
        assert trail.events[-1].status == "degraded"


class TestAuditoria:
    def test_decisoes_aceitas_e_recusadas_ficam_na_trilha(self, synthetic_database, tmp_path):
        trail = AuditTrail(audit_dir=tmp_path)
        selector = FakeSelector(
            [("get_mortality_rate", {"uf": "RJ"}), ("tool_inventada", {"x": 1})]
        )
        outcome = run_tool_selection(selector, request="x", computed={}, trail=trail)

        eventos = [event for event in trail.events if event.node == "select_optional_tools"]
        assert any(event.status == "blocked" for event in eventos)
        resumo = eventos[-1].result_summary
        assert "1 tool(s) adicional(is) aceita(s)" in resumo
        assert "1 recusada(s)" in resumo

        publicado = outcome.to_dict()
        assert publicado["tools_aceitas"] == ["get_mortality_rate"]
        assert publicado["tools_recusadas"] == ["tool_inventada"]
        assert publicado["allowlist"] == list(OPTIONAL_TOOLS)
        assert "fora do alcance do modelo" in publicado["contrato_obrigatorio"]


class TestContratoObrigatorio:
    def test_contrato_nao_muda_com_ou_sem_selecao(self, synthetic_database, monkeypatch):
        """O conjunto de indicadores publicados nao depende do modelo."""
        from tests.test_agent import FakeInterpreter, _run

        monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
        monkeypatch.setattr(
            "src.news.vector_store.stats",
            lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
        )
        state = _run(monkeypatch, FakeInterpreter("Texto tecnico."))

        assert set(state["metrics"]) == {
            "case_growth_rate",
            "mortality_rate",
            "icu_admission_rate",
            "icu_bed_occupancy_rate",
            "vaccination_coverage_among_cases",
            "incidence_rate",
            "seasonal_excess",
        }
        assert set(state["charts"]) == {"casos_diarios", "casos_mensais"}
        # A etapa de selecao rodou e declarou o modo, mesmo sem escolher nada.
        assert state["optional_tools"]["modo"] in {"deterministico", "tool_calling"}

    def test_resultado_adicional_entra_no_lastro_de_evidencia(self, synthetic_database):
        """Um valor calculado por tool adicional pode ser citado no texto."""
        from src.guardrails.output_guard import build_evidence

        selector = FakeSelector([("get_mortality_rate", {"uf": "RJ"})])
        outcome = run_tool_selection(selector, request="e no RJ?", computed={})
        chave, payload = next(iter(outcome.results.items()))

        evidence = build_evidence({f"opcional:{chave}": payload})
        assert evidence.supports(float(payload["denominator"]))


class TestPropostasSaoIntencaoNaoExecucao:
    def test_selecionador_nao_executa_nada_por_conta_propria(self, synthetic_database):
        """A camada que fala com o modelo so colhe intencao."""
        selector = FakeSelector([("get_mortality_rate", {"uf": "RJ"})])
        propostas, _, _ = selector.select_tools(
            request="x", computed={}, tools=[], max_iterations=1, max_retries=0
        )
        assert propostas == [("get_mortality_rate", {"uf": "RJ"})]
        # Nada foi para o estado: quem executa e `run_tool_selection`, depois
        # de validar contra allowlist e schema.
        assert not hasattr(selector, "results")


def test_outcome_vazio_e_serializavel():
    outcome = ToolCallingOutcome(mode="deterministico", planner="x")
    publicado = outcome.to_dict()
    assert publicado["decisoes"] == []
    assert publicado["resultados"] == {}
    assert pytest.approx(0) == publicado["iteracoes"]
