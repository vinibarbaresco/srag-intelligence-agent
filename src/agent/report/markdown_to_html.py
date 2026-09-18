"""Motor de conversao Markdown -> HTML e utilitarios de link/imagem seguros.

Movido de `report.py`. E a unica parte do modulo original que faz parsing (em
vez de so formatar valores de estado) -- por isso isolado do resto.
"""

from __future__ import annotations

import html
import re
from typing import Any


def _markdown_to_html(text: str) -> str:
    """Conversor Markdown -> HTML restrito aos elementos usados no relatorio."""
    lines = text.split("\n")
    output: list[str] = []
    in_table = False
    in_list = False

    def close_blocks() -> None:
        nonlocal in_table, in_list
        if in_table:
            output.append("</table>")
            in_table = False
        if in_list:
            output.append("</ul>")
            in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("<details") or stripped.startswith("</details"):
            close_blocks()
            output.append(stripped)
            continue
        if stripped.startswith("<summary"):
            output.append(stripped)
            continue

        if not stripped:
            close_blocks()
            continue

        if re.fullmatch(r"\|[\s:|-]+\|", stripped):
            continue  # linha separadora do cabecalho da tabela

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not in_table:
                close_blocks()
                output.append("<table>")
                in_table = True
                output.append(
                    "<tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in cells) + "</tr>"
                )
            else:
                output.append(
                    "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>"
                )
            continue

        if in_table:
            close_blocks()

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_blocks()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        if stripped.startswith("> "):
            close_blocks()
            output.append(f"<blockquote>{_inline(stripped[2:])}</blockquote>")
            continue

        if stripped.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{_inline(stripped[2:])}</li>")
            continue

        close_blocks()
        output.append(f"<p>{_inline(stripped)}</p>")

    close_blocks()
    return "\n".join(output)


def _inline(text: str) -> str:
    """Aplica formatacao inline (negrito, italico, codigo, link, imagem)."""
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _render_safe_image, escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _render_safe_link, escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def _safe_url(raw: str) -> str | None:
    """Aceita apenas URLs HTTP(S) ou caminhos relativos sem esquema.

    Titulos e links de noticias sao conteudo externo. Esta validacao impede que
    um link malicioso vire `javascript:` ou outro esquema executavel no HTML
    gerado, mesmo se uma fonte upstream for comprometida.
    """
    from urllib.parse import urlparse

    value = html.unescape(raw).strip()
    if not value or any(character in value for character in ("\x00", "\r", "\n")):
        return None

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    if not parsed.scheme and not parsed.netloc and not value.startswith("//"):
        return value
    return None


def _render_safe_link(match: Any) -> str:
    label, raw_url = match.group(1), match.group(2)
    safe = _safe_url(raw_url)
    if safe is None:
        return label
    return f'<a href="{html.escape(safe, quote=True)}">{label}</a>'


def _render_safe_image(match: Any) -> str:
    alt, raw_url = match.group(1), match.group(2)
    safe = _safe_url(raw_url)
    if safe is None:
        return alt
    return f'<img src="{html.escape(safe, quote=True)}" alt="{alt}">'
