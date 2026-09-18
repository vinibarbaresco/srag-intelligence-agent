"""Testes da camada de tools.

Cobrem o contrato de entrada (schemas fechados), o contrato de saida (envelope
completo com proveniencia), a auditoria automatica e a degradacao controlada --
uma tool que falha deve virar envelope de erro, nunca derrubar a execucao.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.observability.audit import AuditTrail
from src.tools.registry import (
    REGISTRY,
    TOOLS,
    UnknownToolError,
    call_tool,
    openai_tool_specs,
)

_INDICADORES = (
    "get_case_growth_rate",
    "get_mortality_rate",
    "get_icu_metrics",
    "get_vaccination_metrics",
)


class TestRegistro:
    def test_catalogo_cobre_todos_os_entregaveis(self):
        assert {
            *_INDICADORES,
            "get_daily_cases",
            "get_monthly_cases",
            "render_daily_cases_chart",
            "render_monthly_cases_chart",
            "search_srag_news",
        } <= set(REGISTRY)

    def test_cada_tool_tem_descricao_e_categoria(self):
        assert all(tool.description and tool.category for tool in TOOLS)

    def test_schemas_de_function_calling_sao_gerados_dos_modelos(self):
        specs = openai_tool_specs()
        assert len(specs) == len(REGISTRY)
        for spec in specs:
            assert spec["type"] == "function"
            assert spec["function"]["parameters"]["type"] == "object"

    def test_tool_desconhecida_levanta_erro_explicito(self):
        with pytest.raises(UnknownToolError, match="nao registrada"):
            call_tool("tool_inexistente")


class TestEnvelopeDeSaida:
    @pytest.mark.parametrize("nome", _INDICADORES)
    def test_indicador_devolve_envelope_completo(self, nome, synthetic_database):
        result = call_tool(nome)
        for campo in (
            "metric",
            "value",
            "unit",
            "numerator",
            "denominator",
            "period",
            "filters",
            "source",
            "definition",
            "limitations",
        ):
            assert campo in result, f"{nome} nao devolveu '{campo}'"

    @pytest.mark.parametrize("nome", _INDICADORES)
    def test_indicador_declara_limitacoes(self, nome, synthetic_database):
        assert call_tool(nome)["limitations"]

    def test_serie_devolve_pontos_periodo_e_resumo(self, synthetic_database):
        result = call_tool("get_daily_cases", {"window_days": 30})
        assert len(result["points"]) == 30
        assert result["summary"]["pontos"] == 30
        assert result["period"]["inicio"] < result["period"]["fim"]

    def test_grafico_devolve_arquivo_existente(self, synthetic_database):
        from pathlib import Path

        result = call_tool("render_monthly_cases_chart")
        assert Path(result["path"]).exists()
        assert result["chart"] == "casos_mensais"


class TestValidacaoDeParametros:
    @pytest.mark.parametrize(
        "parametros",
        [
            {"uf": "XX"},
            {"uf": 42},
            {"classification": 9},
            {"window_days": 0},
            {"window_days": 5000},
            {"campo_inexistente": 1},
        ],
    )
    def test_parametro_invalido_vira_envelope_de_erro(self, parametros, synthetic_database):
        result = call_tool("get_case_growth_rate", parametros)
        assert "error" in result
        assert "Parametros invalidos" in result["error"]

    def test_parametro_valido_e_aceito(self, synthetic_database):
        result = call_tool("get_case_growth_rate", {"uf": "SP", "window_days": 30})
        assert "error" not in result
        assert result["filters"]["uf"] == "SP"

    def test_busca_de_noticias_exige_consulta_minima(self, synthetic_database):
        assert "error" in call_tool("search_srag_news", {"query": "ab"})


class TestAuditoriaDasTools:
    def test_chamada_gera_evento_com_duracao_e_fonte(self, synthetic_database, tmp_path):
        trail = AuditTrail(audit_dir=tmp_path)
        call_tool("get_mortality_rate", {}, trail=trail)

        assert len(trail.events) == 1
        evento = trail.events[0]
        assert evento.tool == "get_mortality_rate"
        assert evento.status == "ok"
        assert evento.duration_ms >= 0
        assert evento.source
        assert "mortality_rate=" in evento.result_summary

    def test_parametros_rejeitados_tambem_sao_auditados(self, synthetic_database, tmp_path):
        trail = AuditTrail(audit_dir=tmp_path)
        call_tool("get_mortality_rate", {"uf": "ZZ"}, trail=trail)

        assert trail.events[0].status == "error"
        assert "rejeitados" in trail.events[0].result_summary

    def test_falha_de_tool_nao_propaga_excecao(self, synthetic_database, tmp_path, monkeypatch):
        """Simula o banco fora do ar durante a execucao de uma tool."""

        def banco_fora_do_ar():
            raise RuntimeError("banco indisponivel")

        monkeypatch.setattr("src.tools.metric_tools.connect", banco_fora_do_ar)
        trail = AuditTrail(audit_dir=tmp_path)
        result = call_tool("get_mortality_rate", {}, trail=trail)

        assert "error" in result
        assert "banco indisponivel" in result["error"]
        # A falha e registrada exatamente uma vez, pelo decorator da tool.
        assert len(trail.events) == 1
        assert trail.events[0].status == "error"

    def test_trilha_e_persistida_em_json_lines(self, synthetic_database, tmp_path):
        import json

        trail = AuditTrail(audit_dir=tmp_path)
        call_tool("get_icu_metrics", {}, trail=trail)

        linhas = trail.path.read_text(encoding="utf-8").strip().splitlines()
        assert len(linhas) == 1
        evento = json.loads(linhas[0])
        assert evento["run_id"] == trail.run_id
        assert evento["tool"] == "get_icu_metrics"

    def test_parametros_sao_mascarados_antes_de_gravar(self, tmp_path):
        trail = AuditTrail(audit_dir=tmp_path)
        trail.record(
            tool="qualquer",
            parameters={"contato": "medico@hospital.com.br"},
            status="ok",
            result_summary="ok",
        )
        assert "medico@hospital.com.br" not in trail.path.read_text(encoding="utf-8")

    def test_resumo_consolida_status_e_duracao(self, synthetic_database, tmp_path):
        trail = AuditTrail(audit_dir=tmp_path)
        call_tool("get_mortality_rate", {}, trail=trail)
        call_tool("get_mortality_rate", {"uf": "ZZ"}, trail=trail)

        resumo = trail.summary()
        assert resumo["total_events"] == 2
        assert resumo["by_status"] == {"ok": 1, "error": 1}


class TestNoticias:
    def test_acervo_vazio_devolve_motivo_e_nao_excecao(self, synthetic_database):
        # O Vector DB nao e populado pelos testes: a tool deve degradar, nao falhar.
        result = call_tool("search_srag_news", {"query": "SRAG casos recentes"})
        assert "error" not in result
        assert result["articles"] == []
        assert result["unavailable_reason"]

    def test_resultado_traz_proveniencia_temporal_da_noticia(self, tmp_path):
        from src.news.embeddings import HashingEmbedder
        from src.news.rss_client import NewsArticle
        from src.news.vector_store import search, upsert_articles

        store = tmp_path / "noticias.duckdb"
        embedder = HashingEmbedder(dimensions=32)
        article = NewsArticle(
            title="Aumento de casos de SRAG no Brasil",
            source="Fonte oficial",
            published_at=datetime.now(tz=UTC).isoformat(),
            url="https://example.org/noticia",
            query="SRAG",
            article_id="noticia-1",
        )
        upsert_articles([article], embedder=embedder, path=store)

        result = search("casos de SRAG", embedder=embedder, path=store)

        assert result[0]["publication_date"]
        assert result[0]["retrieved_at"]
        assert result[0]["url"] == article.url

    def test_troca_de_backend_reconstroi_o_acervo(self, tmp_path):
        from src.news.embeddings import HashingEmbedder
        from src.news.rss_client import NewsArticle
        from src.news.vector_store import search, stored_backend, upsert_articles

        class OutroHashing(HashingEmbedder):
            backend = "outro-backend"

        store = tmp_path / "noticias.duckdb"
        original = HashingEmbedder(dimensions=16)
        replacement = OutroHashing(dimensions=8)

        def article(identifier: str, title: str) -> NewsArticle:
            return NewsArticle(
                title=title,
                source="Fonte oficial",
                published_at=datetime.now(tz=UTC).isoformat(),
                url=f"https://example.org/{identifier}",
                query="SRAG",
                article_id=identifier,
            )

        upsert_articles([article("antiga", "Noticia antiga sobre SRAG")], original, store)
        upsert_articles([article("nova", "Noticia nova sobre SRAG")], replacement, store)

        result = search("SRAG", embedder=replacement, path=store)

        assert stored_backend(store) == replacement.backend
        assert [item["titulo"] for item in result] == ["Noticia nova sobre SRAG"]

    def test_envelope_traz_janela_e_datas_do_acervo(self, tmp_path, monkeypatch):
        """A tool declara a janela efetivamente usada e o intervalo do acervo."""
        from src.news.embeddings import HashingEmbedder
        from src.news.rss_client import NewsArticle
        from src.news.vector_store import search, stats, upsert_articles
        from src.tools.news_tools import search_srag_news

        store = tmp_path / "janela.duckdb"
        embedder = HashingEmbedder(dimensions=16)
        article = NewsArticle(
            title="Aumento de casos de SRAG no Brasil",
            source="Fonte oficial",
            published_at=datetime.now(tz=UTC).isoformat(),
            url="https://example.org/noticia",
            query="SRAG",
            article_id="noticia-janela",
        )
        upsert_articles([article], embedder=embedder, path=store)

        monkeypatch.setattr(
            "src.news.vector_store.search",
            lambda query, **kw: search(
                query,
                top_k=kw.get("top_k", 5),
                max_age_days=kw.get("max_age_days"),
                embedder=embedder,
                path=store,
                report=kw.get("report"),
            ),
        )
        monkeypatch.setattr(
            "src.news.vector_store.stats", lambda **kw: stats(path=store, report=kw.get("report"))
        )

        result = search_srag_news(query="SRAG casos recentes", max_age_days=45)

        assert result["janela_dias"] == 45
        assert result["data_mais_recente"] is not None
        assert result["data_mais_antiga"] is not None

    def test_janela_dias_usa_o_padrao_quando_nao_informada(self, synthetic_database):
        """Sem `max_age_days` explicito, a tool declara a janela padrao configurada."""
        from src.config import get_settings

        result = call_tool("search_srag_news", {"query": "SRAG casos recentes"})
        assert result["janela_dias"] == get_settings().news_max_age_days

    def test_snippet_do_artigo_chega_ao_resultado_da_busca(self, tmp_path):
        """O resumo do feed RSS precisa sobreviver ao armazenamento e a busca."""
        from src.news.embeddings import HashingEmbedder
        from src.news.rss_client import NewsArticle
        from src.news.vector_store import search, upsert_articles

        store = tmp_path / "snippet.duckdb"
        embedder = HashingEmbedder(dimensions=16)
        article = NewsArticle(
            title="Aumento de casos de SRAG no Brasil",
            source="Fonte oficial",
            published_at=datetime.now(tz=UTC).isoformat(),
            url="https://example.org/noticia",
            query="SRAG",
            article_id="noticia-snippet",
            snippet="Autoridades registraram alta de casos na ultima semana.",
        )
        upsert_articles([article], embedder=embedder, path=store)

        resultado = search("casos de SRAG", embedder=embedder, path=store)

        assert resultado[0]["snippet"] == "Autoridades registraram alta de casos na ultima semana."

    def test_snippet_ausente_no_feed_vira_string_vazia(self):
        """`_build_article` nunca falha por descricao ausente no item do feed."""
        from datetime import timedelta
        from xml.etree import ElementTree

        from src.news.rss_client import NewsArticle, _build_article

        pub_date = (datetime.now(tz=UTC) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        xml_bytes = (
            "<item>"
            "<title>Aumento de casos de SRAG no Brasil</title>"
            "<link>https://agenciabrasil.ebc.com.br/sem-descricao</link>"
            f"<pubDate>{pub_date}</pubDate>"
            '<source url="https://agenciabrasil.ebc.com.br">Agencia Brasil</source>'
            "</item>"
        )
        item = ElementTree.fromstring(xml_bytes)
        horizon = datetime.now(tz=UTC) - timedelta(days=45)
        article = _build_article(item, "SRAG", horizon, True)

        assert isinstance(article, NewsArticle)
        assert article.snippet == ""

    def test_snippet_remove_html_colapsa_espacos_e_trunca(self):
        """`_extract_snippet` limpa a descricao bruta do feed e a limita a 280 chars."""
        from xml.etree import ElementTree

        from src.news.rss_client import _extract_snippet

        # A marcacao vem escapada dentro do XML, como o Google News RSS entrega
        # de fato: o texto bruto do elemento contem "<b>...</b>" literal.
        descricao = "&lt;b&gt;Casos&lt;/b&gt; sobem   muito" + " x" * 200
        xml_bytes = f"<item><description>{descricao}</description></item>"
        item = ElementTree.fromstring(xml_bytes)

        snippet = _extract_snippet(item)

        assert "<b>" not in snippet
        assert "  " not in snippet
        assert len(snippet) <= 280
