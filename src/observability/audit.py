"""Trilha de auditoria da execucao do agente.

Cada execucao recebe um `run_id` unico. Todo no do grafo e toda tool registram
um :class:`AuditEvent` com o que foi acionado, com quais parametros, quanto
tempo levou, se houve erro e qual a fonte do dado. Os eventos sao persistidos em
JSON Lines (`outputs/audit/<run_id>.jsonl`), fonte primaria escrita evento a
evento, e replicados ao final na tabela `audit_events` do DuckDB, para que
varias execucoes possam ser comparadas com SQL.

Por decisao de projeto **nao** se registra o raciocinio interno do modelo --
apenas eventos operacionais e decisoes observaveis do sistema. Parametros passam
pelo mascarador de dados pessoais antes de serem gravados.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from src.config import get_settings
from src.guardrails.pii import scrub_value
from src.observability.logging_config import current_run_id, get_logger

logger = get_logger(__name__)

T = TypeVar("T")

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_DEGRADED = "degraded"
STATUS_BLOCKED = "blocked"


@dataclass(slots=True)
class AuditEvent:
    """Registro imutavel de uma acao observavel do sistema."""

    run_id: str
    seq: int
    timestamp: str
    node: str | None
    tool: str | None
    parameters: dict[str, Any]
    status: str
    duration_ms: float
    result_summary: str
    source: str | None = None
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


@dataclass
class AuditTrail:
    """Coletor de eventos de uma unica execucao.

    Args:
        run_id: identificador da execucao; gerado automaticamente se omitido.
        audit_dir: diretorio de destino do arquivo JSON Lines.
    """

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    audit_dir: Path | None = None
    events: list[AuditEvent] = field(default_factory=list)
    #: Eventos efetivamente replicados no banco, preenchido por
    #: :meth:`persist_to_database`. Zero significa replica nao realizada.
    persisted_events: int = 0
    _seq: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        settings = get_settings()
        self.audit_dir = self.audit_dir or settings.audit_dir
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        current_run_id.set(self.run_id)

    @property
    def path(self) -> Path:
        """Caminho do arquivo JSON Lines desta execucao."""
        assert self.audit_dir is not None
        return self.audit_dir / f"{self.run_id}.jsonl"

    def record(
        self,
        *,
        status: str,
        result_summary: str,
        node: str | None = None,
        tool: str | None = None,
        parameters: dict[str, Any] | None = None,
        duration_ms: float = 0.0,
        source: str | None = None,
        error: str | None = None,
    ) -> AuditEvent:
        """Grava um evento na trilha e o devolve."""
        self._seq += 1
        event = AuditEvent(
            run_id=self.run_id,
            seq=self._seq,
            timestamp=datetime.now(tz=UTC).isoformat(),
            node=node,
            tool=tool,
            parameters=scrub_value(parameters or {}),
            status=status,
            duration_ms=round(duration_ms, 2),
            result_summary=scrub_value(result_summary),
            source=source,
            error=scrub_value(error) if error else None,
        )
        self.events.append(event)
        self._append_to_file(event)
        logger.info(
            "audit",
            extra={
                "node": event.node,
                "tool": event.tool,
                "status": event.status,
                "duration_ms": event.duration_ms,
            },
        )
        return event

    @contextmanager
    def step(
        self,
        *,
        node: str | None = None,
        tool: str | None = None,
        parameters: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Mede e registra um bloco de execucao.

        O dicionario cedido pelo contexto aceita as chaves `summary`, `status`,
        `source` e `error`, permitindo que o bloco descreva seu proprio
        resultado::

            with trail.step(node="collect_metrics") as ctx:
                ...
                ctx["summary"] = "4 indicadores calculados"

        `error` existe para o caso em que o bloco **tratou** a falha: uma trava
        de acervo absorvida por retentativa, um feed fora do ar. O resumo conta
        o que o sistema fez; `error` guarda o diagnostico integral, que nao pode
        aparecer no relatorio publico mas nao pode se perder.
        """
        context: dict[str, Any] = {
            "summary": "",
            "status": STATUS_OK,
            "source": source,
            "error": None,
        }
        started = time.perf_counter()
        try:
            yield context
        except Exception as exc:  # registrado e repassado ao chamador
            self.record(
                node=node,
                tool=tool,
                parameters=parameters,
                status=STATUS_ERROR,
                result_summary=context.get("summary") or "execucao interrompida",
                duration_ms=(time.perf_counter() - started) * 1000,
                source=context.get("source"),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        else:
            self.record(
                node=node,
                tool=tool,
                parameters=parameters,
                status=context.get("status", STATUS_OK),
                result_summary=context.get("summary") or "concluido",
                duration_ms=(time.perf_counter() - started) * 1000,
                source=context.get("source"),
                error=context.get("error"),
            )

    def _append_to_file(self, event: AuditEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json() + "\n")

    def persist_to_database(self, database_path: Path | None = None) -> int:
        """Replica a trilha na tabela `audit_events` do banco analitico.

        O arquivo JSON Lines e a fonte primaria -- escrito evento a evento,
        sobrevive a uma interrupcao no meio da execucao. Esta replica e gravada
        ao final e existe para que varias execucoes possam ser comparadas com
        SQL (duracao por tool, taxa de falha, evolucao ao longo do tempo).

        A falha aqui nunca derruba a execucao: o relatorio ja esta gravado e a
        trilha ja esta em disco. O problema e apenas registrado.

        Args:
            database_path: banco alternativo (usado pelos testes).

        Returns:
            Numero de eventos replicados; zero quando o banco esta indisponivel.
        """
        if not self.events:
            return 0

        # Import local: `load_database` e detalhe da camada de dados, e a
        # auditoria precisa funcionar mesmo sem banco analitico montado.
        from src.data.load_database import TABLE_AUDIT, connect

        rows = [
            (
                event.run_id,
                event.seq,
                event.timestamp,
                event.node,
                event.tool,
                json.dumps(event.parameters, ensure_ascii=False, default=str),
                event.status,
                event.duration_ms,
                event.result_summary,
                event.source,
                event.error,
            )
            for event in self.events
        ]

        try:
            with connect(read_only=False, path=database_path) as connection:
                connection.execute(f"DELETE FROM {TABLE_AUDIT} WHERE run_id = ?", [self.run_id])
                connection.executemany(
                    f"""
                    INSERT INTO {TABLE_AUDIT}
                        (run_id, seq, timestamp, node, tool, parameters,
                         status, duration_ms, result_summary, source, error)
                    VALUES (?, ?, CAST(? AS TIMESTAMP), ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        except Exception as exc:  # a trilha em disco ja garante a auditabilidade
            logger.warning(
                "trilha nao replicada no banco analitico",
                extra={"motivo": f"{type(exc).__name__}: {exc}"},
            )
            return 0

        logger.info(
            "trilha replicada no banco analitico",
            extra={"run_id": self.run_id, "eventos": len(rows)},
        )
        return len(rows)

    def summary(self) -> dict[str, Any]:
        """Consolida a trilha para exibicao no relatorio."""
        by_status: dict[str, int] = {}
        for event in self.events:
            by_status[event.status] = by_status.get(event.status, 0) + 1
        return {
            "run_id": self.run_id,
            "total_events": len(self.events),
            "by_status": by_status,
            "total_duration_ms": round(sum(e.duration_ms for e in self.events), 2),
            "audit_file": str(self.path),
            "events_in_database": self.persisted_events,
        }


def audited(
    tool_name: str, source: str | None = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator que registra a chamada de uma tool na trilha de auditoria.

    A funcao decorada precisa aceitar o argumento nomeado `trail`. Quando ele nao
    e fornecido, a chamada ocorre normalmente e nada e auditado -- util para
    testes unitarios das funcoes puras.

    Args:
        tool_name: nome da tool como aparecera na trilha e no relatorio.
        source: fonte do dado retornado (ex.: rotulo do DATASUS).
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            trail: AuditTrail | None = kwargs.pop("trail", None)
            if trail is None:
                return func(*args, **kwargs)

            with trail.step(tool=tool_name, parameters=dict(kwargs), source=source) as ctx:
                result = func(*args, **kwargs)
                ctx["summary"] = _summarize(result)
                # Degradacao so vale para tool de metrica que devolveu envelope
                # com valor nulo. A ausencia da chave `value` significa outro
                # formato de retorno (serie, grafico, diagnostico), nao falha.
                if isinstance(result, dict) and "value" in result and result["value"] is None:
                    ctx["status"] = STATUS_DEGRADED
                return result

        return wrapper

    return decorator


def _summarize(result: Any) -> str:
    """Produz um resumo curto e sem dados pessoais do retorno de uma tool.

    O resumo e a unica representacao do resultado que fica na trilha, entao
    precisa dizer algo util para cada formato de retorno das tools -- metrica,
    serie, grafico, busca de noticias ou diagnostico.
    """
    if not isinstance(result, dict):
        if isinstance(result, list):
            return f"{len(result)} itens"
        return str(result)[:200]

    if "value" in result and "metric" in result:
        return f"{result['metric']}={result['value']} {result.get('unit', '')}".strip()

    if "chart" in result:
        return (
            f"grafico {result['chart']} com {result.get('points')} pontos "
            f"gravado em {result.get('path')}"
        )

    if isinstance(result.get("points"), list):
        summary = result.get("summary", {})
        return (
            f"{result.get('metric', 'serie')}: {len(result['points'])} pontos, "
            f"total {summary.get('total_de_casos')}"
        )

    if "articles" in result:
        return f"{result.get('total', 0)} noticias recuperadas"

    keys = ", ".join(sorted(result)[:6])
    return f"dict com chaves: {keys}"
