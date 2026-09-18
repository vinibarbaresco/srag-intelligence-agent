"""Formatacao escalar, escaping e leitura do relatorio de qualidade.

Utilitarios puros (sem HTML) compartilhados pelas secoes Markdown e pelo
dashboard HTML -- movidos de `report.py` (que concentrava as duas
renderizacoes) sem mudar nenhum comportamento.
"""

from __future__ import annotations

import json
from typing import Any

from src.config import get_settings
from src.observability.logging_config import get_logger

logger = get_logger(__name__)


def _format_value(metric: dict[str, Any]) -> str:
    if metric.get("value") is None:
        return "nao calculavel"
    unit = metric.get("unit", "")
    suffix = "%" if unit == "%" else f" {unit}" if unit else ""
    return f"{metric['value']}{suffix}"


def _dash(value: Any) -> str:
    return "-" if value is None else str(value)


def _format_counts(counts: dict[str, Any] | None) -> str:
    """Resume um mapa `coluna -> contagem`, omitindo zeros."""
    relevant = {column: count for column, count in (counts or {}).items() if count}
    if not relevant:
        return "nenhum"
    return ", ".join(f"`{column}`={count}" for column, count in sorted(relevant.items()))


def _escape_cell(text: str) -> str:
    """Escapa o separador de coluna do Markdown."""
    return str(text).replace("|", "\\|")


def _adjustment_meaning(code: str, adjustments: dict[str, Any]) -> str:
    """Descricao do codigo de ajuste, tolerando o sufixo de coluna."""
    meanings = adjustments.get("significado") or {}
    base = code.split(":", 1)[0]
    return meanings.get(base, base)


def _pt_number(value: float) -> str:
    """Formata um numero no padrao pt-BR (virgula decimal), sem zeros a mais."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        text = "0"
    return text.replace(".", ",")


def _pt_int(value: Any) -> str:
    """Formata um inteiro com separador de milhar pt-BR."""
    if value is None:
        return "-"
    return f"{int(value):,}".replace(",", ".")


def _load_quality_report() -> dict[str, Any]:
    """Le `quality_report.json`; devolve `{}` se ausente ou ilegivel."""
    settings = get_settings()
    path = settings.quality_report_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "relatorio de qualidade ilegivel; secao omitida",
            extra={"path": str(path), "motivo": f"{type(exc).__name__}: {exc}"},
        )
        return {}


def _analysis_cutoff(state: dict[str, Any]) -> str | None:
    """Data de corte analitica, lida da serie diaria ou de qualquer indicador."""
    series = state.get("series") or {}
    cutoff = ((series.get("daily_cases") or {}).get("period") or {}).get("fim")
    if cutoff:
        return cutoff
    for metric in (state.get("metrics") or {}).values():
        cutoff = (metric.get("period") or {}).get("fim")
        if cutoff:
            return cutoff
    return None
