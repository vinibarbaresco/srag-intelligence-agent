"""Renderizacao do relatorio epidemiologico.

O relatorio e organizado em tres rotulos que nunca se misturam:

* **DADO** -- valores calculados pelas tools sobre o DATASUS, com numerador,
  denominador, periodo e fonte;
* **INFERENCIA** -- a leitura do cenario produzida pela camada de interpretacao;
* **CONTEXTO EXTERNO** -- noticias, com titulo, fonte, data e URL.

Essa separacao e o que torna cada afirmacao rastreavel e impede que uma leitura
interpretativa seja lida como medicao.

Pacote dividido por responsabilidade (movido de um unico `report.py` de ~3200
linhas):

* `formatting` -- formatacao escalar/escaping puros, compartilhados pelas duas
  camadas de renderizacao;
* `markdown_sections` -- o relatorio Markdown (anexo tecnico completo);
* `html_dashboard` -- o dashboard executivo em HTML;
* `theme` -- CSS/tema/script estaticos e o documento HTML raiz;
* `markdown_to_html` -- o conversor Markdown -> HTML usado pelo anexo tecnico;
* `writer` -- grava os dois formatos em disco.

Apenas as tres funcoes abaixo sao a interface publica deste pacote.
"""

from __future__ import annotations

from .html_dashboard import render_html
from .markdown_sections import render_markdown
from .writer import write_report

__all__ = ["render_html", "render_markdown", "write_report"]
