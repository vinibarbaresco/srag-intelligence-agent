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

import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

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
    return moment.astimezone(UTC).replace(tzinfo=None)


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


# =============================================================================
# Acesso concorrente
# =============================================================================
#
# O DuckDB e um banco embutido de um escritor so: o arquivo e travado por quem
# abre para escrita, e qualquer outra abertura -- inclusive somente leitura --
# falha enquanto a trava existir. Numa execucao do agente isso e um conflito
# real e observado: a rotina de ingestao ESCREVE noticias imediatamente antes de
# a busca semantica LER o mesmo arquivo, e a colisao derrubava o contexto
# externo do relatorio.
#
# Tres medidas resolvem o conflito, e nenhuma delas basta sozinha:
#
# 1. **A escrita nunca toca o arquivo vivo.** A ingestao monta um banco novo em
#    arquivo temporario -- copiando o acervo atual por uma leitura consistente,
#    nao por copia binaria -- e so entao o move para o lugar, em uma unica
#    operacao atomica do sistema de arquivos. Leitores concorrentes continuam
#    com a versao anterior ate o instante da troca, e nunca veem um acervo pela
#    metade.
# 2. **Leitores e escritores sao serializados por um arquivo de trava.** A troca
#    atomica encurta a janela de conflito, mas nao a elimina: no Windows,
#    `os.replace` falha enquanto qualquer processo mantem o destino aberto, e uma
#    busca em laco mantem o arquivo aberto quase o tempo todo. Medido: sem a
#    trava, o escritor esgotava as tentativas e desistia -- nao por contencao
#    passageira, mas por inanicao. A secao critica e de milissegundos, entao o
#    custo de serializar e irrelevante diante do que ele evita.
# 3. **Toda abertura tem retentativa com espera crescente e limitada.** Cobre a
#    contencao residual sem mascarar uma trava real: depois do teto, a falha e
#    declarada em vez de esperada para sempre.
#
# Se qualquer etapa falhar, o acervo anterior permanece intacto -- e o relatorio
# sai com o contexto que ja existia, degradado e declarado, em vez de sem
# contexto nenhum.

#: Tentativas de abrir (ou trocar) o arquivo antes de declarar o banco ocupado.
_LOCK_MAX_ATTEMPTS: Final[int] = 5

#: Espera inicial entre tentativas, em segundos, dobrada a cada retentativa.
_LOCK_BASE_DELAY: Final[float] = 0.05

#: Teto da espera entre tentativas. Com 5 tentativas o pior caso soma menos de
#: 1 segundo: tempo suficiente para atravessar uma trava de escrita curta, e
#: curto o bastante para nao segurar o relatorio diante de uma trava real.
_LOCK_MAX_DELAY: Final[float] = 0.4

#: Trechos que identificam uma falha de TRAVA, e nao de corrupcao ou permissao.
#: Um erro que nao seja de trava e repassado imediatamente: retentar nao ajuda e
#: so atrasaria o diagnostico.
_LOCK_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "conflicting lock",
    "could not set lock",
    "being used by another",
    "lock on file",
    "resource temporarily unavailable",
    "permission denied",
    "acesso negado",
)


class VectorStoreBusy(RuntimeError):
    """O Vector DB seguiu travado por outro processo apos todas as tentativas."""


@dataclass(slots=True)
class AccessReport:
    """O que aconteceu ao acessar o Vector DB, para a trilha de auditoria.

    Existe para que uma retentativa bem-sucedida tambem fique registrada: uma
    trava que hoje e absorvida em 50 ms e o aviso de que amanha ela nao sera.
    """

    operacao: str
    tentativas: int = 0
    retentativas: int = 0
    espera_total_s: float = 0.0
    erros: list[str] = field(default_factory=list)
    resultado: str = "pendente"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operacao": self.operacao,
            "tentativas": self.tentativas,
            "retentativas": self.retentativas,
            "espera_total_s": round(self.espera_total_s, 3),
            "erros": list(self.erros),
            "resultado": self.resultado,
        }


def _is_lock_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _LOCK_ERROR_MARKERS)


