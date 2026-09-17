"""Gera `docs/arquitetura.pdf` -- o diagrama conceitual exigido na entrega.

O diagrama e um artefato de comunicacao executiva, nao um fluxograma tecnico
cru: organiza a solucao em quatro camadas (Dados, Contexto Externo,
Inteligencia, Apresentacao), cada uma com uma cor propria e restrita, headline
conclusivo, e uma frase de responsabilidade por componente -- para que a
arquitetura se explique em menos de um minuto.

O layout segue uma grade unica declarada em constantes no topo do arquivo:
uma faixa de ator a esquerda (o usuario), quatro faixas horizontais de camada
e tres colunas de largura igual. As setas de longo alcance andam por
corredores verticais reservados -- espacos que a grade deixa livres de
proposito --, de modo que nenhuma linha cruze outra nem passe por cima de uma
caixa, e os rotulos de seta so ocupam os vaos entre caixas. Cada bloco de
texto e inserido com verificacao de transbordo: se algum conteudo deixar de
caber na area que lhe foi reservada, a geracao falha em vez de produzir uma
pagina com texto cortado ou sobreposto.

Ele e produzido por codigo, a partir das mesmas constantes usadas pela
aplicacao (nos do grafo, catalogo de tools, politicas de guardrail, colunas do
contrato de dados). Assim ele nao se descola da implementacao: acrescentar uma
tool, um guardrail ou um no muda o diagrama na proxima geracao -- e
`docs/verificar_diagrama_pdf.py` trava, no CI, que essa geracao esteja em dia.

Duas paginas:
  1. Arquitetura executiva -- as quatro camadas, o fluxo principal e a faixa
     transversal de governanca.
  2. Fluxo de execucao -- os oito passos de uma chamada real, numerados.

Uso::

    python docs/gerar_diagrama_pdf.py
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.graph import NODE_SEQUENCE  # noqa: E402
from src.config import DATASUS_SOURCE_LABEL, DELIVERY_LABEL, get_settings  # noqa: E402
from src.data.schema import ALL_DERIVED_COLUMNS, ALLOWED_COLUMNS  # noqa: E402
from src.guardrails.policies import ALL_POLICIES  # noqa: E402
from src.news.embeddings import HASHING_DIMENSIONS  # noqa: E402
from src.news.rss_client import DEFAULT_QUERIES, TRUSTED_DOMAINS  # noqa: E402
from src.tools.registry import TOOLS  # noqa: E402

PAGE_WIDTH, PAGE_HEIGHT = 1191, 842  # A3 paisagem
MARGIN = 48

# Nome sob o qual o PDF e entregue, impresso no rodape das duas paginas.
#
# E uma constante, e nao o caminho de saida recebido por `build_diagram`:
# `docs/verificar_diagrama_pdf.py` regera o diagrama num arquivo temporario e
# compara o texto extraido com o do PDF versionado. Derivar este rotulo do
# caminho real faria os dois textos diferirem sempre, e a checagem do CI
# falharia em toda execucao.
DELIVERED_AS = "docs/arquitetura.pdf"

# =============================================================================
# Paleta -- uma cor por camada, restrita. Nada de "arvore de Natal".
# =============================================================================
INK = (0.10, 0.12, 0.16)
MUTED = (0.43, 0.46, 0.51)
LINE = (0.68, 0.71, 0.76)
HAIRLINE = (0.84, 0.86, 0.89)
PAPER = (1, 1, 1)

LAYER_DATA = (0.11, 0.33, 0.42)
LAYER_EXTERNAL = (0.62, 0.36, 0.11)
LAYER_INTELLIGENCE = (0.35, 0.22, 0.46)
LAYER_PRESENTATION = (0.13, 0.40, 0.32)
LAYER_GOVERNANCE = (0.36, 0.38, 0.42)

FILL_DATA = (0.90, 0.94, 0.96)
FILL_EXTERNAL = (0.98, 0.93, 0.87)
FILL_INTELLIGENCE = (0.94, 0.91, 0.96)
FILL_PRESENTATION = (0.90, 0.95, 0.93)
FILL_GOVERNANCE = (0.95, 0.95, 0.96)
FILL_USER = (0.96, 0.96, 0.97)

# =============================================================================
# Grade da pagina 1 -- toda posicao do diagrama sai daqui.
#
# Horizontal: faixa do ator (usuario) | area de conteudo em tres colunas.
# Vertical:   cabecalho | quatro faixas de camada | faixa de governanca | rodape.
#
# Os vaos entre colunas (COL_GAP) sao dimensionados para caber o rotulo da
# seta que os atravessa; os corredores verticais (CORRIDOR_*) sao as faixas de
# x que nenhuma caixa ocupa, por onde passam as setas de longo alcance.
# =============================================================================
HEADER_RULE_Y = 110

RAIL_X0, RAIL_X1 = MARGIN, 198
CONTENT_X0, CONTENT_X1 = 222, PAGE_WIDTH - MARGIN
COL_GAP = 62.0
COL_WIDTH = (CONTENT_X1 - CONTENT_X0 - 2 * COL_GAP) / 3
COL1_X0 = CONTENT_X0
COL1_X1 = COL1_X0 + COL_WIDTH
COL2_X0 = COL1_X1 + COL_GAP
COL2_X1 = COL2_X0 + COL_WIDTH
COL3_X0 = COL2_X1 + COL_GAP
COL3_X1 = CONTENT_X1

BAND1_Y0, BAND1_Y1 = 136.0, 268.0  # dados
BAND2_Y0, BAND2_Y1 = 306.0, 418.0  # contexto externo
BAND3_Y0, BAND3_Y1 = 456.0, 644.0  # inteligencia
BAND4_Y0, BAND4_Y1 = 682.0, 770.0  # apresentacao

# A camada 2 tem duas caixas em vez de tres, alinhadas a mesma grade: a
# terceira coluna fica livre de proposito -- e o corredor por onde a consulta
# ao banco desce, atravessando esta faixa, ate as tools.
BAND2_NEWS_X0, BAND2_NEWS_X1 = COL1_X0, COL1_X1
BAND2_VECTOR_X0 = COL2_X0

GOVERNANCE_Y0, GOVERNANCE_Y1 = 778.0, 818.0
FOOTER_Y = 832.0

# Corredores: x livres de caixa, reservados para as setas de longo alcance.
CORRIDOR_QUERY_X = 1100.0  # banco analitico -> tools, cruzando a camada 2
BAND2_VECTOR_X1 = CORRIDOR_QUERY_X - 60  # folga do corredor a direita
CORRIDOR_NEWS_X = 950.0  # vector db -> tools
CORRIDOR_AGENT_X = 700.0  # usuario -> agente, e agente -> relatorio
LANE_REQUEST_Y = 428.0  # corredor horizontal do pedido do usuario
LANE_DELIVERY_Y = 692.0  # altura da devolucao do relatorio ao usuario


@dataclass(frozen=True, slots=True)
class Box:
    """Retangulo desenhado, com os pontos de ancoragem das setas."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def left(self) -> tuple[float, float]:
        return self.x0, (self.y0 + self.y1) / 2

    @property
    def right(self) -> tuple[float, float]:
        return self.x1, (self.y0 + self.y1) / 2

    @property
    def top(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, self.y0

    @property
    def bottom(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, self.y1

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0


def _textbox(page: fitz.Page, rect: fitz.Rect, text: str, **kwargs: object) -> None:
    """`insert_textbox` que falha alto quando o texto nao cabe.

    O defeito classico deste tipo de gerador e silencioso: o texto cresce (mais
    uma tool, um nome de no mais longo) e o visualizador simplesmente corta o
    que passou da caixa, ou o bloco seguinte encosta no anterior. `insert_textbox`
    devolve a altura que sobrou -- negativa quando faltou espaco. Transformar
    isso em excecao faz a geracao (e o CI, que a repete) travar no lugar certo,
    em vez de publicar uma pagina com sobreposicao.
    """
    leftover = page.insert_textbox(rect, text, **kwargs)  # type: ignore[arg-type]
    if leftover < 0:
        preview = text.replace("\n", " ")[:60]
        raise RuntimeError(
            f"Texto nao cabe na area reservada ({rect.width:.0f}x{rect.height:.0f} pt, "
            f"faltam {abs(leftover):.0f} pt): {preview!r}"
        )


def _text_height(text: str, width: float, fontsize: float, lineheight: float) -> float:
    """Estimativa grosseira de altura ocupada por `insert_textbox` (helv)."""
    chars_per_line = max(1, int(width / (fontsize * 0.52)))
    lines = sum(math.ceil(max(1, len(part)) / chars_per_line) for part in text.split("\n"))
    return lines * fontsize * lineheight


def draw_box(
    page: fitz.Page,
    box: Box,
    title: str,
    blurb: str,
    detail: list[str] | None = None,
    *,
    stroke: tuple[float, float, float],
    fill: tuple[float, float, float],
    title_size: float = 11.5,
) -> Box:
    """Desenha um componente: titulo em negrito, uma frase de responsabilidade
    (o "blurb" que a especificacao pede) e, opcionalmente, uma lista compacta.
    """
    rect = fitz.Rect(box.x0, box.y0, box.x1, box.y1)
    page.draw_rect(rect, color=stroke, fill=fill, width=1.1, radius=0.04)
    page.draw_line(
        fitz.Point(box.x0, box.y0 + 4), fitz.Point(box.x0, box.y1 - 4), color=stroke, width=3
    )

    left = box.x0 + 16
    right = box.x1 - 14
    bottom = box.y1 - 8
    _textbox(
        page,
        fitz.Rect(left, box.y0 + 9, right, box.y0 + 31),
        title,
        fontsize=title_size,
        fontname="hebo",
        color=INK,
    )

    blurb_top = box.y0 + 34
    blurb_rect = fitz.Rect(left, blurb_top, right, bottom)
    _textbox(page, blurb_rect, blurb, fontsize=8.3, fontname="helv", color=MUTED, lineheight=1.34)

    if detail:
        detail_top = blurb_top + _text_height(blurb, blurb_rect.width, 8.3, 1.34) + 8
        _textbox(
            page,
            fitz.Rect(left, detail_top, right, bottom),
            "\n".join(detail),
            fontsize=7.7,
            fontname="helv",
            color=INK,
            lineheight=1.32,
        )
    return box


def _arrow_head(
    page: fitz.Page,
    start: tuple[float, float],
    end: tuple[float, float],
    color: tuple[float, float, float],
) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 6.5
    head = page.new_shape()
    head.draw_polyline(
        [
            fitz.Point(
                end[0] - size * math.cos(angle - math.pi / 7),
                end[1] - size * math.sin(angle - math.pi / 7),
            ),
            fitz.Point(*end),
            fitz.Point(
                end[0] - size * math.cos(angle + math.pi / 7),
                end[1] - size * math.sin(angle + math.pi / 7),
            ),
        ]
    )
    head.finish(color=color, fill=color, width=0.8)
    head.commit()


def draw_arrow(
    page: fitz.Page,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: tuple[float, float, float] = LINE,
    width: float = 1.2,
    label: str | None = None,
    label_align: str = "center",
    label_at: float = 0.5,
    label_offset: float = 0.0,
) -> None:
    """Seta reta com ponta preenchida e rotulo opcional."""
    page.draw_line(fitz.Point(*start), fitz.Point(*end), color=color, width=width)
    _arrow_head(page, start, end, color)
    if label:
        _draw_arrow_label(
            page, start, end, label, align=label_align, at=label_at, offset=label_offset
        )


def _draw_arrow_label(
    page: fitz.Page,
    start: tuple[float, float],
    end: tuple[float, float],
    label: str,
    *,
    align: str = "center",
    at: float = 0.5,
    offset: float = 0.0,
) -> None:
    """Rotulo da seta, sempre com halo branco sobre area vazia.

    `center` serve as setas horizontais entre duas caixas -- a grade dimensiona
    o vao para o rotulo caber inteiro nele. `left`/`right` servem as setas
    verticais dos corredores: ai o texto encosta ao lado da linha, nunca em
    cima dela.
    """
    fontsize = 7.4
    text_width = fitz.get_text_length(label, fontname="helv", fontsize=fontsize)
    anchor_x = start[0] + (end[0] - start[0]) * at
    anchor_y = start[1] + (end[1] - start[1]) * at

    if align == "left":
        x = anchor_x - text_width - 7
        y = anchor_y + 2.6 + offset
    elif align == "right":
        x = anchor_x + 7
        y = anchor_y + 2.6 + offset
    else:
        x = anchor_x - text_width / 2
        y = anchor_y - 5 + offset

    page.draw_rect(
        fitz.Rect(x - 3.5, y - 7.2, x + text_width + 3.5, y + 2.4), color=None, fill=PAPER
    )
    page.insert_text((x, y), label, fontsize=fontsize, fontname="helv", color=MUTED)


def draw_route(
    page: fitz.Page,
    points: list[tuple[float, float]],
    *,
    color: tuple[float, float, float] = LINE,
    width: float = 1.2,
) -> None:
    """Rota ortogonal (dois ou mais segmentos), com ponta so no ultimo trecho.

    Usada nos corredores: a grade reserva o x (ou o y) de cada rota, entao
    nenhuma delas atravessa caixa nem cruza outra rota.
    """
    for start, end in zip(points[:-2], points[1:-1], strict=True):
        page.draw_line(fitz.Point(*start), fitz.Point(*end), color=color, width=width)
    page.draw_line(fitz.Point(*points[-2]), fitz.Point(*points[-1]), color=color, width=width)
    _arrow_head(page, points[-2], points[-1], color)


def _draw_bullets(
    page: fitz.Page,
    *,
    x0: float,
    x1: float,
    cursor: float,
    items: tuple[str, ...],
    fontsize: float = 8.5,
    gap: float = 12.0,
) -> float:
    """Lista com marcador, devolvendo o y final.

    O marcador e um circulo DESENHADO, nao o caractere `•`: as fontes
    base-14 do PDF (helv) nao tem esse glifo e o visualizador o substitui por
    "?" -- era exatamente o que aparecia nesta coluna antes.
    """
    text_x = x0 + 12
    for text in items:
        height = _text_height(text, x1 - text_x, fontsize, 1.32)
        page.draw_circle(fitz.Point(x0 + 3, cursor + 3.4), 1.6, color=INK, fill=INK, width=0)
        _textbox(
            page,
            fitz.Rect(text_x, cursor - 3, x1, cursor + height + 6),
            text,
            fontsize=fontsize,
            fontname="helv",
            color=INK,
            lineheight=1.32,
        )
        cursor += height + gap
    return cursor


def _bullets_height(
    items: tuple[str, ...], width: float, fontsize: float = 8.5, gap: float = 12.0
) -> float:
    return sum(_text_height(text, width - 12, fontsize, 1.32) + gap for text in items)


def _layer_label(
    page: fitz.Page, baseline: float, text: str, color: tuple[float, float, float]
) -> None:
    """Rotulo da camada: quadrado na cor da camada + titulo, sobre o vao."""
    page.draw_rect(
        fitz.Rect(CONTENT_X0, baseline - 6.5, CONTENT_X0 + 6.5, baseline),
        color=None,
        fill=color,
    )
    page.insert_text((CONTENT_X0 + 13, baseline), text, fontsize=9.3, fontname="hebo", color=color)


def _page_header(
    page: fitz.Page,
    headline: str,
    *,
    chips: tuple[tuple[str, str], ...] = (),
) -> None:
    # Identificacao da entrega a esquerda, projeto a direita: o diagrama
    # circula solto (impresso, anexado), e precisa se identificar sozinho.
    page.insert_text((MARGIN, 44), DELIVERY_LABEL, fontsize=10, fontname="hebo", color=INK)
    project = "Indicium HealthCare -- SRAG Intelligence Agent"
    page.insert_text(
        (
            PAGE_WIDTH - MARGIN - fitz.get_text_length(project, fontname="helv", fontsize=10),
            44,
        ),
        project,
        fontsize=10,
        fontname="helv",
        color=MUTED,
    )
    headline_right = 830.0 if chips else PAGE_WIDTH - MARGIN
    _textbox(
        page,
        fitz.Rect(MARGIN, 52, headline_right, 106),
        headline,
        fontsize=19,
        fontname="hebo",
        color=INK,
        lineheight=1.2,
    )

    if chips:
        chip_width = (PAGE_WIDTH - MARGIN - 862) / len(chips)
        for index, (value, caption) in enumerate(chips):
            x = 862 + index * chip_width
            if index:
                page.draw_line(
                    fitz.Point(x - 10, 56), fitz.Point(x - 10, 96), color=HAIRLINE, width=0.8
                )
            page.insert_text((x, 78), value, fontsize=16, fontname="hebo", color=INK)
            page.insert_text((x, 92), caption, fontsize=6.9, fontname="helv", color=MUTED)

    page.draw_line(
        fitz.Point(MARGIN, HEADER_RULE_Y),
        fitz.Point(PAGE_WIDTH - MARGIN, HEADER_RULE_Y),
        color=LINE,
        width=0.8,
    )


def _page_footer(page: fitz.Page, note: str, number: int) -> None:
    page.insert_text((MARGIN, FOOTER_Y), note, fontsize=7.4, fontname="helv", color=MUTED)
    label = f"Arquivo entregue: {DELIVERED_AS}   --   Página {number} / 2"
    page.insert_text(
        (
            PAGE_WIDTH - MARGIN - fitz.get_text_length(label, fontname="helv", fontsize=7.4),
            FOOTER_Y,
        ),
        label,
        fontsize=7.4,
        fontname="helv",
        color=MUTED,
    )


def _tools_summary() -> str:
    counts: dict[str, int] = {}
    for tool in TOOLS:
        counts[tool.category] = counts.get(tool.category, 0) + 1
    labels = {
        "indicador": "indicadores",
        "diagnostico": "diagnóstico",
        "serie": "séries",
        "grafico": "gráficos",
        "contexto_externo": "notícias",
    }
    order = ("indicador", "serie", "grafico", "diagnostico", "contexto_externo")
    parts = [f"{counts[key]} {labels[key]}" for key in order if counts.get(key)]
    return f"{len(TOOLS)} tools: " + ", ".join(parts) + "."


# =============================================================================
# Pagina 1 -- arquitetura executiva
# =============================================================================


def _draw_user_rail(page: fitz.Page) -> Box:
    """Faixa do ator, a esquerda: quem pede, o que pede e o que recebe.

    O usuario fica numa faixa propria -- e nao numa caixa solta no meio do
    desenho -- porque ele toca duas camadas distantes entre si (pede a
    inteligencia, recebe da apresentacao). Na faixa, as duas interacoes saem na
    altura exata da camada correspondente, e nenhuma seta precisa atravessar o
    diagrama na diagonal.
    """
    rail = Box(RAIL_X0, BAND1_Y0, RAIL_X1, BAND4_Y1)
    page.draw_rect(
        fitz.Rect(rail.x0, rail.y0, rail.x1, rail.y1),
        color=INK,
        fill=FILL_USER,
        width=1.1,
        radius=0.02,
    )
    page.draw_line(
        fitz.Point(rail.x0, rail.y0 + 4), fitz.Point(rail.x0, rail.y1 - 4), color=INK, width=3
    )

    left, right = rail.x0 + 16, rail.x1 - 14
    _textbox(
        page,
        fitz.Rect(left, rail.y0 + 12, right, rail.y0 + 34),
        "USUÁRIO",
        fontsize=11.5,
        fontname="hebo",
        color=INK,
    )
    _textbox(
        page,
        fitz.Rect(left, rail.y0 + 38, right, rail.y0 + 120),
        "Profissional de saúde acompanhando a severidade e a evolução dos surtos de SRAG.",
        fontsize=8.3,
        fontname="helv",
        color=MUTED,
        lineheight=1.34,
    )

    for divider_y in (400.0, 660.0):
        page.draw_line(
            fitz.Point(rail.x0 + 14, divider_y),
            fitz.Point(rail.x1 - 14, divider_y),
            color=HAIRLINE,
            width=0.8,
        )

    page.insert_text(
        (left, LANE_REQUEST_Y), "SOLICITA", fontsize=8.6, fontname="hebo", color=LAYER_INTELLIGENCE
    )
    _textbox(
        page,
        fitz.Rect(left, LANE_REQUEST_Y + 8, right, LANE_REQUEST_Y + 86),
        "Pedido em linguagem natural, com recorte opcional por UF e período.",
        fontsize=8.3,
        fontname="helv",
        color=MUTED,
        lineheight=1.34,
    )
    chip = fitz.Rect(left, LANE_REQUEST_Y + 92, right, LANE_REQUEST_Y + 128)
    page.draw_rect(chip, color=HAIRLINE, fill=PAPER, width=0.8, radius=0.08)
    _textbox(
        page,
        fitz.Rect(chip.x0 + 7, chip.y0 + 7, chip.x1 - 5, chip.y1 - 4),
        "python main.py [--uf SP] [--no-llm]",
        fontsize=7.2,
        fontname="helv",
        color=INK,
        lineheight=1.3,
    )

    page.insert_text(
        (left, LANE_DELIVERY_Y), "RECEBE", fontsize=8.6, fontname="hebo", color=LAYER_PRESENTATION
    )
    _textbox(
        page,
        fitz.Rect(left, LANE_DELIVERY_Y + 8, right, rail.y1 - 8),
        "Relatório executivo com KPIs, gráficos, contexto externo isolado e anexo técnico "
        "rastreável.",
        fontsize=8.3,
        fontname="helv",
        color=MUTED,
        lineheight=1.34,
    )
    return rail


def _build_page_one(document: fitz.Document) -> None:
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    _page_header(
        page,
        "Arquitetura em camadas separa dados, inteligência e fontes externas "
        "para garantir rastreabilidade das análises",
        chips=(
            ("4", "camadas"),
            (str(len(TOOLS)), "tools"),
            (str(len(NODE_SEQUENCE)), "nós do grafo"),
            (str(len(ALL_POLICIES)), "guardrails"),
        ),
    )
    rail = _draw_user_rail(page)

    # --- Camada 1: DADOS -----------------------------------------------------
    _layer_label(page, BAND1_Y0 - 8, "1. CAMADA DE DADOS", LAYER_DATA)
    fonte = draw_box(
        page,
        Box(COL1_X0, BAND1_Y0, COL1_X1, BAND1_Y1),
        "Fonte oficial",
        "Open DATASUS / SIVEP-Gripe publica o CSV anual (194 colunas); a URL é "
        "resolvida a cada execução, nunca fixada no código.",
        [f"Fonte: {DATASUS_SOURCE_LABEL}", "Proveniência: manifest.json (sha256)"],
        stroke=LAYER_DATA,
        fill=FILL_DATA,
    )
    ingestao = draw_box(
        page,
        Box(COL2_X0, BAND1_Y0, COL2_X1, BAND1_Y1),
        "Ingestão e limpeza",
        "Lê só as colunas com uso declarado, aplica o pipeline de regras "
        "nomeadas e marca -- nunca exclui em silêncio -- o que é inconsistente.",
        [f"Allowlist: {len(ALLOWED_COLUMNS)} colunas lidas", "Denylist: dado pessoal nunca entra"],
        stroke=LAYER_DATA,
        fill=FILL_DATA,
    )
    banco = draw_box(
        page,
        Box(COL3_X0, BAND1_Y0, COL3_X1, BAND1_Y1),
        "Banco analítico (DuckDB)",
        "Guarda a tabela derivada e a view analítica; conexão somente leitura para qualquer tool.",
        [
            f"srag_cases: {len(ALLOWED_COLUMNS) - 2 + len(ALL_DERIVED_COLUMNS)} colunas",
            "+ referências IBGE / CNES / SI-PNI",
        ],
        stroke=LAYER_DATA,
        fill=FILL_DATA,
    )
    draw_arrow(page, fonte.right, ingestao.left, label="CSV bruto")
    draw_arrow(page, ingestao.right, banco.left, label="Parquet")

    # --- Camada 2: CONTEXTO EXTERNO ------------------------------------------
    # Fica entre os dados e a inteligencia porque e a segunda fonte de entrada,
    # e nao um apendice: a mesma camada de tools consome as duas. A coluna 3
    # desta faixa e deixada livre de proposito -- e o corredor por onde a
    # consulta ao banco desce ate as tools.
    _layer_label(page, BAND2_Y0 - 8, "2. CAMADA DE CONTEXTO EXTERNO", LAYER_EXTERNAL)
    news_source = draw_box(
        page,
        Box(BAND2_NEWS_X0, BAND2_Y0, BAND2_NEWS_X1, BAND2_Y1),
        "Google News RSS",
        "Buscas temáticas sobre SRAG em veículos e órgãos oficiais; o que vem "
        "de fora entra como contexto, nunca como número.",
        [
            f"{len(DEFAULT_QUERIES)} buscas temáticas por execução",
            f"Allowlist: {len(TRUSTED_DOMAINS)} domínios confiáveis",
        ],
        stroke=LAYER_EXTERNAL,
        fill=FILL_EXTERNAL,
    )
    vector_db = draw_box(
        page,
        Box(BAND2_VECTOR_X0, BAND2_Y0, BAND2_VECTOR_X1, BAND2_Y1),
        "Vector DB de notícias (DuckDB)",
        "Embedding + busca por cosseno, atualizados a cada relatório; o cache "
        "persistido cobre falha de rede.",
        [
            f"Sem credencial: hashing local de {HASHING_DIMENSIONS} dimensões",
            "Publicado só sob o rótulo CONTEXTO EXTERNO",
        ],
        stroke=LAYER_EXTERNAL,
        fill=FILL_EXTERNAL,
    )
    draw_arrow(page, news_source.right, vector_db.left, label="ingestão")

    # --- Camada 3: INTELIGENCIA ----------------------------------------------
    _layer_label(page, BAND3_Y0 - 8, "3. CAMADA DE INTELIGÊNCIA", LAYER_INTELLIGENCE)
    llm = draw_box(
        page,
        Box(COL1_X0, BAND3_Y0, COL1_X1, BAND3_Y1),
        "LLM (OpenAI)",
        "Interpreta os dados já calculados e redige a análise -- nunca calcula "
        "número. Um revisor semântico independente audita a saída.",
        ["Sem credencial: um narrador determinístico", "por template assume a redação."],
        stroke=LAYER_INTELLIGENCE,
        fill=FILL_INTELLIGENCE,
    )
    orquestrador = draw_box(
        page,
        Box(COL2_X0, BAND3_Y0, COL2_X1, BAND3_Y1),
        "Agente orquestrador (LangGraph)",
        "Coordena os oito nós do grafo, do pedido ao relatório, e consolida o "
        "contexto que vai para o modelo.",
        [f"{index}. {node}" for index, node in enumerate(NODE_SEQUENCE, start=1)],
        stroke=LAYER_INTELLIGENCE,
        fill=FILL_INTELLIGENCE,
    )
    tools = draw_box(
        page,
        Box(COL3_X0, BAND3_Y0, COL3_X1, BAND3_Y1),
        "Tools determinísticas",
        "Executam consultas parametrizadas e devolvem valor, numerador, "
        "denominador, fonte e limitações -- nunca SQL livre.",
        [_tools_summary()],
        stroke=LAYER_INTELLIGENCE,
        fill=FILL_INTELLIGENCE,
    )
    # Ida e volta em duas linhas paralelas, nunca duas setas sobre a mesma
    # linha: a seta de retorno so se le se tiver trilho proprio.
    ida, volta = (BAND3_Y0 + BAND3_Y1) / 2 - 12, (BAND3_Y0 + BAND3_Y1) / 2 + 12
    draw_arrow(page, (orquestrador.x0, ida), (llm.x1, ida), label="contexto")
    draw_arrow(page, (llm.x1, volta), (orquestrador.x0, volta), label="interpretação")
    draw_arrow(page, (orquestrador.x1, ida), (tools.x0, ida), label="chamada")
    draw_arrow(page, (tools.x0, volta), (orquestrador.x1, volta), label="resultado")

    # Corredores verticais: cada um no seu x reservado, nenhum cruza o outro.
    draw_arrow(
        page,
        (CORRIDOR_QUERY_X, BAND1_Y1),
        (CORRIDOR_QUERY_X, BAND3_Y0),
        label="consulta ao banco",
        label_align="left",
        label_at=0.09,
    )
    draw_arrow(
        page,
        (CORRIDOR_NEWS_X, BAND2_Y1),
        (CORRIDOR_NEWS_X, BAND3_Y0),
        label="busca semântica",
        label_align="left",
    )
    draw_route(
        page,
        [
            (rail.x1, LANE_REQUEST_Y),
            (CORRIDOR_AGENT_X, LANE_REQUEST_Y),
            (CORRIDOR_AGENT_X, BAND3_Y0),
        ],
    )
    _draw_arrow_label(
        page,
        (rail.x1, LANE_REQUEST_Y),
        (CORRIDOR_AGENT_X, LANE_REQUEST_Y),
        "solicita relatório",
        at=0.86,
        offset=7.6,
    )

    # --- Camada 4: APRESENTACAO ----------------------------------------------
    _layer_label(page, BAND4_Y0 - 8, "4. CAMADA DE APRESENTAÇÃO", LAYER_PRESENTATION)
    saida = draw_box(
        page,
        Box(COL1_X0, BAND4_Y0, COL2_X1, BAND4_Y1),
        "Relatório executivo (HTML + Markdown) + gráficos",
        "KPIs, headline calculado, dois gráficos interativos, contexto externo "
        "isolado e anexo técnico completo com a trilha da execução.",
        None,
        stroke=LAYER_PRESENTATION,
        fill=FILL_PRESENTATION,
    )
    draw_box(
        page,
        Box(COL3_X0, BAND4_Y0, COL3_X1, BAND4_Y1),
        "API HTTP (opcional)",
        "Mesmo grafo exposto como serviço: /indicadores, /series, /relatórios.",
        None,
        stroke=LAYER_PRESENTATION,
        fill=FILL_PRESENTATION,
    )
    draw_arrow(
        page,
        (CORRIDOR_AGENT_X, BAND3_Y1),
        (CORRIDOR_AGENT_X, BAND4_Y0),
        label="publica",
        label_align="left",
    )
    draw_arrow(page, (saida.x0, LANE_DELIVERY_Y), (rail.x1, LANE_DELIVERY_Y))

    # --- Faixa transversal: governanca (neutra, fora da paleta das camadas) --
    page.draw_rect(
        fitz.Rect(MARGIN, GOVERNANCE_Y0, PAGE_WIDTH - MARGIN, GOVERNANCE_Y1),
        color=LAYER_GOVERNANCE,
        fill=FILL_GOVERNANCE,
        width=1,
        radius=0.02,
    )
    _textbox(
        page,
        fitz.Rect(MARGIN + 14, GOVERNANCE_Y0 + 9, MARGIN + 150, GOVERNANCE_Y1 - 4),
        "GOVERNANÇA\nTRANSVERSAL",
        fontsize=8.2,
        fontname="hebo",
        color=INK,
        lineheight=1.25,
    )
    page.draw_line(
        fitz.Point(MARGIN + 160, GOVERNANCE_Y0 + 7),
        fitz.Point(MARGIN + 160, GOVERNANCE_Y1 - 7),
        color=LINE,
        width=0.8,
    )
    _textbox(
        page,
        fitz.Rect(MARGIN + 174, GOVERNANCE_Y0 + 8, PAGE_WIDTH - MARGIN - 14, GOVERNANCE_Y1 - 4),
        "Trilha de auditoria por run_id, evento a evento (outputs/audit/).   "
        "Proteção de dados pessoais: minimização na origem e mascaramento.   "
        f"{len(ALL_POLICIES)} guardrails: evidência obrigatória, sem SQL livre, notícias nunca "
        "sobrescrevem dados oficiais, indisponibilidade declarada em vez de estimativa, "
        "revisão semântica independente da saída.",
        fontsize=8.2,
        fontname="helv",
        color=INK,
        lineheight=1.34,
    )

    _page_footer(
        page,
        f"Fonte dos dados: {DATASUS_SOURCE_LABEL}.   Princípio de projeto: Python/SQL para "
        "calcular, Tools para acessar, LangGraph para orquestrar, LLM para interpretar e "
        "comunicar.",
        1,
    )


# =============================================================================
# Pagina 2 -- fluxo de execucao
# =============================================================================

_STEP_DESCRIPTIONS: dict[str, str] = {
    "validate_request": "Guardrails de entrada verificam se o pedido é legítimo (sem conduta "
    "clínica, sem dado individual) e o classificam em risco de prompt injection. Uma recusa "
    "encerra o fluxo aqui, antes de tocar o banco.",
    "collect_epidemiological_metrics": "As tools de indicador consultam o banco analítico e "
    "devolvem os sete indicadores (quatro exigidos, a ocupação de UTI e dois complementares), "
    "cada um com numerador, denominador, fonte e limitações. Ocupação de UTI e cobertura vacinal "
    "populacional cruzam o dado com as referências externas (CNES e SI-PNI).",
    "collect_time_series": "As tools de série e de gráfico geram a série diária (30 dias) e "
    "mensal (12 meses) e renderizam os dois gráficos obrigatórios a partir dos mesmos pontos.",
    "search_external_news": "A tool de notícias atualiza o acervo (rede) e busca, por "
    "similaridade semântica, o contexto mais relevante para o recorte pedido. A escrita usa "
    "troca atômica e trava de arquivo: leitura e ingestão simultâneas não se derrubam, e uma "
    "falha preserva o acervo anterior.",
    "select_optional_tools": "Única etapa em que o modelo decide: por function calling, ele pode "
    "acionar análises ADICIONAIS sobre uma allowlist de tools de leitura, com schemas Pydantic e "
    "teto de chamadas. O contrato obrigatório já está cumprido e fora do alcance dele.",
    "evaluate_alerts": "Regras determinísticas comparam os indicadores aos limiares "
    "configurados e ao histórico de execuções anteriores do mesmo recorte.",
    "validate_evidence": "Todo número que aparecerá na interpretação é conferido contra os "
    "valores efetivamente devolvidos pelas tools -- o conjunto de evidência citável.",
    "generate_interpretation": "O LLM redige a leitura do cenário a partir exclusivamente do "
    "contexto acima; a saída passa por guardrails lexicais e por um revisor semântico "
    "independente antes de ser aceita.",
    "generate_report": "O relatório executivo (HTML + Markdown) e os gráficos são gravados em "
    "disco, e a trilha de auditoria da execução é fechada.",
}

_STEPS_X0, _STEPS_X1 = 88.0, 812.0
_BADGE_X = 66.0
_PANEL_X0, _PANEL_X1 = 843.0, PAGE_WIDTH - MARGIN
_FLOW_Y0, _FLOW_Y1 = 134.0, 762.0


def _draw_step_cards(page: fitz.Page) -> None:
    total = len(NODE_SEQUENCE)
    gap = 12.0
    card_height = (_FLOW_Y1 - _FLOW_Y0 - gap * (total - 1)) / total

    for index, node in enumerate(NODE_SEQUENCE, start=1):
        y0 = _FLOW_Y0 + (index - 1) * (card_height + gap)
        y1 = y0 + card_height
        center_y = (y0 + y1) / 2

        page.draw_rect(
            fitz.Rect(_STEPS_X0, y0, _STEPS_X1, y1),
            color=HAIRLINE,
            fill=(0.985, 0.985, 0.99),
            width=0.9,
            radius=0.05,
        )
        page.draw_line(
            fitz.Point(_STEPS_X0, y0 + 4),
            fitz.Point(_STEPS_X0, y1 - 4),
            color=LAYER_INTELLIGENCE,
            width=3,
        )

        if index < total:
            page.draw_line(
                fitz.Point(_BADGE_X, center_y + 13),
                fitz.Point(_BADGE_X, center_y + card_height + gap - 13),
                color=LINE,
                width=1.1,
                dashes="[2 2] 0",
            )
        page.draw_circle(
            fitz.Point(_BADGE_X, center_y),
            11.5,
            color=LAYER_INTELLIGENCE,
            fill=FILL_INTELLIGENCE,
            width=1.3,
        )
        _textbox(
            page,
            fitz.Rect(_BADGE_X - 11.5, center_y - 8, _BADGE_X + 11.5, center_y + 11),
            str(index),
            fontsize=10.5,
            fontname="hebo",
            color=LAYER_INTELLIGENCE,
            align=1,
        )

        _textbox(
            page,
            fitz.Rect(_STEPS_X0 + 18, center_y - 8, _STEPS_X0 + 230, center_y + 10),
            node,
            fontsize=10.5,
            fontname="hebo",
            color=INK,
        )
        description = _STEP_DESCRIPTIONS.get(node, "")
        text_width = _STEPS_X1 - 14 - (_STEPS_X0 + 244)
        height = _text_height(description, text_width, 8.6, 1.34)
        _textbox(
            page,
            fitz.Rect(
                _STEPS_X0 + 244,
                center_y - height / 2 - 2,
                _STEPS_X1 - 14,
                center_y + height / 2 + 6,
            ),
            description,
            fontsize=8.6,
            fontname="helv",
            color=MUTED,
            lineheight=1.34,
        )


def _draw_side_panels(page: fitz.Page) -> None:
    """Coluna da direita: o que vale para toda a execucao, nao para um passo.

    Tres paineis dimensionados pelo proprio conteudo e distribuidos com folga
    igual entre o topo e a base da coluna de passos -- em vez de um painel
    unico alto que terminava com um terco de espaco vazio.
    """
    text_x0, text_x1 = _PANEL_X0 + 16, _PANEL_X1 - 14

    invariants = (
        "Cada nó e cada tool grava um evento de auditoria (timestamp, status, "
        "duração, resumo) antes de seguir.",
        "Falha em uma tool produz resultado parcial e degradação visível -- nunca "
        "interrompe a execução em silêncio.",
        "Nenhum número chega ao relatório sem ter saído de uma tool; o LLM "
        "interpreta, nunca calcula.",
        "Métrica sem dado suficiente é declarada indisponível, com o motivo -- nunca estimada.",
    )
    guardrails = tuple(policy.name for policy in ALL_POLICIES)
    outputs = (
        "outputs/reports/ -- relatório em HTML e Markdown",
        "outputs/charts/ -- os dois gráficos obrigatórios",
        "outputs/audit/ -- um JSON Lines por run_id",
        "outputs/history/ -- base da comparação entre execuções",
    )

    panels: list[tuple[str, tuple[str, ...], float]] = []
    for title, items, gap in (
        ("Em cada passo", invariants, 12.0),
        (f"Guardrails ativos ({len(ALL_POLICIES)})", guardrails, 8.0),
        ("Saídas de uma execução", outputs, 8.0),
    ):
        height = 42 + _bullets_height(items, text_x1 - text_x0, gap=gap) + 4
        panels.append((title, items, height))

    spare = (_FLOW_Y1 - _FLOW_Y0 - sum(panel[2] for panel in panels)) / (len(panels) - 1)
    cursor = _FLOW_Y0
    for (title, items, height), gap in zip(panels, (12.0, 8.0, 8.0), strict=True):
        page.draw_rect(
            fitz.Rect(_PANEL_X0, cursor, _PANEL_X1, cursor + height),
            color=LAYER_GOVERNANCE,
            fill=FILL_GOVERNANCE,
            width=1,
            radius=0.03,
        )
        _textbox(
            page,
            fitz.Rect(text_x0, cursor + 13, text_x1, cursor + 33),
            title,
            fontsize=10.3,
            fontname="hebo",
            color=INK,
        )
        _draw_bullets(page, x0=text_x0, x1=text_x1, cursor=cursor + 42, items=items, gap=gap)
        cursor += height + spare


def _build_page_two(document: fitz.Document) -> None:
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    _page_header(page, "Fluxo de execução: do pedido ao relatório em oito passos determinísticos")
    _draw_step_cards(page)
    _draw_side_panels(page)
    _page_footer(
        page,
        f"Fonte dos dados: {DATASUS_SOURCE_LABEL}.   Trilha completa de uma execução real: "
        "python main.py --audit <run_id>.",
        2,
    )


def build_diagram(output_path: Path) -> Path:
    """Monta o PDF do diagrama de arquitetura (duas paginas)."""
    document = fitz.open()
    _build_page_one(document)
    _build_page_two(document)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    document.close()
    return output_path


def main() -> int:
    settings = get_settings()
    path = build_diagram(settings.docs_dir / "arquitetura.pdf")
    print(f"Diagrama gerado: {path} ({path.stat().st_size / 1024:.1f} KB, 2 paginas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
