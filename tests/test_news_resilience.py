"""Resiliencia do acervo de noticias: concorrencia, degradacao e mensagem publica.

Tres propriedades sao verificadas aqui, e as tres vieram de um defeito observado
em execucao (trava concorrente em `news_vectors.duckdb`):

1. escrita e leitura simultaneas nao se derrubam;
2. uma falha de gravacao nunca perde o acervo anterior;
3. o que o relatorio publica sobre a falha nao carrega caminho, PID nem numero
   -- porque numero sem lastro faz o guardrail de evidencia bloquear o texto.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from src.guardrails.output_guard import build_evidence, validate_output
from src.guardrails.sanitize import public_reason, technical_detail
from src.news.embeddings import HashingEmbedder
from src.news.rss_client import NewsArticle
from src.news.vector_store import (
    TABLE_ARTICLES,
    AccessReport,
    VectorStoreBusy,
    _atomic_swap,
    connect,
    search,
    stats,
    upsert_articles,
)
from src.tools.news_tools import (
    NEWS_UNAVAILABLE_NOTICE,
    _public_cause,
    search_srag_news,
)


def _article(index: int, *, title: str | None = None) -> NewsArticle:
    published = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    return NewsArticle(
        article_id=f"art-{index}",
        title=title or f"Casos de SRAG sobem em hospitais da regiao {index}",
        source="agenciabrasil.ebc.com.br",
        published_at=published,
        url=f"https://agenciabrasil.ebc.com.br/noticia-{index}",
        query="SRAG",
    )


@pytest.fixture
def store(tmp_path):
    """Acervo isolado, ja com uma noticia, para cada teste."""
    path = tmp_path / "news_vectors.duckdb"
    upsert_articles([_article(1)], embedder=HashingEmbedder(), path=path)
    return path


class TestTrocaAtomica:
    def test_gravacao_preserva_o_acervo_anterior(self, store):
        upsert_articles([_article(2)], embedder=HashingEmbedder(), path=store)
        with connect(read_only=True, path=store) as connection:
            ids = {
                row[0]
                for row in connection.execute(f"SELECT article_id FROM {TABLE_ARTICLES}").fetchall()
            }
        assert ids == {"art-1", "art-2"}

    def test_arquivo_temporario_nao_sobrevive_a_gravacao(self, store):
        upsert_articles([_article(2)], embedder=HashingEmbedder(), path=store)
        assert not list(store.parent.glob("*.staging-*"))

    def test_falha_na_troca_deixa_o_acervo_intacto(self, store, monkeypatch):
        """Se a promocao falhar, o acervo anterior continua servivel."""

        def sempre_travado(*args, **kwargs):
            raise PermissionError("Acesso negado ao arquivo de destino")

        monkeypatch.setattr("src.news.vector_store.os.replace", sempre_travado)
        monkeypatch.setattr("src.news.vector_store._LOCK_BASE_DELAY", 0.001)

        with pytest.raises(VectorStoreBusy):
            upsert_articles([_article(2)], embedder=HashingEmbedder(), path=store)

        # O acervo anterior permanece legivel e completo.
        assert stats(path=store)["noticias_armazenadas"] == 1
        assert not list(store.parent.glob("*.staging-*"))

    def test_erro_que_nao_e_trava_nao_e_retentado(self, tmp_path, monkeypatch):
        """Retentar um erro de corrupcao so atrasaria o diagnostico."""
        report = AccessReport(operacao="teste")
        chamadas = []

        def falha_estrutural(*args, **kwargs):
            chamadas.append(1)
            raise duckdb.Error("Catalog Error: table does not exist")

        monkeypatch.setattr("src.news.vector_store.os.replace", falha_estrutural)
        with pytest.raises(duckdb.Error):
            _atomic_swap(tmp_path / "a", tmp_path / "b", report)
        assert len(chamadas) == 1
        assert report.resultado == "erro"


class TestConcorrencia:
    def test_ingestao_e_busca_simultaneas_nao_se_derrubam(self, store):
        """O conflito real: a ingestao escreve enquanto a busca le o acervo."""
        erros: list[BaseException] = []
        encontrados: list[int] = []
        parar = threading.Event()

        def ingerindo() -> None:
            indice = 100
            while not parar.is_set():
                try:
                    upsert_articles([_article(indice)], embedder=HashingEmbedder(), path=store)
                except BaseException as exc:  # noqa: BLE001 - o teste e sobre isso
                    erros.append(exc)
                    return
                indice += 1

        def buscando() -> None:
            for _ in range(25):
                try:
                    resultado = search(
                        "SRAG hospitais", top_k=5, embedder=HashingEmbedder(), path=store
                    )
                except FileNotFoundError:
                    # Janela em que o destino ainda nao existe: aceitavel, o
                    # acervo esta sendo promovido. Nao e perda de dado.
                    continue
                except BaseException as exc:  # noqa: BLE001
                    erros.append(exc)
                    return
                encontrados.append(len(resultado))
                time.sleep(0.005)

        escritor = threading.Thread(target=ingerindo, daemon=True)
        leitor = threading.Thread(target=buscando, daemon=True)
        escritor.start()
        leitor.start()
        leitor.join(timeout=60)
        parar.set()
        escritor.join(timeout=60)

        assert not erros, f"acesso concorrente falhou: {erros[:2]}"
        assert encontrados, "nenhuma busca completou durante a ingestao"
        # Toda busca bem-sucedida viu um acervo com conteudo -- nunca vazio pela
        # metade, que e o que a troca atomica existe para impedir.
        assert all(total > 0 for total in encontrados)

    def test_retentativa_e_registrada_no_relatorio_de_acesso(self, store, monkeypatch):
        tentativas = {"n": 0}
        real = duckdb.connect

        def travado_uma_vez(*args, **kwargs):
            tentativas["n"] += 1
            if tentativas["n"] == 1:
                raise duckdb.IOException("Conflicting lock is held in another process")
            return real(*args, **kwargs)

        monkeypatch.setattr("src.news.vector_store.duckdb.connect", travado_uma_vez)
        monkeypatch.setattr("src.news.vector_store._LOCK_BASE_DELAY", 0.001)

        report = AccessReport(operacao="teste")
        with connect(read_only=True, path=store, report=report):
            pass

        assert report.retentativas == 1
        assert report.resultado == "ok_apos_retentativa"
        assert "Conflicting lock" in report.erros[0]

    def test_acervo_permanentemente_travado_declara_ocupado(self, store, monkeypatch):
        monkeypatch.setattr(
            "src.news.vector_store.duckdb.connect",
            lambda *a, **k: (_ for _ in ()).throw(duckdb.IOException("Could not set lock on file")),
        )
        monkeypatch.setattr("src.news.vector_store._LOCK_BASE_DELAY", 0.001)

        report = AccessReport(operacao="teste")
        with pytest.raises(VectorStoreBusy):
            with connect(read_only=True, path=store, report=report):
                pass
        assert report.resultado == "ocupado"
        # Cinco tentativas de abrir o banco, mais a aquisicao da trava do
        # acervo, que usa o mesmo orcamento e o mesmo relatorio.
        assert report.tentativas >= 5


class TestMensagemPublica:
    def test_causa_publica_nao_carrega_caminho_pid_nem_traceback(self):
        exc = duckdb.IOException(
            'IO Error: Could not set lock on file "C:\\dados\\news_vectors.duckdb": '
            "Conflicting lock is held in /usr/bin/python3 (PID 48213)"
        )
        publico = public_reason(exc, fallback=NEWS_UNAVAILABLE_NOTICE)

        assert "C:\\dados" not in publico
        assert "48213" not in publico
        assert "news_vectors.duckdb" not in publico
        assert "/usr/bin" not in publico
        # E o detalhe integral continua disponivel para a auditoria.
        assert "48213" in technical_detail(exc)

    def test_causa_publica_nao_contem_numero_algum(self):
        """Numero em aviso vira valor sem lastro e bloqueia a publicacao."""
        exc = VectorStoreBusy("ocupado apos 5 tentativas em 12.5 segundos")
        publico = _public_cause(exc)
        assert not re.search(r"\d", publico)

    def test_causa_publica_nao_despeja_estrutura_tecnica_de_sdk(self):
        """Achado real (execucao com credencial invalida): o `str()` de uma
        excecao de SDK de API (ex.: `openai.AuthenticationError`) e algo como
        `Error code: 401 - {'error': {'message': '...', 'type': '...'}}`.
        Nada disso e caminho, PID ou numero (o numero vira "N"), mas a chave e
        as aspas de um dict/JSON cru vazavam no relatorio publicado -- exatamente
        o "despejo tecnico" que esta funcao promete nao publicar.
        """
        exc = RuntimeError(
            "Error code: 401 - {'error': {'message': 'Incorrect API key provided: "
            "sk-inval***', 'type': 'invalid_request_error', 'code': 'invalid_api_key', "
            "'param': None}}"
        )
        publico = public_reason(exc, fallback=NEWS_UNAVAILABLE_NOTICE)
        assert publico == NEWS_UNAVAILABLE_NOTICE
        assert "{" not in publico and "}" not in publico
        # O detalhe integral continua disponivel para a auditoria.
        assert "invalid_api_key" in technical_detail(exc)

    def test_aviso_de_indisponibilidade_atravessa_o_guardrail_de_evidencia(self):
        """A falha de noticias nao pode derrubar a interpretacao deterministica."""
        evidence = build_evidence({"mortality_rate": {"value": 7.86, "numerator": 100}})
        texto = (
            "### Leitura do contexto externo\n\n"
            f"{NEWS_UNAVAILABLE_NOTICE}\n\n"
            f"{_public_cause(VectorStoreBusy('travado com PID 9931 em /tmp/x.duckdb'))}"
        )
        resultado = validate_output(texto, evidence)
        assert resultado.allowed, resultado.violations

    def test_envelope_separa_publico_de_tecnico(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SRAG_VECTOR_STORE", str(tmp_path / "inexistente.duckdb"))
        monkeypatch.setattr(
            "src.news.vector_store.search",
            lambda *a, **k: (_ for _ in ()).throw(
                FileNotFoundError(f"Vector DB nao encontrado em {tmp_path}/news.duckdb")
            ),
        )
        monkeypatch.setattr(
            "src.news.vector_store.stats", lambda *a, **k: {"noticias_armazenadas": 0}
        )
        resultado = search_srag_news(query="SRAG", top_k=5, max_age_days=45)

        assert resultado["articles"] == []
        assert "acervo local de noticias ainda nao foi criado" in resultado["unavailable_reason"]
        assert str(tmp_path) not in resultado["unavailable_reason"]
        assert str(tmp_path) in resultado["technical_detail"]


class TestIngestaoDegradada:
    def test_falha_de_gravacao_vira_aviso_publicavel_e_nao_excecao(self, tmp_path, monkeypatch):
        from src.news import ingest as ingest_module
        from src.observability.audit import STATUS_DEGRADED, AuditTrail

        monkeypatch.setattr(
            ingest_module,
            "collect_articles",
            lambda *a, **k: ([_article(9)], []),
        )
        monkeypatch.setattr(
            ingest_module,
            "upsert_articles",
            lambda *a, **k: (_ for _ in ()).throw(
                VectorStoreBusy("acervo travado por PID 771 em /tmp/news.duckdb")
            ),
        )
        monkeypatch.setattr(ingest_module, "stats", lambda *a, **k: {"noticias_armazenadas": 3})

        trail = AuditTrail(audit_dir=tmp_path)
        resumo = ingest_module.ingest_news(trail=trail)

        assert resumo["noticias_gravadas"] == 0
        assert not re.search(r"\d", resumo["gravacao_degradada"])
        assert "771" in resumo["technical_detail"]
        evento = trail.events[-1]
        assert evento.status == STATUS_DEGRADED
        assert "771" in evento.error


class TestJanelaDeNoticias:
    """Transparencia da janela de idade: sem numero magico morto e sem vazamento."""

    def _feed_xml(self, *, pub_date: str, title: str, link: str) -> str:
        return (
            "<rss><channel><item>"
            f"<title>{title}</title>"
            f"<link>{link}</link>"
            f"<pubDate>{pub_date}</pubDate>"
            f'<source url="https://agenciabrasil.ebc.com.br">Agencia Brasil</source>'
            "</item></channel></rss>"
        )

    def test_default_de_collect_articles_usa_a_configuracao_e_nao_30_fixo(self, monkeypatch):
        """`rss_client.py:231` nao pode ter um default morto que nunca e usado."""
        from src.config import get_settings, reset_settings_cache
        from src.news import rss_client

        monkeypatch.setenv("NEWS_MAX_AGE_DAYS", "10")
        reset_settings_cache()
        try:
            assert get_settings().news_max_age_days == 10

            capturados: list = []
            real_build = rss_client._build_article

            def espiao(item, query, horizon, trusted_only):
                capturados.append(horizon)
                return real_build(item, query, horizon, trusted_only)

            from xml.etree import ElementTree

            xml_bytes = self._feed_xml(
                pub_date=(datetime.now(tz=UTC) - timedelta(days=1)).strftime(
                    "%a, %d %b %Y %H:%M:%S GMT"
                ),
                title="Aumento de casos de SRAG no Brasil",
                link="https://agenciabrasil.ebc.com.br/x",
            )
            items = list(ElementTree.fromstring(xml_bytes).iterfind(".//item"))

            monkeypatch.setattr(rss_client, "_build_article", espiao)
            monkeypatch.setattr(rss_client, "fetch_feed", lambda *a, **k: items)

            rss_client.collect_articles(("SRAG",))
            assert capturados, "collect_articles nao chamou _build_article nem uma vez"
            janela_usada = datetime.now(tz=UTC) - capturados[0]
            assert 9.9 <= janela_usada.total_seconds() / 86400 <= 10.1
        finally:
            monkeypatch.delenv("NEWS_MAX_AGE_DAYS", raising=False)
            reset_settings_cache()

    def test_artigo_fora_da_janela_e_descartado_por_collect_articles(self, monkeypatch):
        from xml.etree import ElementTree

        from src.news import rss_client

        pub_date_velha = (datetime.now(tz=UTC) - timedelta(days=100)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        xml_bytes = self._feed_xml(
            pub_date=pub_date_velha,
            title="Aumento de casos de SRAG no Brasil",
            link="https://agenciabrasil.ebc.com.br/velha",
        )
        items = list(ElementTree.fromstring(xml_bytes).iterfind(".//item"))
        monkeypatch.setattr(rss_client, "fetch_feed", lambda *a, **k: items)

        articles, warnings = rss_client.collect_articles(("SRAG",), max_age_days=45)

        assert articles == []
        assert warnings == []

    def test_artigo_dentro_da_janela_e_mantido_por_collect_articles(self, monkeypatch):
        from xml.etree import ElementTree

        from src.news import rss_client

        pub_date_recente = (datetime.now(tz=UTC) - timedelta(days=1)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        xml_bytes = self._feed_xml(
            pub_date=pub_date_recente,
            title="Aumento de casos de SRAG no Brasil",
            link="https://agenciabrasil.ebc.com.br/recente",
        )
        items = list(ElementTree.fromstring(xml_bytes).iterfind(".//item"))
        monkeypatch.setattr(rss_client, "fetch_feed", lambda *a, **k: items)

        articles, _ = rss_client.collect_articles(("SRAG",), max_age_days=45)

        assert len(articles) == 1

    def test_artigo_fora_da_janela_nao_aparece_na_busca_do_vector_store(self, tmp_path):
        """Mesmo ja gravado, a busca com `max_age_days` nao pode devolve-lo."""
        from src.news.embeddings import HashingEmbedder

        store = tmp_path / "janela.duckdb"
        embedder = HashingEmbedder(dimensions=16)
        antiga = NewsArticle(
            article_id="antiga",
            title="Noticia antiga sobre SRAG, fora da janela",
            source="agenciabrasil.ebc.com.br",
            published_at=(datetime.now(tz=UTC) - timedelta(days=100)).isoformat(),
            url="https://agenciabrasil.ebc.com.br/antiga",
            query="SRAG",
        )
        recente = _article(1)
        upsert_articles([antiga, recente], embedder=embedder, path=store)

        resultado = search("SRAG", top_k=10, max_age_days=45, embedder=embedder, path=store)

        titulos = [item["titulo"] for item in resultado]
        assert antiga.title not in titulos
        assert recente.title in titulos
