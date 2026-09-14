"""Regras de alerta e historico de execucoes."""

from __future__ import annotations

import json

import pytest

from src.config import reset_settings_cache
from src.monitoring.alerts import LEVEL_ALERT, LEVEL_ATTENTION, LEVEL_NORMAL, evaluate_alerts
from src.monitoring.history import (
    build_entry,
    compare_runs,
    previous_run,
    read_history,
    record_run,
)


def _metric(key: str, value: float | None, reason: str | None = None) -> dict:
    return {
        "metric": key,
        "value": value,
        "unavailable_reason": reason,
        "components": {"data_corte_analitica": "2026-08-02"},
    }


class TestRegrasDeAlerta:
    def test_dentro_dos_limiares_e_normal(self):
        alerts = evaluate_alerts(
            {
                "case_growth_rate": _metric("case_growth_rate", 5.0),
                "mortality_rate": _metric("mortality_rate", 4.0),
                "seasonal_excess": _metric("seasonal_excess", 10.0),
            }
        )
        assert alerts["nivel"] == LEVEL_NORMAL
        assert alerts["total_disparados"] == 0
        assert len(alerts["regras_avaliadas"]) == 3

    def test_limiar_excedido_dispara_alerta_com_valor_e_limiar(self):
        alerts = evaluate_alerts({"case_growth_rate": _metric("case_growth_rate", 50.0)})
        assert alerts["nivel"] == LEVEL_ALERT
        assert alerts["total_disparados"] == 1
        disparo = alerts["disparados"][0]
        assert disparo["regra"] == "crescimento_de_casos"
        assert "50.0%" in disparo["mensagem"]
        assert "20.0%" in disparo["mensagem"]

    def test_valor_exatamente_no_limiar_nao_dispara(self):
        alerts = evaluate_alerts({"mortality_rate": _metric("mortality_rate", 10.0)})
        assert alerts["total_disparados"] == 0

    def test_indicador_indisponivel_vira_atencao_e_nunca_alerta(self):
        alerts = evaluate_alerts(
            {"seasonal_excess": _metric("seasonal_excess", None, "poucos anos de baseline")}
        )
        assert alerts["nivel"] == LEVEL_ATTENTION
        assert alerts["total_disparados"] == 0
        assert alerts["atencao"][0]["regra"] == "indicador_indisponivel"
        assert "poucos anos" in alerts["atencao"][0]["mensagem"]

    def test_corte_analitico_insuficiente_vira_atencao(self):
        alerts = evaluate_alerts(
            {},
            {
                "get_notification_completeness": {
                    "corte_suficiente": False,
                    "alerta": "O corte configurado (7 dias) e menor que o p75 (13 dias).",
                }
            },
        )
        assert alerts["nivel"] == LEVEL_ATTENTION
        assert alerts["atencao"][0]["regra"] == "corte_analitico_insuficiente"

    def test_limiares_vem_da_configuracao(self, monkeypatch):
        monkeypatch.setenv("ALERT_MORTALITY_THRESHOLD_PCT", "3")
        reset_settings_cache()
        try:
            alerts = evaluate_alerts({"mortality_rate": _metric("mortality_rate", 4.0)})
        finally:
            monkeypatch.undo()
            reset_settings_cache()
        assert alerts["total_disparados"] == 1
        assert alerts["disparados"][0]["limiar"] == 3.0

    def test_alerta_prevalece_sobre_atencao_no_nivel_consolidado(self):
        alerts = evaluate_alerts(
            {
                "case_growth_rate": _metric("case_growth_rate", 90.0),
                "incidence_rate": _metric("incidence_rate", None, "sem populacao"),
            }
        )
        assert alerts["nivel"] == LEVEL_ALERT
        assert "1 regra(s)" in alerts["resumo"]
        assert "1 ponto(s) de atencao" in alerts["resumo"]


