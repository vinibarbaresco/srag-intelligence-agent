"""Gera `docs/arquitetura.pdf` -- o diagrama conceitual exigido na entrega.

O diagrama e um artefato de comunicacao executiva, nao um fluxograma tecnico
cru: organiza a solucao em quatro camadas (Dados, Inteligencia, Contexto
Externo, Apresentacao), cada uma com uma cor propria e restrita, headline
conclusivo, e uma frase de responsabilidade por componente -- para que a
arquitetura se explique em menos de um minuto.

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
from src.config import DATASUS_SOURCE_LABEL, get_settings  # noqa: E402
from src.data.schema import ALL_DERIVED_COLUMNS, ALLOWED_COLUMNS  # noqa: E402
from src.guardrails.policies import ALL_POLICIES  # noqa: E402
from src.tools.registry import TOOLS  # noqa: E402

PAGE_WIDTH, PAGE_HEIGHT = 1191, 842  # A3 paisagem

# =============================================================================
# Paleta -- uma cor por camada, restrita. Nada de "arvore de Natal".
# =============================================================================
INK = (0.10, 0.12, 0.16)
MUTED = (0.43, 0.46, 0.51)
LINE = (0.68, 0.71, 0.76)
PAPER = (1, 1, 1)

LAYER_DATA = (0.11, 0.33, 0.42)
LAYER_INTELLIGENCE = (0.35, 0.22, 0.46)
LAYER_EXTERNAL = (0.62, 0.36, 0.11)
LAYER_PRESENTATION = (0.13, 0.40, 0.32)
LAYER_GOVERNANCE = (0.36, 0.38, 0.42)

FILL_DATA = (0.90, 0.94, 0.96)
FILL_INTELLIGENCE = (0.94, 0.91, 0.96)
FILL_EXTERNAL = (0.98, 0.93, 0.87)
FILL_PRESENTATION = (0.90, 0.95, 0.93)
FILL_GOVERNANCE = (0.95, 0.95, 0.96)
FILL_USER = (0.97, 0.97, 0.97)


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
    page.draw_rect(rect, color=stroke, fill=fill, width=1.2, radius=0.05)
    page.draw_line(
        fitz.Point(box.x0, box.y0 + 3), fitz.Point(box.x0, box.y1 - 3), color=stroke, width=3
    )

    bottom = box.y1 - 6
    cursor = box.y0 + 19
    page.insert_textbox(
        fitz.Rect(box.x0 + 14, box.y0 + 8, box.x1 - 10, min(box.y0 + 30, bottom)),
        title,
        fontsize=title_size,
        fontname="hebo",
        color=INK,
    )
    cursor += 10
    blurb_rect = fitz.Rect(box.x0 + 14, cursor, box.x1 - 12, max(cursor + 1, bottom))
    page.insert_textbox(
        blurb_rect, blurb, fontsize=8.3, fontname="helv", color=MUTED, lineheight=1.32
    )

    if detail:
        detail_top = cursor + _text_height(blurb, blurb_rect.width, 8.3, 1.32) + 6
        if detail_top < bottom - 4:
            page.insert_textbox(
                fitz.Rect(box.x0 + 14, detail_top, box.x1 - 12, bottom),
                "\n".join(detail),
                fontsize=7.6,
                fontname="helv",
                color=INK,
                lineheight=1.3,
            )
    return box


def _text_height(text: str, width: float, fontsize: float, lineheight: float) -> float:
    """Estimativa grosseira de altura ocupada por `insert_textbox` (helv)."""
    chars_per_line = max(1, int(width / (fontsize * 0.52)))
    lines = sum(math.ceil(max(1, len(part)) / chars_per_line) for part in text.split("\n"))
    return lines * fontsize * lineheight


def draw_arrow(
    page: fitz.Page,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: tuple[float, float, float] = LINE,
    width: float = 1.3,
    dashes: str | None = None,
    label: str | None = None,
    label_offset: float = 0.0,
) -> None:
    """Desenha uma seta reta com ponta preenchida e rotulo opcional."""
    shape = page.new_shape()
    shape.draw_line(fitz.Point(*start), fitz.Point(*end))
    shape.finish(color=color, width=width, dashes=dashes)
    shape.commit()

    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 6.5
    tip = fitz.Point(*end)
    left = fitz.Point(
        end[0] - size * math.cos(angle - math.pi / 7),
        end[1] - size * math.sin(angle - math.pi / 7),
    )
    right = fitz.Point(
        end[0] - size * math.cos(angle + math.pi / 7),
        end[1] - size * math.sin(angle + math.pi / 7),
    )
    head = page.new_shape()
    head.draw_polyline([left, tip, right])
    head.finish(color=color, fill=color, width=0.8)
    head.commit()

    if label:
        _draw_arrow_label(page, start, end, label, label_offset)


def _draw_arrow_label(
    page: fitz.Page,
    start: tuple[float, float],
    end: tuple[float, float],
    label: str,
    label_offset: float,
) -> None:
    """Rotulo centrado no meio da seta, com halo branco -- so quando o halo
    cabe no vao entre as duas pontas sem invadir o que estiver ao lado. Um vao
    curto (duas caixas proximas na mesma linha) recebe o texto sem halo, para
    nunca apagar pedaco do conteudo da caixa vizinha.
    """
    fontsize = 7.2
    text_width = fitz.get_text_length(label, fontname="helv", fontsize=fontsize)
    gap = math.hypot(end[0] - start[0], end[1] - start[1])

    mid_x = (start[0] + end[0]) / 2
    mid_y = (start[1] + end[1]) / 2 - 5 + label_offset

    if text_width + 10 <= gap:
        page.draw_rect(
            fitz.Rect(mid_x - text_width / 2 - 4, mid_y - 7, mid_x + text_width / 2 + 4, mid_y + 3),
            color=None,
            fill=PAPER,
        )
    page.insert_text(
        (mid_x - text_width / 2, mid_y), label, fontsize=fontsize, fontname="helv", color=MUTED
    )


def draw_elbow_arrow(
    page: fitz.Page,
    points: list[tuple[float, float]],
    *,
    color: tuple[float, float, float] = LINE,
    width: float = 1.3,
    label: str | None = None,
) -> None:
    """Seta em L (dois ou mais segmentos), com ponta so no ultimo trecho.

    Usada para contornar caixas de outras camadas em vez de atravessa-las --
    a especificacao pede poucas setas cruzadas, e uma linha reta entre pontos
    distantes do diagrama quase sempre cruza algo no meio do caminho.
    """
    for start, end in zip(points[:-2], points[1:-1], strict=True):
        page.draw_line(fitz.Point(*start), fitz.Point(*end), color=color, width=width)
    draw_arrow(page, points[-2], points[-1], color=color, width=width, label=label)


def _draw_bullets(
    page: fitz.Page,
    box: Box,
    *,
    cursor: float,
    items: tuple[str, ...],
    fontsize: float = 8.4,
    gap: float = 14.0,
) -> float:
    """Lista com marcador, devolvendo o y final.

    O marcador e um circulo DESENHADO, nao o caractere `•`: as fontes
    base-14 do PDF (helv) nao tem esse glifo e o visualizador o substitui por
    "?" -- era exatamente o que aparecia nesta coluna antes.
    """
    text_x = box.x0 + 26
    text_width = box.x1 - 14 - text_x
    for text in items:
        page.draw_circle(fitz.Point(box.x0 + 18, cursor + 3.4), 1.6, color=INK, fill=INK, width=0)
        page.insert_textbox(
            fitz.Rect(text_x, cursor - 3, box.x1 - 14, cursor + 70),
            text,
            fontsize=fontsize,
            fontname="helv",
            color=INK,
            lineheight=1.3,
        )
        cursor += _text_height(text, text_width, fontsize, 1.3) + gap
    return cursor


def _layer_label(
    page: fitz.Page, x: float, y: float, text: str, color: tuple[float, float, float]
) -> None:
    page.insert_text((x, y), text, fontsize=9.5, fontname="hebo", color=color)


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
    return f"{len(TOOLS)} tools determinísticas: " + ", ".join(parts) + "."


# =============================================================================
# Pagina 1 -- arquitetura executiva
# =============================================================================


def _build_page_one(document: fitz.Document) -> None:
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    page.insert_text(
        (48, 42),
        "Indicium HealthCare -- SRAG Intelligence Agent",
        fontsize=12,
        fontname="helv",
        color=MUTED,
    )
    page.insert_textbox(
        fitz.Rect(48, 44, PAGE_WIDTH - 260, 108),
        "Arquitetura em camadas separa dados, inteligência e fontes externas "
        "para garantir rastreabilidade das análises",
        fontsize=18.5,
        fontname="hebo",
        color=INK,
        lineheight=1.2,
    )
    page.draw_line(fitz.Point(48, 116), fitz.Point(PAGE_WIDTH - 48, 116), color=LINE, width=0.8)

    # --- Usuario: unico ponto de entrada e saida, ancorado a direita --------
    user = Box(PAGE_WIDTH - 210, 132, PAGE_WIDTH - 48, 242)
    draw_box(
        page,
        user,
        "USUÁRIO",
        "Profissional de saúde solicita o relatório e recebe métricas, gráficos "
        "e interpretação de volta.",
        ["python main.py [--uf SP] [--no-llm]"],
        stroke=INK,
        fill=FILL_USER,
    )

    # --- Camada 1: DADOS ------------------------------------------------------
    layer1_y0, layer1_y1 = 128, 268
    _layer_label(page, 48, layer1_y0 - 8, "1. CAMADA DE DADOS", LAYER_DATA)
    fonte = draw_box(
        page,
        Box(48, layer1_y0, 268, layer1_y1),
        "Fonte oficial",
        "Open DATASUS / SIVEP-Gripe publica o CSV anual (194 colunas); a URL é "
        "resolvida a cada execução, nunca fixada no código.",
        [f"Fonte: {DATASUS_SOURCE_LABEL}", "Proveniência: manifest.json (sha256)"],
        stroke=LAYER_DATA,
        fill=FILL_DATA,
    )
    ingestao = draw_box(
        page,
        Box(298, layer1_y0, 518, layer1_y1),
        "Ingestão e limpeza",
        "Lê só as colunas com uso declarado, aplica o pipeline de regras "
        "nomeadas e marca -- nunca exclui em silêncio -- o que é inconsistente.",
        [f"Allowlist: {len(ALLOWED_COLUMNS)} colunas lidas", "Denylist: dado pessoal nunca entra"],
        stroke=LAYER_DATA,
        fill=FILL_DATA,
    )
    banco = draw_box(
        page,
        Box(548, layer1_y0, 768, layer1_y1),
        "Banco analítico (DuckDB)",
        "Guarda a tabela derivada e a view analítica; conexão somente leitura para qualquer tool.",
        [
            f"srag_cases: {len(ALLOWED_COLUMNS) - 2 + len(ALL_DERIVED_COLUMNS)} colunas",
            "+ referências IBGE / SI-PNI",
        ],
        stroke=LAYER_DATA,
        fill=FILL_DATA,
    )
    draw_arrow(page, fonte.right, ingestao.left, label="CSV bruto")
    draw_arrow(page, ingestao.right, banco.left, label="Parquet")

    # --- Camada 2: INTELIGENCIA ------------------------------------------------
    layer2_y0, layer2_y1 = 300, 470
    _layer_label(page, 48, layer2_y0 - 8, "2. CAMADA DE INTELIGÊNCIA", LAYER_INTELLIGENCE)
    tools = draw_box(
        page,
        Box(48, layer2_y0, 298, layer2_y1),
        "Tools determinísticas",
        "Executam consultas parametrizadas e devolvem valor, numerador, "
        "denominador, fonte e limitações -- nunca SQL livre.",
        [_tools_summary()],
        stroke=LAYER_INTELLIGENCE,
        fill=FILL_INTELLIGENCE,
    )
    orquestrador = draw_box(
        page,
        Box(328, layer2_y0, 618, layer2_y1),
        "Agente orquestrador (LangGraph)",
        "Coordena os oito nós do grafo, do pedido ao relatório, e consolida o "
        "contexto que vai para o modelo.",
        [f"{index}. {node}" for index, node in enumerate(NODE_SEQUENCE, start=1)],
        stroke=LAYER_INTELLIGENCE,
        fill=FILL_INTELLIGENCE,
        title_size=11.5,
    )
    llm = draw_box(
        page,
        Box(648, layer2_y0, 848, layer2_y1),
        "LLM (OpenAI)",
        "Interpreta os dados já calculados e redige a análise -- nunca "
        "calcula número. Revisor semântico independente audita a saída.",
        ["Sem credencial: narrador determinístico", "por template assume a redação"],
        stroke=LAYER_INTELLIGENCE,
        fill=FILL_INTELLIGENCE,
    )
    draw_arrow(page, banco.bottom, (tools.top[0], tools.y0), label="consulta")
    draw_arrow(page, tools.right, orquestrador.left, label="resultado")
    draw_arrow(page, orquestrador.right, llm.left, label="contexto", label_offset=-9)
    draw_arrow(page, llm.left, orquestrador.right, label="interpretação", label_offset=9)
    draw_arrow(page, (user.x0, user.center[1]), (orquestrador.x1 - 40, layer2_y0), label="solicita")

    # --- Camada 3: CONTEXTO EXTERNO -------------------------------------------
    layer3_y0, layer3_y1 = 502, 610
    _layer_label(page, 48, layer3_y0 - 8, "3. CAMADA DE CONTEXTO EXTERNO", LAYER_EXTERNAL)
    news_source = draw_box(
        page,
        Box(48, layer3_y0, 298, layer3_y1),
        "Google News RSS",
        "Seis buscas temáticas sobre SRAG, filtradas por uma allowlist de "
        "veículos e órgãos oficiais confiáveis.",
        None,
        stroke=LAYER_EXTERNAL,
        fill=FILL_EXTERNAL,
    )
    vector_db = draw_box(
        page,
        Box(328, layer3_y0, 618, layer3_y1),
        "Vector DB de notícias (DuckDB)",
        "Embedding (OpenAI ou hashing local) + busca por cosseno. Atualiza a "
        "cada relatório; cache persistido cobre falha de rede.",
        None,
        stroke=LAYER_EXTERNAL,
        fill=FILL_EXTERNAL,
    )
    # Ancorada perto da base das caixas, nao no centro vertical: e onde a
    # caixa tem espaco em branco de sobra (o blurb curto nao alcanca ate la),
    # entao o rotulo nao cai em cima do proprio texto do componente.
    draw_arrow(
        page,
        (news_source.x1, news_source.y1 - 18),
        (vector_db.x0, vector_db.y1 - 18),
        label="ingestão",
    )
    draw_arrow(page, vector_db.top, (tools.center[0], tools.y1), label="busca semântica")

    # --- Camada 4: APRESENTACAO ------------------------------------------------
    layer4_y0, layer4_y1 = 642, 750
    _layer_label(page, 48, layer4_y0 - 8, "4. CAMADA DE APRESENTAÇÃO", LAYER_PRESENTATION)
    saida = draw_box(
        page,
        Box(48, layer4_y0, 388, layer4_y1),
        "Relatório executivo (HTML/MD) + gráficos",
        "KPIs, headline calculado, dois gráficos interativos, contexto "
        "externo isolado e anexo técnico completo.",
        None,
        stroke=LAYER_PRESENTATION,
        fill=FILL_PRESENTATION,
    )
    # Ligado ao mesmo grafo do orquestrador (ja dito no proprio blurb); sem
    # seta propria para nao adicionar outra linha cruzando a camada.
    draw_box(
        page,
        Box(418, layer4_y0, 618, layer4_y1),
        "API HTTP (opcional)",
        "Mesmo grafo exposto como serviço: /indicadores, /series, /relatórios.",
        None,
        stroke=LAYER_PRESENTATION,
        fill=FILL_PRESENTATION,
    )
    # Duas setas de longo alcance, cada uma no seu corredor vertical -- nunca
    # uma reta direta, que atravessaria a camada de contexto externo ou a de
    # inteligencia inteira. Dois corredores distintos evitam que as linhas
    # se sobreponham uma na outra.
    report_channel_x = llm.x1 + 40
    receive_channel_x = llm.x1 + 90

    report_path = [
        orquestrador.bottom,
        (orquestrador.bottom[0], layer2_y1 + 6),
        (report_channel_x, layer2_y1 + 6),
        (report_channel_x, layer4_y0 - 6),
        (saida.x1 - 20, layer4_y0 - 6),
        (saida.x1 - 20, saida.y0),
    ]
    draw_elbow_arrow(page, report_path, label=None)
    _draw_arrow_label(
        page,
        (report_channel_x, layer2_y1 + 6),
        (report_channel_x, layer4_y0 - 6),
        "relatório",
        0.0,
    )

    draw_elbow_arrow(
        page,
        [
            (saida.x1, layer4_y1 + 14),
            (receive_channel_x, layer4_y1 + 14),
            (receive_channel_x, user.y1 - 20),
            (user.x0, user.y1 - 20),
        ],
        label="recebe relatório",
    )

    # --- Faixa transversal: governanca (neutra, fora da paleta das camadas) ---
    governance = Box(48, 772, PAGE_WIDTH - 48, 812)
    page.draw_rect(
        fitz.Rect(governance.x0, governance.y0, governance.x1, governance.y1),
        color=LAYER_GOVERNANCE,
        fill=FILL_GOVERNANCE,
        width=1,
        radius=0.02,
    )
    gov_text = (
        f"GOVERNANÇA TRANSVERSAL  --  trilha de auditoria por run_id (evento a evento, "
        f"outputs/audit/) + proteção de dados pessoais (minimização na origem, mascaramento) "
        f"+ {len(ALL_POLICIES)} guardrails (evidência obrigatória, sem SQL livre, notícias nunca "
        "sobrescrevem dados, indisponibilidade declarada, revisão semântica independente)"
    )
    page.insert_textbox(
        fitz.Rect(governance.x0 + 14, governance.y0 + 8, governance.x1 - 14, governance.y1 - 6),
        gov_text,
        fontsize=8.4,
        fontname="helv",
        color=INK,
        lineheight=1.35,
    )

    page.insert_text(
        (48, PAGE_HEIGHT - 22),
        "Fonte dos dados: "
        f"{DATASUS_SOURCE_LABEL}.  Princípio de projeto: Python/SQL para calcular, "
        "Tools para acessar, LangGraph para orquestrar, LLM para interpretar e comunicar.",
        fontsize=7.4,
        fontname="helv",
        color=MUTED,
    )
    page.insert_text(
        (PAGE_WIDTH - 90, PAGE_HEIGHT - 22),
        "Página 1 / 2",
        fontsize=7.4,
        fontname="helv",
        color=MUTED,
    )


# =============================================================================
# Pagina 2 -- fluxo de execucao
# =============================================================================

_STEP_DESCRIPTIONS: dict[str, str] = {
    "validate_request": "Guardrails de entrada verificam se o pedido é legítimo (sem conduta "
    "clínica, sem dado individual). Uma recusa encerra o fluxo aqui, antes de tocar o banco.",
    "collect_epidemiological_metrics": "As tools de indicador consultam o banco analítico e "
    "devolvem os seis indicadores (quatro exigidos + dois complementares), cada um com "
    "numerador, denominador, fonte e limitações.",
    "collect_time_series": "As tools de série e de gráfico geram a série diária (30 dias) e "
    "mensal (12 meses) e renderizam os dois gráficos obrigatórios a partir dos mesmos pontos.",
    "search_external_news": "A tool de notícias atualiza o acervo (rede) e busca, por "
    "similaridade semântica, o contexto mais relevante para o recorte pedido.",
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


def _build_page_two(document: fitz.Document) -> None:
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    page.insert_text(
        (48, 42),
        "Indicium HealthCare -- SRAG Intelligence Agent",
        fontsize=12,
        fontname="helv",
        color=MUTED,
    )
    page.insert_textbox(
        fitz.Rect(48, 50, PAGE_WIDTH - 48, 100),
        "Fluxo de execução: do pedido ao relatório em oito passos determinísticos",
        fontsize=19,
        fontname="hebo",
        color=INK,
        lineheight=1.18,
    )
    page.draw_line(fitz.Point(48, 108), fitz.Point(PAGE_WIDTH - 48, 108), color=LINE, width=0.8)

    top = 140
    row_height = 82
    number_col = 48
    text_col = 108
    line_x = number_col + 16

    for index, node in enumerate(NODE_SEQUENCE, start=1):
        y_center = top + (index - 1) * row_height
        page.draw_circle(
            fitz.Point(line_x, y_center + 8),
            11,
            color=LAYER_INTELLIGENCE,
            fill=FILL_INTELLIGENCE,
            width=1.3,
        )
        page.insert_textbox(
            fitz.Rect(line_x - 11, y_center - 3, line_x + 11, y_center + 19),
            str(index),
            fontsize=11,
            fontname="hebo",
            color=LAYER_INTELLIGENCE,
            align=1,
        )
        if index < len(NODE_SEQUENCE):
            page.draw_line(
                fitz.Point(line_x, y_center + 19),
                fitz.Point(line_x, y_center + row_height - 3),
                color=LINE,
                width=1.2,
                dashes="[2 2] 0",
            )

        page.insert_textbox(
            fitz.Rect(text_col, y_center - 4, PAGE_WIDTH - 340, y_center + 16),
            node,
            fontsize=12.5,
            fontname="hebo",
            color=INK,
        )
        page.insert_textbox(
            fitz.Rect(text_col, y_center + 16, PAGE_WIDTH - 340, y_center + row_height - 6),
            _STEP_DESCRIPTIONS.get(node, ""),
            fontsize=8.8,
            fontname="helv",
            color=MUTED,
            lineheight=1.35,
        )

    # --- Coluna lateral: o que nunca muda, qualquer que seja o passo ---------
    side = Box(PAGE_WIDTH - 300, 140, PAGE_WIDTH - 48, 140 + row_height * len(NODE_SEQUENCE) - 20)
    page.draw_rect(
        fitz.Rect(side.x0, side.y0, side.x1, side.y1),
        color=LAYER_GOVERNANCE,
        fill=FILL_GOVERNANCE,
        width=1,
        radius=0.03,
    )
    page.insert_textbox(
        fitz.Rect(side.x0 + 16, side.y0 + 14, side.x1 - 14, side.y0 + 34),
        "Em cada passo",
        fontsize=10.5,
        fontname="hebo",
        color=INK,
    )
    invariants = (
        "Cada nó e cada tool grava um evento de auditoria (timestamp, status, duração, "
        "resumo) antes de seguir.",
        "Falha em uma tool produz resultado parcial e degradação visível -- nunca "
        "interrompe a execução em silêncio.",
        "Nenhum número chega ao relatório sem ter saído de uma tool; o LLM interpreta, "
        "nunca calcula.",
        "Métrica sem dado suficiente é declarada indisponível, com o motivo -- nunca estimada.",
    )
    cursor = _draw_bullets(page, side, cursor=side.y0 + 46, items=invariants)

    # Segundo painel: os guardrails, lidos do proprio codigo. Entram aqui
    # porque valem para toda a execucao (nao para um passo especifico) -- e
    # porque a coluna, so com os invariantes, terminava com um terco de espaco
    # vazio, o que faz uma pagina parecer inacabada.
    cursor += 10
    page.draw_line(
        fitz.Point(side.x0 + 16, cursor), fitz.Point(side.x1 - 14, cursor), color=LINE, width=0.8
    )
    cursor += 16
    page.insert_textbox(
        fitz.Rect(side.x0 + 16, cursor - 4, side.x1 - 14, cursor + 18),
        f"Guardrails ativos ({len(ALL_POLICIES)})",
        fontsize=10.5,
        fontname="hebo",
        color=INK,
    )
    cursor += 22
    _draw_bullets(
        page,
        side,
        cursor=cursor,
        items=tuple(policy.name for policy in ALL_POLICIES),
        gap=7,
    )

    page.insert_text(
        (48, PAGE_HEIGHT - 22),
        f"Fonte dos dados: {DATASUS_SOURCE_LABEL}.  Trilha completa de uma execução real: "
        "python main.py --audit <run_id>.",
        fontsize=7.4,
        fontname="helv",
        color=MUTED,
    )
    page.insert_text(
        (PAGE_WIDTH - 90, PAGE_HEIGHT - 22),
        "Página 2 / 2",
        fontsize=7.4,
        fontname="helv",
        color=MUTED,
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
