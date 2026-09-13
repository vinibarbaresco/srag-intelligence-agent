"""Logging estruturado em JSON para toda a aplicacao.

Um unico formato de log, emitido em JSON Lines, permite que a saida de qualquer
etapa (ingestao, tools, nos do grafo) seja lida por maquina sem parsing fragil.
O `run_id` corrente e injetado automaticamente em todo registro.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

#: `run_id` da execucao corrente, propagado automaticamente para os logs.
current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)

_RESERVED_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    """Serializa cada registro de log como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        run_id = current_run_id.get()
        if run_id:
            payload["run_id"] = run_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Instala o formatador JSON no logger raiz (idempotente).

    Args:
        level: nivel minimo de log (`DEBUG`, `INFO`, `WARNING`, ...).
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Bibliotecas de terceiros sao ruidosas em DEBUG e nao acrescentam
    # informacao auditavel sobre o dominio.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger nomeado ja integrado ao formato estruturado."""
    return logging.getLogger(name)