class TestHistoricoDeExecucoes:
    def _state(self, run_id: str, growth: float, uf: str | None = None) -> dict:
        return {
            "run_id": run_id,
            "uf": uf,
            "classification": None,
            "metrics": {
                "case_growth_rate": _metric("case_growth_rate", growth),
                "mortality_rate": _metric("mortality_rate", 5.0),
            },
        }

    def test_primeira_execucao_nao_tem_comparacao(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        entry = build_entry(self._state("a", 10.0))
        assert previous_run(None, None, path) is None
        comparison = compare_runs(entry, None)
        assert comparison["execucao_anterior"] is None
        assert "Primeira execucao" in comparison["mensagem"]

    def test_segunda_execucao_compara_com_a_anterior_do_mesmo_recorte(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        record_run(build_entry(self._state("a", 10.0)), path)
        record_run(build_entry(self._state("sp", 99.0, uf="SP")), path)  # outro recorte
        current = build_entry(self._state("b", 12.5))

        previous = previous_run(None, None, path)
        assert previous["run_id"] == "a"
        comparison = compare_runs(current, previous)
        assert comparison["execucao_anterior"] == "a"
        assert comparison["variacao"]["case_growth_rate"] == {
            "anterior": 10.0,
            "atual": 12.5,
            "variacao": 2.5,
        }
        assert comparison["mesma_data_de_corte"] is True

    def test_indicador_ausente_em_uma_das_execucoes_nao_quebra(self, tmp_path):
        previous = build_entry(self._state("a", 10.0))
        previous["indicadores"].pop("mortality_rate")
        comparison = compare_runs(build_entry(self._state("b", 11.0)), previous)
        assert comparison["variacao"]["mortality_rate"]["variacao"] is None

    def test_linha_corrompida_e_ignorada(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        record_run(build_entry(self._state("a", 10.0)), path)
        path.open("a", encoding="utf-8").write("{nao e json\n")
        assert [entry["run_id"] for entry in read_history(path)] == ["a"]

    def test_historico_so_guarda_agregados(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        record_run(build_entry(self._state("a", 10.0)), path)
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert set(record) == {
            "run_id",
            "gerado_em",
            "uf",
            "classification",
            "data_corte_analitica",
            "indicadores",
            "nivel_de_alerta",
            "alertas_disparados",
        }
        assert record["uf"] == "BR"


class TestNoDeAlertasNoGrafo:
    def test_estado_traz_alertas_e_historico(self, synthetic_database, monkeypatch):
        from src.agent.llm import DeterministicNarrator
        from src.agent.orchestrator import run_report

        monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
        monkeypatch.setattr(
            "src.news.vector_store.stats",
            lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
        )
        monkeypatch.setattr(
            "src.agent.orchestrator.get_interpreter", lambda **_: DeterministicNarrator()
        )
        first = run_report("Gere o relatorio de SRAG do ultimo mes", uf="RJ")
        second = run_report("Gere o relatorio de SRAG do ultimo mes", uf="RJ")

        # Base sintetica: crescimento de +50% e letalidade de 25% (SP) disparam;
        # no recorte RJ ha um unico caso, e a regra de celula pequena suprime.
        assert first["alerts"]["nivel"] in {LEVEL_NORMAL, LEVEL_ATTENTION, LEVEL_ALERT}
        assert first["alerts"]["historico"]["execucao_anterior"] is None
        assert second["alerts"]["historico"]["execucao_anterior"] == first["run_id"]
        assert "alerts" in second and "regras_avaliadas" in second["alerts"]

    def test_crescimento_sintetico_dispara_alerta(self, synthetic_database, monkeypatch):
        from src.agent.llm import DeterministicNarrator
        from src.agent.orchestrator import run_report

        monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
        monkeypatch.setattr(
            "src.news.vector_store.stats",
            lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
        )
        monkeypatch.setattr(
            "src.agent.orchestrator.get_interpreter", lambda **_: DeterministicNarrator()
        )
        state = run_report("Gere o relatorio de SRAG do ultimo mes", uf="SP")
        triggered = {item["regra"] for item in state["alerts"]["disparados"]}
        assert {"crescimento_de_casos", "letalidade"} <= triggered
        assert state["alerts"]["nivel"] == LEVEL_ALERT

    def test_fail_on_alert_muda_o_codigo_de_saida(self, synthetic_database, monkeypatch):
        import main as entrypoint
        from src.agent.llm import DeterministicNarrator

        monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
        monkeypatch.setattr(
            "src.news.vector_store.stats",
            lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
        )
        monkeypatch.setattr(
            "src.agent.orchestrator.get_interpreter", lambda **_: DeterministicNarrator()
        )
        status = entrypoint.main(["--no-llm", "--uf", "SP", "--fail-on-alert"])
        assert status == entrypoint.EXIT_ALERT
        assert entrypoint.main(["--no-llm", "--uf", "SP"]) == 0


@pytest.fixture(autouse=True)
def _isolated_history(tmp_path, monkeypatch):
    """Cada teste escreve seu proprio historico, sem herdar o de outros."""
    monkeypatch.setattr(
        "src.monitoring.history._history_path",
        lambda path=None: path or tmp_path / "runs.jsonl",
    )
    yield
