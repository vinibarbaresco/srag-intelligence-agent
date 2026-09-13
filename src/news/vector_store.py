"""Vector DB de noticias, sobre DuckDB.

E o componente "Vector DB" da arquitetura: a rotina de coleta (`src.news.ingest`)
grava aqui as noticias vetorizadas, e o agente as recupera por similaridade
semantica no momento de interpretar os indicadores.

O armazenamento e uma tabela DuckDB com o embedding em coluna de lista e a
similaridade calculada por `list_cosine_similarity`. Para o volume desta PoC
(dezenas a centenas de noticias) a busca exaustiva e instantanea e dispensa
indice aproximado -- simplicidade preferivel a sofisticacao desnecessaria.

A deduplicacao usa o `article_id` estavel produzido pelo cliente RSS, de modo que
reexecucoes da coleta atualizem em vez de duplicar.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb

from src.config import get_settings
from src.news.embeddings import Embedder, get_embedder
from src.news.rss_client import NewsArticle
from src.observability.logging_config import get_logger

logger = get_logger(__name__)


def _as_naive_utc(moment: datetime) -> datetime:
    """Converte para UTC e remove o fuso.

    O DuckDB so materializa `TIMESTAMP WITH TIME ZONE` em Python com `pytz`
    instalado. Como todos os instantes aqui ja sao UTC, guardar o timestamp sem
    fuso elimina essa dependencia sem perder informacao -- a convencao esta
    documentada no esquema da tabela.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


TABLE_ARTICLES = "news_articles"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_ARTICLES} (
    article_id       VARCHAR PRIMARY KEY,
    title            VARCHAR NOT NULL,
    source           VARCHAR NOT NULL,
    published_at     TIMESTAMP NOT NULL,          -- UTC, sem fuso (ver _as_naive_utc)
    url              VARCHAR NOT NULL,
    query            VARCHAR,
    embedding        DOUBLE[],
    embedding_backend VARCHAR,
    ingested_at      TIMESTAMP NOT NULL           -- UTC, sem fuso
)
"""


@contextmanager
def connect(read_only: bool = False, path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre o Vector DB, criando o esquema quando necessario."""
    store_path = path or get_settings().vector_store_path
    store_path.parent.mkdir(parents=True, exist_ok=True)

    if read_only and not store_path.exists():
        raise FileNotFoundError(
            f"Vector DB de noticias nao encontrado em {store_path}. Execute: "
            "python -m src.news.ingest"
        )

    connection = duckdb.connect(str(store_path), read_only=read_only)
    try:
        if not read_only:
            connection.execute(_SCHEMA_SQL)
        yield connection
    finally:
        connection.close()


def upsert_articles(
    articles: list[NewsArticle],
    embedder: Embedder | None = None,
    path: Path | None = None,
) -> int:
    """Vetoriza e grava noticias, substituindo as ja conhecidas.

    Args:
        articles: noticias coletadas pelo cliente RSS.
        embedder: backend de embedding; escolhido automaticamente se omitido.
        path: caminho alternativo do Vector DB (usado pelos testes).

    Returns:
        Numero de noticias gravadas.
    """
    if not articles:
        return 0

    embedder = embedder or get_embedder()
    vectors = embedder.embed([f"{article.title}. {article.source}" for article in articles])
    now = _as_naive_utc(datetime.now(tz=timezone.utc))

    rows = [
        (
            article.article_id,
            article.title,
            article.source,
            _as_naive_utc(datetime.fromisoformat(article.published_at)),
            article.url,
            article.query,
            vector,
            embedder.backend,
            now,
        )
        for article, vector in zip(articles, vectors)
    ]

    with connect(read_only=False, path=path) as connection:
        connection.executemany(
            f"""
            INSERT OR REPLACE INTO {TABLE_ARTICLES}
                (article_id, title, source, published_at, url, query,
                 embedding, embedding_backend, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    logger.info(
        "noticias gravadas no vector db",
        extra={"quantidade": len(rows), "backend": embedder.backend},
    )
    return len(rows)


def search(
    query: str,
    *,
    top_k: int = 5,
    max_age_days: int | None = None,
    embedder: Embedder | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Busca semantica por noticias relacionadas a `query`.

    Args:
        query: texto da consulta (ex.: "aumento de casos de SRAG em criancas").
        top_k: numero maximo de noticias retornadas.
        max_age_days: limita a busca as noticias publicadas na janela.
        embedder: backend de embedding; deve ser o mesmo usado na ingestao.
        path: caminho alternativo do Vector DB.

    Returns:
        Noticias ordenadas por similaridade decrescente, cada uma com titulo,
        fonte, data, URL e o escore de similaridade.
    """
    embedder = embedder or get_embedder()
    query_vector = embedder.embed([query])[0]

    predicates = ["embedding IS NOT NULL"]
    parameters: list[Any] = [query_vector]
    if max_age_days is not None:
        predicates.append("published_at >= now()::TIMESTAMP - INTERVAL (?) DAY")
        parameters.append(max_age_days)
    parameters.append(top_k)

    with connect(read_only=True, path=path) as connection:
        rows = connection.execute(
            f"""
            SELECT title, source, published_at, url, embedding_backend,
                   list_cosine_similarity(embedding, ?) AS similaridade
            FROM {TABLE_ARTICLES}
            WHERE {' AND '.join(predicates)}
            ORDER BY similaridade DESC NULLS LAST, published_at DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    return [
        {
            "titulo": title,
            "fonte": source,
            "data": published.date().isoformat(),
            "url": url,
            "similaridade": round(float(score), 4) if score is not None else None,
            "embedding_backend": backend,
        }
        for title, source, published, url, backend, score in rows
    ]


def stats(path: Path | None = None) -> dict[str, Any]:
    """Estado atual do Vector DB, para o bloco de auditoria do relatorio."""
    try:
        with connect(read_only=True, path=path) as connection:
            row = connection.execute(
                f"""
                SELECT count(*), min(published_at), max(published_at),
                       max(ingested_at), any_value(embedding_backend)
                FROM {TABLE_ARTICLES}
                """
            ).fetchone()
    except (FileNotFoundError, duckdb.Error):
        return {"noticias_armazenadas": 0, "disponivel": False}

    total, oldest, newest, ingested, backend = row
    return {
        "noticias_armazenadas": int(total),
        "disponivel": bool(total),
        "noticia_mais_antiga": oldest.date().isoformat() if oldest else None,
        "noticia_mais_recente": newest.date().isoformat() if newest else None,
        "ultima_ingestao": ingested.isoformat() if ingested else None,
        "embedding_backend": backend,
    }
