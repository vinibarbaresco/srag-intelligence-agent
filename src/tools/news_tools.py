"""Tool de consulta ao Vector DB de noticias.

A busca ocorre sobre o acervo ja coletado e vetorizado pela rotina de ingestao,
nao na internet: isso torna a geracao do relatorio reproduzivel e independente da
disponibilidade dos feeds no instante da execucao.

O retorno traz apenas fatos externos com proveniencia (titulo, fonte, data, URL).
Nenhum indicador e derivado deste conteudo (Guardrail 5).
"""

from __future__ import annotations

from typing import Any

from src.news import vector_store
from src.observability.audit import audited
from src.tools.schemas import NewsQuery

_SOURCE_LABEL = "Vector DB de noticias (Google News RSS, fontes na allowlist)"


@audited("search_srag_news", source=_SOURCE_LABEL)
def search_srag_news(**kwargs: Any) -> dict[str, Any]:
    """Busca semantica por noticias recentes relacionadas ao tema informado.

    Returns:
        Envelope com as noticias encontradas e o estado do acervo. Quando o
        Vector DB esta vazio ou indisponivel, retorna lista vazia com
        `unavailable_reason` preenchido -- o relatorio segue sem contexto
        externo em vez de falhar.
    """
    query = NewsQuery(**kwargs)

    try:
        articles = vector_store.search(
            query.query, top_k=query.top_k, max_age_days=query.max_age_days
        )
        unavailable_reason = None
    except FileNotFoundError as exc:
        articles = []
        unavailable_reason = (
            f"{exc} O relatorio sera gerado sem contexto externo de noticias."
        )

    store_stats = vector_store.stats()
    if not articles and unavailable_reason is None:
        unavailable_reason = (
            "Nenhuma noticia do acervo corresponde ao tema consultado na janela "
            "informada."
        )

    return {
        "query": query.query,
        "articles": articles,
        "total": len(articles),
        "source": _SOURCE_LABEL,
        "vector_db": store_stats,
        "unavailable_reason": unavailable_reason,
        "usage_note": (
            "Noticias sao contexto externo. Elas nao alteram, corrigem nem "
            "substituem os indicadores calculados sobre os dados do DATASUS."
        ),
    }
