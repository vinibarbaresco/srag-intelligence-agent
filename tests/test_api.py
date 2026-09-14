"""API HTTP: adaptador de entrada sobre tools, grafo e artefatos gravados."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import create_app


@pytest.fixture
def client(synthetic_database, monkeypatch) -> TestClient:
    monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
    monkeypatch.setattr(
        "src.news.vector_store.stats",
        lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
    )
    return TestClient(create_app(), raise_server_exceptions=False)


class TestOperacao:
    def test_health_reporta_banco_e_credencial(self, client):
        payload = client.get("/health").json()
        assert payload["status"] == "ok"
        assert payload["banco_analitico_pronto"] is True
        assert payload["llm_habilitado"] is False
        assert payload["revisao_semantica_habilitada"] is False

    def test_catalogo_de_indicadores_lista_so_indicadores_e_diagnosticos(self, client):
        nomes = {item["nome"] for item in client.get("/indicadores").json()["indicadores"]}
        assert "get_mortality_rate" in nomes
        assert "get_incidence_rate" in nomes
        assert "render_daily_cases_chart" not in nomes
        assert "search_srag_news" not in nomes


class TestIndicadores:
    def test_indicador_devolve_o_mesmo_envelope_da_tool(self, client):
        payload = client.get("/indicadores/get_mortality_rate", params={"uf": "SP"}).json()
        assert payload["metric"] == "mortality_rate"
        assert payload["value"] == pytest.approx(25.0)
        assert payload["numerator"] == 15
        assert payload["denominator"] == 60
        assert "limitations" in payload

    def test_nome_fora_do_registro_e_404(self, client):
        assert client.get("/indicadores/execute_sql").status_code == 404

    def test_grafico_nao_e_acessivel_como_indicador(self, client):
        assert client.get("/indicadores/render_daily_cases_chart").status_code == 404

    def test_uf_invalida_e_422_pela_validacao_da_tool(self, client):
        response = client.get("/indicadores/get_mortality_rate", params={"uf": "XX"})
        assert response.status_code == 422
        assert "Parametros invalidos" in response.json()["detail"]

    def test_injecao_em_parametro_nao_alcanca_o_banco(self, client):
        response = client.get(
            "/indicadores/get_mortality_rate", params={"uf": "SP'; DROP TABLE srag_cases;--"}
        )
        assert response.status_code == 422

    def test_serie_diaria(self, client):
        payload = client.get("/series/get_daily_cases", params={"uf": "SP"}).json()
        assert payload["metric"] == "daily_cases"
        assert payload["summary"]["pontos"] == 30

    def test_indicador_nao_e_acessivel_pela_rota_de_series(self, client):
        assert client.get("/series/get_mortality_rate").status_code == 404


class TestRelatorios:
    def test_relatorio_completo_via_api(self, client):
        response = client.post("/relatorios", json={"uf": "SP", "usar_llm": False})
        assert response.status_code == 200
        payload = response.json()
        assert payload["aceito"] is True
        assert payload["via_de_interpretacao"] == "deterministic-template"
        assert {item["metric"] for item in payload["indicadores"]} >= {
            "mortality_rate",
            "incidence_rate",
            "seasonal_excess",
        }
        assert payload["alertas"]["nivel"] in {"normal", "atencao", "alerta"}
        assert payload["custo_estimado_usd"] == 0

        run_id = payload["run_id"]
        markdown = client.get(f"/relatorios/{run_id}")
        assert markdown.status_code == 200
        assert "DADO - Indicadores epidemiologicos" in markdown.text
        html = client.get(f"/relatorios/{run_id}", params={"formato": "html"})
        assert html.status_code == 200
        assert "<html" in html.text

        audit = client.get(f"/auditoria/{run_id}").json()
        assert audit["total"] >= 8
        assert {event["node"] for event in audit["eventos"] if event["node"]} >= {
            "validate_request",
            "evaluate_alerts",
            "generate_report",
        }

    def test_pedido_clinico_e_recusado_com_motivo(self, client):
        payload = client.post(
            "/relatorios",
            json={"solicitacao": "Qual remedio devo tomar para SRAG?", "usar_llm": False},
        ).json()
        assert payload["aceito"] is False
        assert payload["guardrail_de_entrada"] == "medical_advice"
        assert payload["indicadores"] == []

    def test_campo_desconhecido_no_corpo_e_422(self, client):
        response = client.post("/relatorios", json={"sql": "DROP TABLE srag_cases"})
        assert response.status_code == 422

    def test_run_id_que_nao_e_uuid_e_rejeitado(self, client):
        assert client.get("/relatorios/..%2F..%2F.env").status_code in {404, 422}
        assert client.get("/relatorios/nao-e-uuid").status_code == 422
        assert client.get("/auditoria/nao-e-uuid").status_code == 422

    def test_execucao_inexistente_e_404(self, client):
        assert client.get("/relatorios/00000000-0000-0000-0000-000000000000").status_code == 404

    def test_formato_desconhecido_e_422(self, client):
        response = client.get(
            "/relatorios/00000000-0000-0000-0000-000000000000", params={"formato": "pdf"}
        )
        assert response.status_code == 422
