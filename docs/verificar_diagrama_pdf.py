"""Verifica se `docs/arquitetura.pdf` esta em dia com o codigo.

O PDF e gerado por `docs/gerar_diagrama_pdf.py` a partir de constantes reais do
codigo (nos do grafo, catalogo de tools, guardrails, colunas). Ele deveria,
portanto, poder ser travado pela mesma checagem de "documentacao gerada" que o
CI aplica aos `.md` de `docs/` -- mas `document.save()` do PyMuPDF varia bytes
entre geracoes (metadados internos, IDs de objeto) mesmo quando o CONTEUDO
visivel e identico, o que faria um `git diff --exit-code` binario falhar em
todo run sem que nada tivesse mudado.

Este script existe para fechar esse gap sem esse falso positivo: regera o
diagrama num arquivo temporario e compara o TEXTO extraido pagina a pagina
contra o `arquitetura.pdf` versionado. Uma tool, guardrail, coluna ou no do
grafo que mude sem que o PDF seja regerado aparece como diferenca de texto;
uma diferenca puramente binaria de metadados nunca aparece.

Uso::

    python docs/verificar_diagrama_pdf.py
"""

from __future__ import annotations

import difflib
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docs.gerar_diagrama_pdf import build_diagram  # noqa: E402
from src.config import get_settings  # noqa: E402


def _extract_text(path: Path) -> str:
    with fitz.open(str(path)) as document:
        return "\n".join(page.get_text() for page in document)


def main() -> int:
    committed_path = get_settings().docs_dir / "arquitetura.pdf"
    if not committed_path.exists():
        print(f"ERRO: {committed_path} nao existe. Rode docs/gerar_diagrama_pdf.py.")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        regenerated_path = build_diagram(Path(tmp) / "arquitetura.pdf")
        committed_text = _extract_text(committed_path)
        regenerated_text = _extract_text(regenerated_path)

    if committed_text == regenerated_text:
        print("docs/arquitetura.pdf: conteudo em dia com o codigo.")
        return 0

    diff = "\n".join(
        difflib.unified_diff(
            committed_text.splitlines(),
            regenerated_text.splitlines(),
            fromfile="docs/arquitetura.pdf (commitado)",
            tofile="docs/arquitetura.pdf (regerado agora)",
            lineterm="",
        )
    )
    print("ERRO: docs/arquitetura.pdf esta desatualizado em relacao ao codigo.")
    print("Rode `python docs/gerar_diagrama_pdf.py` e comite o resultado.\n")
    print(diff)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
