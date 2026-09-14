"""Rotina de ingestao de noticias no Vector DB.

Corresponde ao bloco "rotina para fazer a busca de noticias em tempo real" da
arquitetura: coleta os feeds, filtra por fonte confiavel e por tema, vetoriza e
grava no Vector DB. Por padrao, o agente executa esta rotina antes da busca de
cada relatorio. Se a fonte estiver indisponivel, usa o acervo persistido como
fallback e registra a degradacao, preservando a entrega e a rastreabilidade.

Uso::

    python -m src.news.ingest
    python -m src.news.ingest --max-age-days 15
"""

from __future__ import annotations

import argparse
import sys

from src.config import get_settings
from src.news.embeddings import get_embedder
from src.news.rss_client import DEFAULT_QUERIES, collect_articles
from src.news.vector_store import stats, upsert_articles
from src.observability.audit import STATUS_DEGRADED, AuditTrail
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


def ingest_news(
    *,
    max_age_days: int | None = None,
    trail: AuditTrail | None = None,
) -> dict:
    """Coleta, vetoriza e grava noticias no Vector DB.

    Falhas parciais de feed nao interrompem a rotina: elas viram avisos e o
    evento de auditoria e marcado como `degraded`, para que a cobertura reduzida
    fique visivel em vez de passar despercebida.

    Args:
        max_age_days: janela de publicacao; padrao `NEWS_MAX_AGE_DAYS`.
        trail: trilha de auditoria, quando executada dentro de um run do agente.

    Returns:
        Resumo da ingestao, com quantidades e avisos.
    """
    settings = get_settings()
    window = max_age_days or settings.news_max_age_days

    articles, warnings = collect_articles(DEFAULT_QUERIES, max_age_days=window)
    embedder = get_embedder()
    stored = upsert_articles(articles, embedder=embedder)

    summary = {
        "consultas": list(DEFAULT_QUERIES),
        "janela_dias": window,
        "noticias_coletadas": len(articles),
        "noticias_gravadas": stored,
        "feeds_com_falha": warnings,
        "embedding_backend": embedder.backend,
        "vector_db": stats(),
    }

    if trail is not None:
        trail.record(
            node="ingest_news",
            tool="news_ingestion",
            parameters={"janela_dias": window, "consultas": len(DEFAULT_QUERIES)},
            status=STATUS_DEGRADED if warnings else "ok",
            result_summary=(f"{stored} noticias gravadas; {len(warnings)} feeds com falha"),
            source="Google News RSS (fontes na allowlist)",
        )

    return summary


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(description="Coleta noticias sobre SRAG e grava no Vector DB.")
    parser.add_argument("--max-age-days", type=int, default=settings.news_max_age_days)
    args = parser.parse_args(argv)

    summary = ingest_news(max_age_days=args.max_age_days)

    print(f"Noticias coletadas: {summary['noticias_coletadas']}")
    print(f"Noticias gravadas:  {summary['noticias_gravadas']}")
    print(f"Embedding backend:  {summary['embedding_backend']}")
    print(f"Vector DB:          {summary['vector_db']['noticias_armazenadas']} noticias")
    for warning in summary["feeds_com_falha"]:
        print(f"  AVISO: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
