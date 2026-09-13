"""Testes do agente, do grafo e do relatorio.

O caminho com modelo de linguagem e exercitado com um interpretador de teste --
nao ha chamada de rede na suite. O que se verifica nao e a qualidade do texto do
modelo, e sim que o orquestrador trate corretamente as duas situacoes que
importam: texto valido e texto que viola um guardrail.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.agent.graph import NODE_SEQUENCE
from src.agent.llm import DeterministicNarrator, Interpreter, get_interpreter
from src.agent.orchestrator import run_report
from src.agent.report import render_html, render_markdown
from src.agent.state import initial_state


class FakeInterpreter(Interpreter):
    """Interpretador controlado, no lugar da chamada ao modelo."""

    source = "fake-llm"

    def __init__(self, text: str, selected: list[str] | None = None) -> None:
        self._text = text
        self._selected = selected or ["get_case_growth_rate"]

    def plan(self, request: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
        return {"selected_tools": self._selected, "rationale": "plano de teste"}

    def interpret(self, context: dict[str, Any]) -> str:
        return self._text


@pytest.fixture
def sem_noticias(monkeypatch):
    """Neutraliza a busca de noticias: a suite nao acessa a rede."""
    monkeypatch.setattr(
        "src.news.vector_store.search",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "src.news.vector_store.stats",
        lambda *args, **kwargs: {"noticias_armazenadas": 0, "disponivel": False},
    )


def _run(monkeypatch, interpreter: Interpreter, **kwargs):
    monkeypatch.setattr("src.agent.orchestrator.get_interpreter", lambda **_: interpreter)
    return run_report("Gere o relatorio de SRAG do ultimo mes", **kwargs)


class TestFluxoCompleto:
    def test_executa_todos_os_nos_e_entrega_os_artefatos(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, DeterministicNarrator())

        assert set(state["metrics"]) == {
            "case_growth_rate",
            "mortality_rate",
            "icu_admission_rate",
            "vaccination_coverage_among_cases",
        }
        assert set(state["series"]) == {"daily_cases", "monthly_cases"}
        assert set(state["charts"]) == {"casos_diarios", "casos_mensais"}
        assert state["interpretation"]
        assert state["report_paths"]["markdown"]
        assert state["report_paths"]["html"]

    def test_artefatos_sao_gravados_em_disco(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        from pathlib import Path

        state = _run(monkeypatch, DeterministicNarrator())
        for path in state["report_paths"].values():
            assert Path(path).exists()
        for chart in state["charts"].values():
            assert Path(chart["path"]).exists()

    def test_trilha_de_auditoria_cobre_a_execucao(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        from pathlib import Path

        state = _run(monkeypatch, DeterministicNarrator())
        audit = state["audit_summary"]

        assert audit["run_id"] == state["run_id"]
        assert audit["total_events"] >= len(NODE_SEQUENCE)

        eventos = [
            json.loads(linha)
            for linha in Path(audit["audit_file"]).read_text(encoding="utf-8").splitlines()
        ]
        nos = {evento["node"] for evento in eventos if evento["node"]}
        assert {"validate_request", "collect_epidemiological_metrics",
                "collect_time_series", "search_external_news",
                "validate_evidence", "generate_interpretation",
                "generate_report"} <= nos
        assert [evento["seq"] for evento in eventos] == list(range(1, len(eventos) + 1))

    def test_run_id_e_unico_por_execucao(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        primeiro = _run(monkeypatch, DeterministicNarrator())["run_id"]
        segundo = _run(monkeypatch, DeterministicNarrator())["run_id"]
        assert primeiro != segundo

    def test_recorte_por_uf_e_propagado_as_tools(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, DeterministicNarrator(), uf="SP")
        assert state["metrics"]["mortality_rate"]["filters"]["uf"] == "SP"


class TestPlanejamento:
    def test_plano_do_modelo_nao_suprime_tool_obrigatoria(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        # O modelo seleciona uma unica tool; o relatorio exige todas.
        state = _run(monkeypatch, FakeInterpreter("Texto.", selected=["get_daily_cases"]))

        assert "get_mortality_rate" in state["plan"]["effective_tools"]
        assert len(state["metrics"]) == 4

    def test_plano_e_registrado_com_o_planejador(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, FakeInterpreter("Texto."))
        assert state["plan"]["selected_tools"] == ["get_case_growth_rate"]


class TestGuardrailsNoFluxo:
    def test_pedido_clinico_encerra_antes_de_consultar_dados(
        self, synthetic_database, monkeypatch
    ):
        monkeypatch.setattr(
            "src.agent.orchestrator.get_interpreter", lambda **_: DeterministicNarrator()
        )
        state = run_report("Qual remedio devo tomar para gripe?")

        assert state["validation"]["allowed"] is False
        assert state["validation"]["blocked_by"] == "medical_advice"
        assert not state.get("metrics")
        assert state["report_paths"]["markdown"]

    def test_relatorio_de_recusa_explica_o_motivo(self, synthetic_database, monkeypatch):
        monkeypatch.setattr(
            "src.agent.orchestrator.get_interpreter", lambda **_: DeterministicNarrator()
        )
        state = run_report("Como tratar meu paciente com SRAG?")
        texto = render_markdown(dict(state))

        assert "Solicitacao nao processada" in texto
        assert "medical_advice" in texto

    def test_texto_com_valor_inventado_e_substituido(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(
            monkeypatch,
            FakeInterpreter("A taxa de mortalidade foi de 87,3% neste periodo."),
        )

        assert state["interpretation_source"].startswith("deterministic-template")
        assert "87,3" not in state["interpretation"]
        assert "evidence_binding" in state["guardrail_report"]["resultado"]["blocked_by"]
        assert any("bloqueada" in aviso for aviso in state["warnings"])

    def test_texto_com_dado_pessoal_e_substituido(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, FakeInterpreter("Paciente CPF 123.456.789-00 evoluiu."))
        assert "123.456.789-00" not in state["interpretation"]
        assert "sensitive_data" in state["guardrail_report"]["resultado"]["blocked_by"]

    def test_texto_lastreado_e_preservado(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, FakeInterpreter("O cenario apresenta estabilidade."))
        assert state["interpretation"] == "O cenario apresenta estabilidade."
        assert state["interpretation_source"] == "fake-llm"
        assert state["guardrail_report"]["resultado"]["allowed"] is True

    def test_todas_as_politicas_sao_registradas_no_relatorio(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, DeterministicNarrator())
        assert len(state["guardrail_report"]["politicas_ativas"]) == 6


class TestDegradacaoDeNoticias:
    def test_vector_db_vazio_nao_interrompe_o_relatorio(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, DeterministicNarrator())
        assert state["external_context"]["articles"] == []
        assert state["report_paths"]["markdown"]

    def test_falha_na_busca_vira_aviso_e_nao_excecao(
        self, synthetic_database, monkeypatch
    ):
        def indisponivel(*_args, **_kwargs):
            raise RuntimeError("vector db corrompido")

        monkeypatch.setattr("src.news.vector_store.search", indisponivel)
        state = _run(monkeypatch, DeterministicNarrator())

        assert state["report_paths"]["markdown"]
        assert any("indisponivel" in aviso.lower() for aviso in state["warnings"])


class TestSelecaoDoInterpretador:
    def test_sem_credencial_usa_via_deterministica(self):
        # A conftest neutraliza OPENAI_API_KEY para toda a sessao.
        from src.config import get_settings

        assert get_settings().llm_enabled is False
        assert isinstance(get_interpreter(use_llm=True), DeterministicNarrator)

    def test_no_llm_forca_via_deterministica(self):
        assert isinstance(get_interpreter(use_llm=False), DeterministicNarrator)

    def test_narrador_deterministico_so_cita_valores_das_tools(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        state = _run(monkeypatch, DeterministicNarrator())
        assert state["guardrail_report"]["resultado"]["allowed"] is True


class TestRelatorio:
    @pytest.fixture
    def state(self, synthetic_database, sem_noticias, monkeypatch):
        return dict(_run(monkeypatch, DeterministicNarrator()))

    def test_markdown_separa_dado_inferencia_e_contexto_externo(self, state):
        texto = render_markdown(state)
        assert "## DADO - Indicadores epidemiologicos" in texto
        assert "## INFERENCIA - Interpretacao do cenario" in texto
        assert "## CONTEXTO EXTERNO - Noticias recentes" in texto

    def test_markdown_declara_as_metricas_nao_calculaveis(self, state):
        texto = render_markdown(state)
        assert "Taxa de ocupacao de leitos de UTI: nao calculavel" in texto
        assert "Taxa de vacinacao da populacao: nao calculavel" in texto

    def test_markdown_traz_numerador_denominador_e_fonte(self, state):
        texto = render_markdown(state)
        assert "**Numerador:**" in texto
        assert "**Denominador:**" in texto
        assert "**Fonte:**" in texto

    def test_markdown_traz_run_id_e_trilha(self, state):
        texto = render_markdown(state)
        assert state["run_id"] in texto
        assert "Trilha de auditoria" in texto

    def test_html_e_gerado_e_bem_formado(self, state):
        pagina = render_html(render_markdown(state), state)
        assert pagina.startswith("<!DOCTYPE html>")
        assert pagina.count("<table>") == pagina.count("</table>")
        assert "<h1>" in pagina

    def test_estado_inicial_tem_todos_os_compartimentos(self):
        state = initial_state("run-1", "pedido")
        assert state["metrics"] == {}
        assert state["external_context"] == {}
        assert state["warnings"] == []