def _retrying(operation: str, report: AccessReport | None, action: Any) -> Any:
    """Executa `action`, retentando com espera crescente enquanto houver trava."""
    report = report or AccessReport(operacao=operation)
    delay = _LOCK_BASE_DELAY
    last: BaseException | None = None

    for attempt in range(1, _LOCK_MAX_ATTEMPTS + 1):
        report.tentativas += 1
        try:
            result = action()
        except (duckdb.Error, OSError) as exc:
            if not _is_lock_error(exc):
                report.resultado = "erro"
                report.erros.append(f"{type(exc).__name__}: {exc}")
                raise
            last = exc
            report.erros.append(f"tentativa {attempt}: {type(exc).__name__}: {exc}")
            if attempt == _LOCK_MAX_ATTEMPTS:
                break
            report.retentativas += 1
            time.sleep(delay)
            report.espera_total_s += delay
            delay = min(delay * 2, _LOCK_MAX_DELAY)
        else:
            report.resultado = "ok" if report.retentativas == 0 else "ok_apos_retentativa"
            return result

    report.resultado = "ocupado"
    logger.warning(
        "vector db ocupado apos todas as tentativas",
        extra={"operacao": operation, "tentativas": report.tentativas},
    )
    raise VectorStoreBusy(
        f"O acervo de noticias seguiu em uso por outro processo apos "
        f"{report.tentativas} tentativas ({operation}). O acervo anterior "
        "permanece intacto."
    ) from last


# --- Serializacao entre leitores e escritores --------------------------------
#
# A retentativa sozinha nao resolve o conflito, e a medicao mostrou por que: no
# Windows, `os.replace` falha enquanto QUALQUER processo mantem o destino
# aberto, e uma busca em laco mantem o arquivo aberto quase o tempo todo. O
# escritor entao esgota as tentativas e desiste -- nao por uma trava passageira,
# mas por inanicao.
#
# A correcao e serializar explicitamente, com um arquivo de trava ao lado do
# acervo. Ele e barato (a secao critica e de milissegundos), funciona entre
# processos -- que e o caso real, com a ingestao agendada e o relatorio rodando
# juntos -- e o contador por thread abaixo cobre o caso de threads do mesmo
# processo, que uma trava de arquivo do sistema operacional nao distingue.
_PROCESS_LOCK = threading.RLock()
_REENTRANCY = threading.local()


