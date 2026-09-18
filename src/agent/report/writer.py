"""Grava o relatorio (Markdown + HTML) em disco.

Movido de `report.py`. Unica funcao deste pacote com efeito colateral de I/O
em disco -- isolada para nao misturar side effects com as funcoes puras de
renderizacao dos demais modulos.
"""

from __future__ import annotations

from typing import Any

from src.config import get_settings
from src.observability.logging_config import get_logger

from .html_dashboard import render_html
from .markdown_sections import render_markdown

logger = get_logger(__name__)


def write_report(state: dict[str, Any]) -> dict[str, str]:
    """Grava o relatorio em Markdown e HTML e devolve os caminhos.

    Args:
        state: estado final do grafo.

    Returns:
        Mapa com as chaves `markdown` e `html`.
    """
    settings = get_settings()
    settings.ensure_directories()
    run_id = state.get("run_id", "sem-run-id")

    markdown_text = render_markdown(state)
    markdown_path = settings.reports_dir / f"relatorio_srag_{run_id}.md"
    markdown_path.write_text(markdown_text, encoding="utf-8")

    html_path = settings.reports_dir / f"relatorio_srag_{run_id}.html"
    html_path.write_text(render_html(markdown_text, state), encoding="utf-8")

    logger.info(
        "relatorio gravado",
        extra={"markdown": str(markdown_path), "html": str(html_path)},
    )
    return {"markdown": str(markdown_path), "html": str(html_path)}
