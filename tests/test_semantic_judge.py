"""Revisao semantica independente da saida (segunda camada de guardrail)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.llm import Interpreter
from src.agent.orchestrator import run_report
from src.guardrails.semantic_judge import (
    BLOCKING_CATEGORIES,
    FINDING_CATEGORIES,
    DisabledJudge,
    JudgeVerdict,
    OpenAIJudge,
    SemanticJudge,
    get_semantic_judge,
)


class FakeJudge(SemanticJudge):
    """Revisor controlado: devolve o veredito configurado e registra o texto."""

    source = "fake-judge"

    def __init__(self, findings: list[dict[str, str]] | None = None, error: str | None = None):
        self.findings = findings or []
        self.error = error
        self.reviewed: list[str] = []

    def review(self, text: str, context: dict[str, Any]) -> JudgeVerdict:
        self.reviewed.append(text)
        if self.error:
            return JudgeVerdict(allowed=True, source=self.source, error=self.error)
        blocking = [f for f in self.findings if f["categoria"] in BLOCKING_CATEGORIES]
        return JudgeVerdict(allowed=not blocking, findings=self.findings, source=self.source)


class FakeInterpreter(Interpreter):
    source = "fake-llm"

    def __init__(self, text: str) -> None:
        self._text = text

    def interpret(self, context: dict[str, Any]) -> str:
        return self._text


class TestVeredito:
    def test_achados_mapeiam_para_politicas(self):
        verdict = JudgeVerdict(
            allowed=False,
            findings=[
                {"categoria": "conduta_clinica", "trecho": "procure um medico", "motivo": "x"},
                {"categoria": "instrucao_externa_seguida", "trecho": "conforme", "motivo": "y"},
            ],
            source="fake",
        )
        # So a categoria bloqueante vira violacao; a consultiva vira aviso.
        policies = [item["policy"] for item in verdict.violations()]
        assert policies == ["medical_advice"]
        assert len(verdict.advisories()) == 1
        assert "nao bloqueante" in verdict.advisories()[0]
        assert all(item["type"].startswith("revisao_semantica:") for item in verdict.violations())

    def test_causalidade_indevida_bloqueia_e_mapeia_para_evidence_binding(self):
        """Nova categoria (rodada de guardrail de causalidade): mesma forma das outras."""
        verdict = JudgeVerdict(
            allowed=False,
            findings=[
                {
                    "categoria": "causalidade_indevida",
                    "trecho": "a queda de vacinacao explica o aumento de casos",
                    "motivo": "causalidade sem fonte, sem conectivo lexical explicito",
                }
            ],
            source="fake",
        )
        assert "causalidade_indevida" in BLOCKING_CATEGORIES
        policies = [item["policy"] for item in verdict.violations()]
        assert policies == ["evidence_binding"]
        assert verdict.violations()[0]["type"] == "revisao_semantica:causalidade_indevida"

    def test_todas_as_categorias_tem_politica(self):
        from src.guardrails.policies import ALL_POLICIES

        keys = {policy.key for policy in ALL_POLICIES}
        assert set(FINDING_CATEGORIES.values()) <= keys

    def test_revisor_desativado_aprova_e_se_declara(self):
        verdict = DisabledJudge().review("qualquer texto", {})
        assert verdict.allowed is True
        assert verdict.available is False
        assert verdict.to_dict()["executada"] is False


class TestSelecaoDoRevisor:
    def test_sem_credencial_usa_revisor_desativado(self):
        assert isinstance(get_semantic_judge(use_llm=True), DisabledJudge)

    def test_no_llm_desativa_o_revisor(self):
        assert isinstance(get_semantic_judge(use_llm=False), DisabledJudge)


class TestRevisorOpenAI:
    """Parse da resposta do modelo, com o cliente substituido."""

    def _judge(self, monkeypatch, content: str, usage: dict | None = None) -> OpenAIJudge:
        judge = OpenAIJudge.__new__(OpenAIJudge)
        judge.model = "modelo-teste"
        judge.source = "openai-judge:modelo-teste"
        judge._usage = {"chamadas": 0, "tokens_entrada": 0, "tokens_saida": 0}
        judge._client = SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(
                content=content, usage_metadata=usage or {"input_tokens": 10, "output_tokens": 5}
            )
        )
        return judge

    def test_achado_valido_bloqueia_mesmo_com_aprovado_true(self, monkeypatch):
        judge = self._judge(
            monkeypatch,
            '{"aprovado": true, "achados": [{"categoria": "conduta_clinica", '
            '"trecho": "procure atendimento", "motivo": "orientacao individual"}]}',
        )
        verdict = judge.review("texto", {})
        assert verdict.allowed is False
        assert verdict.findings[0]["categoria"] == "conduta_clinica"

    def test_categoria_desconhecida_e_descartada(self, monkeypatch):
        judge = self._judge(
            monkeypatch,
            '{"aprovado": false, "achados": '
            '[{"categoria": "estilo_ruim", "trecho": "", "motivo": ""}]}',
        )
        assert judge.review("texto", {}).allowed is True

    def test_resposta_ilegivel_e_fail_open_com_erro_declarado(self, monkeypatch):
        judge = self._judge(monkeypatch, "nao consigo avaliar")
        verdict = judge.review("texto", {})
        assert verdict.allowed is True
        assert verdict.error is not None
        assert verdict.available is False

    def test_consumo_de_tokens_e_contabilizado(self, monkeypatch):
        judge = self._judge(monkeypatch, '{"aprovado": true, "achados": []}')
        judge.review("texto", {})
        judge.review("texto", {})
        usage = judge.usage_report()
        assert usage["chamadas"] == 2
        assert usage["tokens_total"] == 30
        assert usage["custo_estimado_usd"] > 0


@pytest.fixture
def sem_noticias(monkeypatch):
    monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
    monkeypatch.setattr(
        "src.news.vector_store.stats",
        lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
    )


def _run(monkeypatch, interpreter: Interpreter, judge: SemanticJudge):
    monkeypatch.setattr("src.agent.orchestrator.get_interpreter", lambda **_: interpreter)
    monkeypatch.setattr("src.agent.orchestrator.get_semantic_judge", lambda **_: judge)
    # Recorte SP: e onde a letalidade sintetica e exatamente 25,0% (o caso de RJ
    # entra no denominador nacional e muda o valor lastreado).
    return run_report("Gere o relatorio de SRAG do ultimo mes", uf="SP")


TEXTO_LASTREADO = "A letalidade entre casos encerrados foi de 25.0%, com 15 obitos em 60 casos."


class TestIntegracaoNoGrafo:
    def test_texto_aprovado_lexicalmente_passa_pelo_revisor(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        judge = FakeJudge()
        state = _run(monkeypatch, FakeInterpreter(TEXTO_LASTREADO), judge)
        assert judge.reviewed == [TEXTO_LASTREADO]
        assert state["interpretation"] == TEXTO_LASTREADO
        assert state["guardrail_report"]["revisao_semantica"]["executada"] is True
        assert state["guardrail_report"]["revisao_semantica"]["aprovado"] is True

    def test_achado_do_revisor_bloqueia_e_cai_na_via_deterministica(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        texto = (
            "A letalidade foi de 25.0%. Quem tiver falta de ar deveria considerar ir ao "
            "pronto atendimento o quanto antes."
        )
        judge = FakeJudge(
            findings=[
                {
                    "categoria": "conduta_clinica",
                    "trecho": "deveria considerar ir ao pronto atendimento",
                    "motivo": "orientacao individual parafraseada",
                }
            ]
        )
        state = _run(monkeypatch, FakeInterpreter(texto), judge)
        assert state["interpretation_source"].startswith("deterministic-template")
        assert "pronto atendimento" not in state["interpretation"]
        assert "medical_advice" in state["guardrail_report"]["resultado"]["blocked_by"]
        violation = state["guardrail_report"]["resultado"]["violations"][0]
        assert violation["type"] == "revisao_semantica:conduta_clinica"

    def test_achado_consultivo_nao_bloqueia_mas_avisa(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        judge = FakeJudge(
            findings=[
                {
                    "categoria": "instrucao_externa_seguida",
                    "trecho": "a noticia aponta",
                    "motivo": "atribuicao a fonte externa",
                }
            ]
        )
        state = _run(monkeypatch, FakeInterpreter(TEXTO_LASTREADO), judge)
        assert state["interpretation"] == TEXTO_LASTREADO
        assert state["guardrail_report"]["resultado"]["allowed"] is True
        assert any("nao bloqueante" in aviso for aviso in state["warnings"])
        assert state["guardrail_report"]["revisao_semantica"]["achados"]

    def test_revisor_indisponivel_e_fail_open_com_aviso(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        judge = FakeJudge(error="TimeoutError: sem resposta")
        state = _run(monkeypatch, FakeInterpreter(TEXTO_LASTREADO), judge)
        assert state["interpretation"] == TEXTO_LASTREADO
        assert any("Revisao semantica" in aviso for aviso in state["warnings"])
        assert state["guardrail_report"]["revisao_semantica"]["erro"] is not None

    def test_falha_do_revisor_fica_no_evento_de_auditoria_formal(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        """Lacuna do auditor: o erro nao pode aparecer so em `warnings`."""
        import json
        from pathlib import Path

        judge = FakeJudge(error="TimeoutError: sem resposta")
        state = _run(monkeypatch, FakeInterpreter(TEXTO_LASTREADO), judge)

        eventos = [
            json.loads(linha)
            for linha in Path(state["audit_summary"]["audit_file"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        evento = next(e for e in eventos if e["node"] == "apply_output_guardrails")
        assert evento["error"] is not None
        assert "TimeoutError" in evento["error"]

    def test_achado_de_causalidade_indevida_bloqueia_e_cai_na_via_deterministica(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        texto = (
            "A letalidade foi de 25.0%. A queda na cobertura vacinal explica o "
            "aumento de casos observado no periodo."
        )
        judge = FakeJudge(
            findings=[
                {
                    "categoria": "causalidade_indevida",
                    "trecho": "a queda na cobertura vacinal explica o aumento de casos",
                    "motivo": "causalidade sem fonte, sem conectivo lexical explicito",
                }
            ]
        )
        state = _run(monkeypatch, FakeInterpreter(texto), judge)
        assert state["interpretation_source"].startswith("deterministic-template")
        assert "explica o aumento" not in state["interpretation"]
        assert "evidence_binding" in state["guardrail_report"]["resultado"]["blocked_by"]
        violation = state["guardrail_report"]["resultado"]["violations"][0]
        assert violation["type"] == "revisao_semantica:causalidade_indevida"

    def test_texto_reprovado_lexicalmente_nao_chega_ao_revisor(
        self, synthetic_database, sem_noticias, monkeypatch
    ):
        judge = FakeJudge()
        state = _run(monkeypatch, FakeInterpreter("A mortalidade foi de 87,3%."), judge)
        assert judge.reviewed == []
        assert "lexicais" in state["guardrail_report"]["revisao_semantica"]["motivo"]

    def test_via_deterministica_nao_e_revisada(self, synthetic_database, sem_noticias, monkeypatch):
        from src.agent.llm import DeterministicNarrator

        judge = FakeJudge()
        state = _run(monkeypatch, DeterministicNarrator(), judge)
        assert judge.reviewed == []
        assert state["guardrail_report"]["revisao_semantica"]["revisor"] == "nao aplicavel"
        assert state["llm_usage"]["custo_total_estimado_usd"] == 0