def _acquire_file_lock(store_path: Path, report: AccessReport | None) -> Any:
    """Trava exclusiva no arquivo sentinela ao lado do acervo, com retentativa."""
    lock_path = store_path.with_name(store_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")

    def attempt() -> bool:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True

    try:
        _retrying("trava_do_acervo", report, attempt)
    except BaseException:
        handle.close()
        raise
    return handle


def _release_file_lock(handle: Any) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:  # fechar o handle ja libera a trava
        logger.debug("liberacao explicita da trava falhou", extra={"motivo": str(exc)})
    finally:
        handle.close()


@contextmanager
def _store_lock(store_path: Path, report: AccessReport | None = None) -> Iterator[None]:
    """Garante acesso exclusivo ao acervo, entre threads e entre processos.

    Reentrante por thread: uma operacao que ja segura a trava pode chamar outra
    que tambem a pediria sem travar a si mesma -- uma trava de arquivo do
    sistema operacional nao tem essa nocao, e sem o contador o segundo pedido
    esperaria pelo primeiro para sempre.
    """
    depth = getattr(_REENTRANCY, "depth", 0)
    if depth:
        _REENTRANCY.depth = depth + 1
        try:
            yield
        finally:
            _REENTRANCY.depth -= 1
        return

    with _PROCESS_LOCK:
        handle = _acquire_file_lock(store_path, report)
        _REENTRANCY.depth = 1
        try:
            yield
        finally:
            _REENTRANCY.depth = 0
            _release_file_lock(handle)


@contextmanager
def connect(
    read_only: bool = False,
    path: Path | None = None,
    *,
    report: AccessReport | None = None,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre o Vector DB com trava e retentativa, criando o esquema se preciso.

    A conexao e sempre fechada na saida do contexto, inclusive em erro, e a
    trava e liberada depois dela: uma conexao vazada mantem o arquivo aberto e
    transforma um conflito pontual em permanente.
    """
    store_path = path or get_settings().vector_store_path
    store_path.parent.mkdir(parents=True, exist_ok=True)

    if read_only and not store_path.exists():
        raise FileNotFoundError(
            f"Vector DB de noticias nao encontrado em {store_path}. Execute: "
            "python -m src.news.ingest"
        )

    operation = "abrir_leitura" if read_only else "abrir_escrita"
    with _store_lock(store_path, report):
        connection = _retrying(
            operation, report, lambda: duckdb.connect(str(store_path), read_only=read_only)
        )
        try:
            if not read_only:
                connection.execute(_SCHEMA_SQL)
            yield connection
        finally:
            connection.close()


def _atomic_swap(staging: Path, target: Path, report: AccessReport | None = None) -> None:
    """Move `staging` para `target` em uma unica operacao do sistema de arquivos.

    `os.replace` e atomico: nenhum leitor observa o destino inexistente ou pela
    metade. No Windows ele falha enquanto outro processo mantem o destino
    aberto, e e por isso que a chamada tambem passa pela retentativa.
    """
    # A troca acontece DENTRO da trava: e a unica forma de garantir que nenhum
    # leitor mantenha o destino aberto no instante do `os.replace`.
    with _store_lock(target, report):
        _retrying("troca_atomica", report, lambda: os.replace(staging, target))
    # O write-ahead log pertencia ao arquivo que acabou de ser substituido.
    # Deixa-lo para tras faria o DuckDB tentar reaplicar, sobre o acervo novo,
    # transacoes do antigo.
    wal = target.with_name(target.name + ".wal")
    try:
        wal.unlink(missing_ok=True)
    except OSError as exc:  # nao impede a troca, que ja aconteceu
        logger.warning("wal anterior nao removido", extra={"motivo": str(exc)})


#: Colunas da tabela, na ordem do INSERT. Uma lista so, para que a copia do
#: acervo e a insercao de novidades nao possam divergir entre si.
_ARTICLE_COLUMNS: Final[tuple[str, ...]] = (
    "article_id",
    "title",
    "source",
    "published_at",
    "url",
    "query",
    "embedding",
    "embedding_backend",
    "ingested_at",
)


def _existing_rows(store_path: Path, report: AccessReport) -> list[tuple[Any, ...]]:
    """Le o acervo atual por uma conexao somente leitura, com retentativa.

    A copia e logica, e nao binaria, de proposito: copiar o arquivo enquanto
    outro processo escreve nele produziria um instantaneo inconsistente. Um
    acervo ausente ou ilegivel devolve lista vazia -- a ingestao entao
    reconstroi, que e o pior caso aceitavel; o inaceitavel seria propagar um
    arquivo corrompido.
    """
    if not store_path.exists():
        return []
    try:
        with connect(read_only=True, path=store_path, report=report) as connection:
            return connection.execute(
                f"SELECT {', '.join(_ARTICLE_COLUMNS)} FROM {TABLE_ARTICLES}"
            ).fetchall()
    except (FileNotFoundError, duckdb.Error) as exc:
        logger.warning(
            "acervo anterior nao pode ser lido; sera reconstruido",
            extra={"motivo": f"{type(exc).__name__}: {exc}"},
        )
        return []


def upsert_articles(
    articles: list[NewsArticle],
    embedder: Embedder | None = None,
    path: Path | None = None,
    *,
    report: AccessReport | None = None,
) -> int:
    """Vetoriza e grava noticias, substituindo as ja conhecidas.

    A gravacao acontece num arquivo temporario ao lado do acervo e so entao e
    promovida por troca atomica (ver a nota sobre acesso concorrente acima).
    Enquanto isso, quem le continua vendo o acervo anterior; se qualquer etapa
    falhar, ele permanece intacto e a excecao sobe para ser declarada.

    Args:
        articles: noticias coletadas pelo cliente RSS.
        embedder: backend de embedding; escolhido automaticamente se omitido.
        path: caminho alternativo do Vector DB (usado pelos testes).
        report: coletor de tentativas e retentativas, para a auditoria.

    Returns:
        Numero de noticias gravadas.

    Raises:
        VectorStoreBusy: se o acervo seguir travado apos todas as tentativas.
    """
    if not articles:
        return 0

    embedder = embedder or get_embedder()
    vectors = embedder.embed([f"{article.title}. {article.source}" for article in articles])
    now = _as_naive_utc(datetime.now(tz=UTC))

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
        for article, vector in zip(articles, vectors, strict=True)
    ]

    store_path = path or get_settings().vector_store_path
    store_path.parent.mkdir(parents=True, exist_ok=True)
    report = report or AccessReport(operacao="ingestao")

    previous = _existing_rows(store_path, report)
    existing_backends = {row[7] for row in previous if row[7] is not None}
    if existing_backends and existing_backends != {embedder.backend}:
        # Vetores de modelos diferentes nao compartilham o mesmo espaco (e
        # frequentemente nem a mesma dimensao). Uma reingestao com outro
        # backend deve reconstruir o pequeno acervo, nunca deixar uma tabela
        # hibrida que falhara durante a similaridade de cosseno.
        previous = []
        logger.warning(
            "acervo de noticias reconstruido por troca de embedding",
            extra={
                "backends_anteriores": sorted(existing_backends),
                "backend_novo": embedder.backend,
            },
        )

    staging = store_path.with_name(f"{store_path.name}.staging-{uuid.uuid4().hex[:12]}")
    columns = ", ".join(_ARTICLE_COLUMNS)
    placeholders = ", ".join("?" for _ in _ARTICLE_COLUMNS)
    try:
        with connect(read_only=False, path=staging, report=report) as connection:
            if previous:
                connection.executemany(
                    f"INSERT OR REPLACE INTO {TABLE_ARTICLES} ({columns}) VALUES ({placeholders})",
                    previous,
                )
            connection.executemany(
                f"INSERT OR REPLACE INTO {TABLE_ARTICLES} ({columns}) VALUES ({placeholders})",
                rows,
            )
        _atomic_swap(staging, store_path, report)
    finally:
        # O temporario so sobrevive a um fracasso; apos a troca ele ja nao
        # existe. A limpeza cobre os DOIS sidecars que o acompanham -- o
        # write-ahead log e o arquivo de trava --, senao uma falha deixaria lixo
        # ao lado do acervo a cada tentativa. Ate no fracasso o acervo original
        # continua servivel.
        for sidecar in ("", ".wal", ".lock"):
            staging.with_name(staging.name + sidecar).unlink(missing_ok=True)

    logger.info(
        "noticias gravadas no vector db",
        extra={
            "quantidade": len(rows),
            "preservadas": len(previous),
            "backend": embedder.backend,
            "retentativas": report.retentativas,
        },
    )
    return len(rows)


class EmbeddingBackendMismatch(RuntimeError):
    """O acervo foi vetorizado com um backend diferente do backend atual.

    Vetores de backends distintos nao sao comparaveis -- tem dimensoes e espacos
    semanticos diferentes. Detectar isso explicitamente evita que o erro apareca
    como uma falha obscura de dimensao vinda do banco.
    """


def stored_backend(path: Path | None = None, *, report: AccessReport | None = None) -> str | None:
    """Backend de embedding com que o acervo atual foi vetorizado."""
    try:
        with connect(read_only=True, path=path, report=report) as connection:
            rows = connection.execute(
                f"SELECT DISTINCT embedding_backend FROM {TABLE_ARTICLES} "
                "WHERE embedding_backend IS NOT NULL ORDER BY 1"
            ).fetchall()
    except (FileNotFoundError, duckdb.Error, VectorStoreBusy):
        return None
    if not rows:
        return None
    backends = [row[0] for row in rows]
    return backends[0] if len(backends) == 1 else f"mistos:{','.join(backends)}"


def search(
    query: str,
    *,
    top_k: int = 5,
    max_age_days: int | None = None,
    embedder: Embedder | None = None,
    path: Path | None = None,
    report: AccessReport | None = None,
) -> list[dict[str, Any]]:
    """Busca semantica por noticias relacionadas a `query`.

    Args:
        query: texto da consulta (ex.: "aumento de casos de SRAG em criancas").
        top_k: numero maximo de noticias retornadas.
        max_age_days: limita a busca as noticias publicadas na janela.
        embedder: backend de embedding; deve ser o mesmo usado na ingestao.
        path: caminho alternativo do Vector DB.
        report: coletor de tentativas e retentativas, para a auditoria.

    Returns:
        Noticias ordenadas por similaridade decrescente, cada uma com titulo,
        fonte, data, URL e o escore de similaridade.

    Raises:
        EmbeddingBackendMismatch: se o acervo foi vetorizado com outro backend.
        VectorStoreBusy: se o acervo seguir travado apos todas as tentativas.
    """
    embedder = embedder or get_embedder()
    report = report or AccessReport(operacao="busca")

    current = stored_backend(path, report=report)
    if current is not None and current != embedder.backend:
        raise EmbeddingBackendMismatch(
            f"O acervo de noticias foi vetorizado com '{current}', mas a busca "
            f"usa '{embedder.backend}'. Vetores de backends diferentes nao sao "
            "comparaveis. Reexecute a ingestao para revetorizar o acervo: "
            "python -m src.news.ingest"
        )

    query_vector = embedder.embed([query])[0]

    predicates = ["embedding IS NOT NULL"]
    parameters: list[Any] = [query_vector]
    if max_age_days is not None:
        # `published_at` e gravado em UTC sem fuso; `now()` do DuckDB usaria o
        # fuso local. O limiar e calculado em Python, na mesma convencao.
        threshold = _as_naive_utc(datetime.now(tz=UTC)) - timedelta(days=max_age_days)
        predicates.append("published_at >= ?")
        parameters.append(threshold)
    parameters.append(top_k)

    with connect(read_only=True, path=path, report=report) as connection:
        rows = connection.execute(
            f"""
            SELECT title, source, published_at, url, embedding_backend, ingested_at,
                   list_cosine_similarity(embedding, ?) AS similaridade
            FROM {TABLE_ARTICLES}
            WHERE {" AND ".join(predicates)}
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
            "publication_date": published.replace(tzinfo=UTC).isoformat(),
            "url": url,
            "retrieved_at": ingested.replace(tzinfo=UTC).isoformat(),
            "similaridade": round(float(score), 4) if score is not None else None,
            "embedding_backend": backend,
        }
        for title, source, published, url, backend, ingested, score in rows
    ]


