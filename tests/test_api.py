"""API HTTP: adaptador de entrada sobre tools, grafo e artefatos gravados."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import reset_settings_cache


@pytest.fixture
def client(synthetic_database, monkeypatch) -> TestClient:
    monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
    monkeypatch.setattr(
        "src.news.vector_store.stats",
        lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
    )
    return TestClient(create_app(), raise_server_exceptions=False)


def _configured_client(monkeypatch, synthetic_database, **env: str) -> TestClient:
    """Client sobre uma app nova, com variaveis de ambiente da API HTTP customizadas.

    `create_app()` le `get_settings()` na propria criacao (para decidir se
    acopla o middleware de CORS), entao o ambiente precisa estar no lugar
    ANTES da fabrica ser chamada -- diferente do fixture `client`, que usa
    sempre a configuracao padrao. Quem chama e responsavel por
    `monkeypatch.undo()` + `reset_settings_cache()` no fim do teste, no mesmo
    padrao usado no resto da suite (ver `tests/test_metrics.py`).
    """
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
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


class TestAutenticacao:
    def test_sem_token_configurado_todas_rotas_acessiveis_sem_header(self, client):
        """Regressao: o padrao (sem API_AUTH_TOKEN) nao exige header nenhum."""
        assert client.get("/health").status_code == 200
        assert client.get("/indicadores").status_code == 200
        assert client.get("/indicadores/get_mortality_rate", params={"uf": "SP"}).status_code == 200

    def test_sem_header_e_401_quando_token_configurado(self, monkeypatch, synthetic_database):
        api_client = _configured_client(
            monkeypatch, synthetic_database, API_AUTH_TOKEN="segredo-123"
        )
        try:
            response = api_client.get("/indicadores")
            assert response.status_code == 401
            assert "segredo-123" not in response.text
        finally:
            monkeypatch.undo()
            reset_settings_cache()

    def test_header_com_token_errado_e_401(self, monkeypatch, synthetic_database):
        api_client = _configured_client(
            monkeypatch, synthetic_database, API_AUTH_TOKEN="segredo-123"
        )
        try:
            response = api_client.get("/indicadores", headers={"Authorization": "Bearer errado"})
            assert response.status_code == 401
        finally:
            monkeypatch.undo()
            reset_settings_cache()

    def test_header_com_token_correto_e_200(self, monkeypatch, synthetic_database):
        api_client = _configured_client(
            monkeypatch, synthetic_database, API_AUTH_TOKEN="segredo-123"
        )
        try:
            response = api_client.get(
                "/indicadores", headers={"Authorization": "Bearer segredo-123"}
            )
            assert response.status_code == 200
        finally:
            monkeypatch.undo()
            reset_settings_cache()

    def test_health_continua_publico_mesmo_com_token_configurado(
        self, monkeypatch, synthetic_database
    ):
        api_client = _configured_client(
            monkeypatch, synthetic_database, API_AUTH_TOKEN="segredo-123"
        )
        try:
            assert api_client.get("/health").status_code == 200
        finally:
            monkeypatch.undo()
            reset_settings_cache()


class TestRateLimit:
    def test_quarta_requisicao_na_mesma_janela_e_429_com_retry_after(
        self, monkeypatch, synthetic_database
    ):
        api_client = _configured_client(
            monkeypatch, synthetic_database, API_RATE_LIMIT_PER_MINUTE="3"
        )
        try:
            for _ in range(3):
                assert api_client.get("/health").status_code == 200
            response = api_client.get("/health")
            assert response.status_code == 429
            assert response.headers.get("Retry-After") == "60"
        finally:
            monkeypatch.undo()
            reset_settings_cache()


class TestCORS:
    def test_sem_origens_configuradas_resposta_nao_tem_header_cors(self, client):
        response = client.get("/health", headers={"Origin": "https://exemplo.com"})
        assert "access-control-allow-origin" not in response.headers

    def test_origem_permitida_e_refletida_no_header(self, monkeypatch, synthetic_database):
        api_client = _configured_client(
            monkeypatch,
            synthetic_database,
            API_CORS_ALLOWED_ORIGINS="https://painel.exemplo.com",
        )
        try:
            response = api_client.get("/health", headers={"Origin": "https://painel.exemplo.com"})
            assert response.headers.get("access-control-allow-origin") == (
                "https://painel.exemplo.com"
            )
        finally:
            monkeypatch.undo()
            reset_settings_cache()

    def test_origem_fora_da_lista_nao_recebe_header_cors(self, monkeypatch, synthetic_database):
        api_client = _configured_client(
            monkeypatch,
            synthetic_database,
            API_CORS_ALLOWED_ORIGINS="https://painel.exemplo.com",
        )
        try:
            response = api_client.get("/health", headers={"Origin": "https://outro-dominio.com"})
            assert "access-control-allow-origin" not in response.headers
        finally:
            monkeypatch.undo()
            reset_settings_cache()
