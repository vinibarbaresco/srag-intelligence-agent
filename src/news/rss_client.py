"""Coleta de noticias recentes sobre SRAG e sindromes respiratorias.

Fonte: feeds RSS do Google News, restritos a uma allowlist de veiculos e orgaos
oficiais. A escolha por RSS e deliberada -- dispensa credenciais, e reprodutivel
por qualquer avaliador e devolve, para cada item, os quatro atributos exigidos
pela especificacao: titulo, fonte, data e URL.

Nenhum conteudo coletado aqui influencia o calculo de indicadores. As noticias
sao contexto externo e circulam no sistema em campo proprio (Guardrail 5).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Final
from urllib.parse import parse_qs, quote_plus, urlparse

import requests

from src.observability.logging_config import get_logger

logger = get_logger(__name__)

_FEED_TEMPLATE: Final[str] = (
    "https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-150"
)
_HTTP_TIMEOUT: Final[tuple[int, int]] = (10, 25)
_USER_AGENT: Final[str] = "srag-intelligence-agent/1.0 (PoC academica)"

#: Consultas padrao, derivadas dos temas exigidos na especificacao.
DEFAULT_QUERIES: Final[tuple[str, ...]] = (
    "SRAG sindrome respiratoria aguda grave",
    "surto respiratorio Brasil",
    "influenza gripe casos Brasil",
    "covid-19 casos hospitalizacao Brasil",
    "virus sincicial respiratorio VSR criancas",
    "lotacao UTI hospitais pressao assistencial",
)

#: Dominios aceitos: orgaos oficiais, institutos de pesquisa e grande imprensa.
TRUSTED_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "gov.br",
        "fiocruz.br",
        "butantan.gov.br",
        "who.int",
        "paho.org",
        "agenciabrasil.ebc.com.br",
        "ebc.com.br",
        "g1.globo.com",
        "globo.com",
        "folha.uol.com.br",
        "uol.com.br",
        "estadao.com.br",
        "oglobo.globo.com",
        "cnnbrasil.com.br",
        "bbc.com",
        "veja.abril.com.br",
        "abril.com.br",
        "correiobraziliense.com.br",
        "band.uol.com.br",
        "r7.com",
        "terra.com.br",
        "metropoles.com",
        "poder360.com.br",
        "nexojornal.com.br",
        "scielo.br",
        "usp.br",
        "unicamp.br",
        "ufrj.br",
    }
)


#: Termos que caracterizam o tema de interesse.
#:
#: A allowlist de dominios garante a confiabilidade da *fonte*, mas nao do
#: *conteudo*: um portal oficial confiavel publica materias sobre qualquer
#: assunto, e o Google News as vezes casa a consulta com o site inteiro. Este
#: filtro garante que a noticia seja de fato sobre sindromes respiratorias.
TOPIC_KEYWORDS: Final[tuple[str, ...]] = (
    "srag",
    "sindrome respiratoria",
    "sindrome respiratoria aguda grave",
    "insuficiencia respiratoria",
    "doenca respiratoria",
    "virus respiratorio",
    "influenza",
    "gripe",
    "h1n1",
    "h3n2",
    "covid",
    "coronavirus",
    "sars",
    "vsr",
    "sincicial",
    "pneumonia",
    "bronquiolite",
    "surto",
    "epidemia",
    "pandemia",
    "vacinacao",
    "vacina",
    "uti",
    "leitos",
    "internacao",
    "hospitalizacao",
)


def is_on_topic(title: str) -> bool:
    """Indica se o titulo trata de sindromes respiratorias ou pressao hospitalar.

    A comparacao usa o titulo normalizado (sem acentos e em caixa baixa), de modo
    que "Sindrome Respiratoria" e "sindrome respiratória" sejam equivalentes.
    """
    normalized = f" {_normalize_title(title)} "
    return any(f" {keyword} " in normalized for keyword in TOPIC_KEYWORDS)


@dataclass(frozen=True, slots=True)
class NewsArticle:
    """Noticia coletada, com proveniencia completa."""

    title: str
    source: str
    published_at: str
    url: str
    query: str
    article_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NewsFetchError(RuntimeError):
    """Falha na coleta de um feed de noticias."""


def _normalize_title(title: str) -> str:
    """Normaliza o titulo para deduplicacao (sem acento, pontuacao ou caixa)."""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]+", " ", ascii_only.lower()).strip()


def _article_id(title: str, url: str) -> str:
    """Identificador estavel para deduplicacao entre execucoes."""
    canonical = f"{_normalize_title(title)}|{_canonical_url(url)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _canonical_url(url: str) -> str:
    """Remove parametros de rastreamento para que a mesma materia colida."""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".rstrip("/").lower()


def _domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_trusted(url: str) -> bool:
    """Indica se a URL pertence a allowlist de fontes confiaveis."""
    domain = _domain_of(url)
    return any(domain == trusted or domain.endswith(f".{trusted}") for trusted in TRUSTED_DOMAINS)


def _publisher_url(item: ElementTree.Element, link: str) -> str:
    """Recupera a URL do veiculo original.

    O Google News publica links de redirecionamento em `news.google.com`. O
    elemento `<source url="...">` traz o dominio do veiculo, que e o que
    realmente importa para avaliar a confiabilidade da fonte.
    """
    source_element = item.find("source")
    if source_element is not None:
        declared = source_element.get("url")
        if declared:
            return declared

    parsed = urlparse(link)
    if parsed.netloc.endswith("news.google.com"):
        embedded = parse_qs(parsed.query).get("url")
        if embedded:
            return embedded[0]
    return link


def _parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch_feed(query: str, *, timeout: tuple[int, int] = _HTTP_TIMEOUT) -> list[ElementTree.Element]:
    """Baixa e parseia um feed RSS do Google News.

    Raises:
        NewsFetchError: em falha de rede ou XML invalido.
    """
    url = _FEED_TEMPLATE.format(query=quote_plus(query))
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except requests.RequestException as exc:
        raise NewsFetchError(f"Falha de rede ao consultar '{query}': {exc}") from exc
    except ElementTree.ParseError as exc:
        raise NewsFetchError(f"Feed invalido para '{query}': {exc}") from exc

    return list(root.iterfind(".//item"))


def collect_articles(
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    *,
    max_age_days: int = 30,
    trusted_only: bool = True,
) -> tuple[list[NewsArticle], list[str]]:
    """Coleta noticias das consultas informadas, deduplicadas e filtradas.

    Args:
        queries: termos de busca.
        max_age_days: descarta itens publicados antes desta janela.
        trusted_only: mantem apenas fontes da allowlist.

    Returns:
        Par `(artigos, avisos)`. Os avisos descrevem feeds que falharam, de modo
        que a degradacao da coleta seja visivel no relatorio em vez de silenciosa.
    """
    horizon = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
    collected: dict[str, NewsArticle] = {}
    warnings: list[str] = []

    for query in queries:
        try:
            items = fetch_feed(query)
        except NewsFetchError as exc:
            warnings.append(str(exc))
            logger.warning("feed indisponivel", extra={"consulta": query, "motivo": str(exc)})
            continue

        for item in items:
            article = _build_article(item, query, horizon, trusted_only)
            if article is not None:
                collected.setdefault(article.article_id, article)

    articles = sorted(collected.values(), key=lambda item: item.published_at, reverse=True)
    logger.info(
        "coleta de noticias concluida",
        extra={
            "consultas": len(queries),
            "artigos": len(articles),
            "feeds_com_falha": len(warnings),
        },
    )
    return articles, warnings


def _build_article(
    item: ElementTree.Element,
    query: str,
    horizon: datetime,
    trusted_only: bool,
) -> NewsArticle | None:
    """Converte um `<item>` do feed em :class:`NewsArticle`, ou descarta."""
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    if not title or not link:
        return None

    published = _parse_published(item.findtext("pubDate"))
    if published is None or published < horizon:
        return None

    if not is_on_topic(title):
        return None

    publisher_url = _publisher_url(item, link)
    if trusted_only and not is_trusted(publisher_url):
        return None

    source_element = item.find("source")
    source_name = (
        (source_element.text or "").strip()
        if source_element is not None and source_element.text
        else _domain_of(publisher_url)
    )

    return NewsArticle(
        title=title,
        source=source_name,
        published_at=published.astimezone(timezone.utc).isoformat(),
        url=link,
        query=query,
        article_id=_article_id(title, publisher_url),
    )