def stats(path: Path | None = None, *, report: AccessReport | None = None) -> dict[str, Any]:
    """Estado atual do Vector DB, para o bloco de auditoria do relatorio."""
    try:
        with connect(read_only=True, path=path, report=report) as connection:
            row = connection.execute(
                f"""
                SELECT count(*), min(published_at), max(published_at),
                       max(ingested_at), any_value(embedding_backend)
                FROM {TABLE_ARTICLES}
                """
            ).fetchone()
    except (FileNotFoundError, duckdb.Error, VectorStoreBusy) as exc:
        # Estado do acervo e informacao acessoria: nao pode derrubar o
        # relatorio. A causa vai para o bloco, que o relatorio publica como
        # "acervo indisponivel" em vez de omitir a secao.
        return {
            "noticias_armazenadas": 0,
            "disponivel": False,
            "motivo": f"{type(exc).__name__}: {exc}",
        }

    total, oldest, newest, ingested, backend = row
    return {
        "noticias_armazenadas": int(total),
        "disponivel": bool(total),
        "noticia_mais_antiga": oldest.date().isoformat() if oldest else None,
        "noticia_mais_recente": newest.date().isoformat() if newest else None,
        "ultima_ingestao": ingested.isoformat() if ingested else None,
        "embedding_backend": backend,
    }
