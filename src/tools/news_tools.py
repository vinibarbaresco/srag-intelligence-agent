"""Tool de consulta ao Vector DB de noticias.

A busca ocorre sobre o acervo vetorizado pela rotina de ingestao. O orquestrador
tenta atualizar esse acervo antes de cada relatorio e usa a copia persistida
como fallback quando os feeds estiverem indisponiveis.

O retorno traz apenas fatos externos com proveniencia (titulo, fonte, data, URL
e instante de recuperacao).
Nenhum indicador e derivado deste conteudo (Guardrail 5).

Duas fatias distintas saem daqui quando algo falha, e a separacao e o ponto:

* `unavailable_reason` -- frase publicavel, sem caminho de arquivo, PID ou
  numero. E o que o relatorio mostra.
* `technical_detail` -- mensagem integral, destinada a trilha de auditoria e ao
  log, nunca ao relatorio nem ao contexto do modelo.

A separacao nao e estetica. A mensagem bruta de uma trava do DuckDB carrega o
caminho do arquivo e um PID; publicada no relatorio, ela injetava numeros que
nenhuma tool produziu, e o guardrail de evidencia entao bloqueava o texto
inteiro -- uma falha de noticia derrubava a interpretacao deterministica.
"""

from __future__ import annotations

from typing import Any

from src.config import get_settings
from src.guardrails.sanitize import public_reason, technical_detail
from src.news import vector_store
from src.news.vector_store import AccessReport, EmbeddingBackendMismatch, VectorStoreBusy
from src.observability.audit import audited
from src.tools.schemas import NewsQuery

_SOURCE_LABEL = "Vector DB de noticias (Google News RSS, fontes na allowlist)"

#: Frase publicada quando o contexto externo nao pode ser recuperado.
#:
#: Nao contem numero algum, de proposito: ela precisa atravessar o guardrail de
#: evidencia sem lastro nenhum, porque e exatamente nas execucoes em que as
#: tools de noticia falharam que o relatorio mais precisa ser publicado.
NEWS_UNAVAILABLE_NOTICE = (
    "As noticias nao foram atualizadas nesta execucao e nenhum contexto externo "
    "pode ser recuperado. Os indicadores, as series e as limitacoes do relatorio "
    "nao dependem dessa fonte e permanecem validos."
)

NEWS_EMPTY_NOTICE = "Nenhuma noticia do acervo corresponde ao tema consultado na janela informada."

#: Causa publicavel de cada falha conhecida do acervo.
#:
#: O texto e curado por tipo de excecao, e nao derivado da mensagem: assim o
#: relatorio diz o que aconteceu em termos do dominio ("o acervo estava em uso")
#: em vez de repassar um diagnostico de banco de dados ao leitor. A mensagem
#: original vai integra para a auditoria.
_PUBLIC_CAUSES: dict[type[Exception], str] = {
    FileNotFoundError: (
        "O acervo local de noticias ainda nao foi criado nesta instalacao "
        "(a coleta inicial nao foi executada)."
    ),
    EmbeddingBackendMismatch: (
        "O acervo de noticias foi vetorizado com outro backend de embedding e "
        "precisa ser reconstruido antes de voltar a ser consultavel."
    ),
    VectorStoreBusy: (
        "O acervo de noticias estava em uso por outro processo e nao pode ser "
        "lido nesta execucao; o acervo anterior permanece intacto."
    ),
}


def _public_cause(exc: Exception) -> str:
    """Frase publicavel para a falha, sempre seguida do aviso padrao."""
    for kind, cause in _PUBLIC_CAUSES.items():
        if isinstance(exc, kind):
            return f"{cause} {NEWS_UNAVAILABLE_NOTICE}"
    return public_reason(exc, fallback=NEWS_UNAVAILABLE_NOTICE)


@audited("search_srag_news", source=_SOURCE_LABEL)
def search_srag_news(**kwargs: Any) -> dict[str, Any]:
    """Busca semantica por noticias recentes relacionadas ao tema informado.

    Returns:
        Envelope com as noticias encontradas e o estado do acervo. Quando o
        Vector DB esta vazio, travado ou indisponivel, retorna lista vazia com
        `unavailable_reason` publicavel e `technical_detail` para a auditoria --
        o relatorio segue sem contexto externo em vez de falhar.
    """
    query = NewsQuery(**kwargs)
    report = AccessReport(operacao="busca_de_noticias")

    # Janela efetivamente usada: a informada pelo chamador, ou a padrao
    # configurada quando ele a omite. Resolvida aqui -- e nao deixada em
    # aberto na busca -- para que a busca real e o que o relatorio declara
    # como janela nunca divirjam (Guardrail de transparencia da janela).
    effective_max_age_days = (
        query.max_age_days if query.max_age_days is not None else get_settings().news_max_age_days
    )

    try:
        articles = vector_store.search(
            query.query,
            top_k=query.top_k,
            max_age_days=effective_max_age_days,
            report=report,
        )
        unavailable_reason: str | None = None
        detail: str | None = None
    except (FileNotFoundError, EmbeddingBackendMismatch, VectorStoreBusy) as exc:
        articles = []
        unavailable_reason = _public_cause(exc)
        detail = technical_detail(exc)

    store_stats = vector_store.stats(report=report)
    if not articles and unavailable_reason is None:
        unavailable_reason = NEWS_EMPTY_NOTICE

    # As datas publicadas tem de vir dos artigos EFETIVAMENTE recuperados por
    # esta busca (ja filtrados por `effective_max_age_days`), nunca de
    # `store_stats`: aquele bloco descreve o acervo inteiro, sem filtro de
    # janela, e usa-lo aqui fazia o relatorio anunciar como "mais antiga
    # dentro da janela" uma noticia que na verdade estava fora dela -- o
    # acervo pode conter anos de historico enquanto a busca so devolve dias.
    datas_recuperadas = sorted(article["data"] for article in articles if article.get("data"))

    return {
        "query": query.query,
        "articles": articles,
        "total": len(articles),
        "source": _SOURCE_LABEL,
        "vector_db": store_stats,
        "janela_dias": effective_max_age_days,
        "data_mais_recente": datas_recuperadas[-1] if datas_recuperadas else None,
        "data_mais_antiga": datas_recuperadas[0] if datas_recuperadas else None,
        "unavailable_reason": unavailable_reason,
        "technical_detail": detail,
        "acesso_ao_acervo": report.to_dict(),
        "usage_note": (
            "Noticias sao contexto externo. Elas nao alteram, corrigem nem "
            "substituem os indicadores calculados sobre os dados do DATASUS."
        ),
    }
