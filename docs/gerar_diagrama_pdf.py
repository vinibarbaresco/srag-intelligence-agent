"""Gera `docs/arquitetura.pdf` -- o diagrama conceitual exigido na entrega.

O diagrama e produzido por codigo, a partir das mesmas constantes usadas pela
aplicacao (nos do grafo, catalogo de tools, politicas de guardrail). Assim ele
nao se descola da implementacao: acrescentar uma tool ou um guardrail muda o
diagrama na proxima geracao.

Uso::

    python docs/gerar_diagrama_pdf.py
"""

from __future__ import annotations

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

INK = (0.10, 0.12, 0.15)
MUTED = (0.42, 0.45, 0.50)
LINE = (0.62, 0.66, 0.72)

ACCENT_DATA = (0.12, 0.31, 0.48)
ACCENT_AGENT = (0.45, 0.20, 0.52)
ACCENT_TOOLS = (0.11, 0.42, 0.38)
ACCENT_EXTERNAL = (0.63, 0.35, 0.08)
ACCENT_GOVERNANCE = (0.55, 0.13, 0.20)

FILL_DATA = (0.91, 0.94, 0.97)
FILL_AGENT = (0.95, 0.92, 0.97)
FILL_TOOLS = (0.90, 0.95, 0.94)
FILL_EXTERNAL = (0.98, 0.94, 0.88)
FILL_GOVERNANCE = (0.98, 0.92, 0.93)


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


def draw_box(
    page: fitz.Page,
    box: Box,
    title: str,
    lines: list[str],
    *,
    stroke: tuple[float, float, float],
    fill: tuple[float, float, float],
    title_size: float = 10.5,
    body_size: float = 7.4,
) -> Box:
    """Desenha uma caixa arredondada com titulo e linhas de conteudo."""
    rect = fitz.Rect(box.x0, box.y0, box.x1, box.y1)
    page.draw_rect(rect, color=stroke, fill=fill, width=1.1, radius=0.06)

    cursor = box.y0 + 15
    page.insert_text(
        (box.x0 + 11, cursor), title, fontsize=title_size, fontname="hebo", color=stroke
    )
    cursor += 6

    for line in lines:
        cursor += body_size + 3.2
        page.insert_text(
            (box.x0 + 11, cursor), line, fontsize=body_size, fontname="helv", color=INK
        )
    return box


def draw_arrow(
    page: fitz.Page,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: tuple[float, float, float] = LINE,
    width: float = 1.2,
    dashes: str | None = None,
    label: str | None = None,
) -> None:
    """Desenha uma seta reta com ponta preenchida e rotulo opcional."""
    shape = page.new_shape()
    shape.draw_line(fitz.Point(*start), fitz.Point(*end))
    shape.finish(color=color, width=width, dashes=dashes)
    shape.commit()

    import math

    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 6.0
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
        mid_x = (start[0] + end[0]) / 2
        mid_y = (start[1] + end[1]) / 2 - 4
        page.insert_text(
            (mid_x - len(label) * 1.7, mid_y),
            label,
            fontsize=6.6,
            fontname="helv",
            color=MUTED,
        )


def _tools_by_category() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for tool in TOOLS:
        grouped.setdefault(tool.category, []).append(tool.name)
    return grouped


def build_diagram(output_path: Path) -> Path:
    """Monta o PDF do diagrama de arquitetura."""
    document = fitz.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    # --- Cabecalho -----------------------------------------------------------
    page.insert_text(
        (48, 50),
        "Indicium HealthCare - SRAG Intelligence Agent",
        fontsize=20,
        fontname="hebo",
        color=INK,
    )
    page.insert_text(
        (48, 70),
        "Arquitetura da PoC: orquestracao por LangGraph, calculo deterministico em "
        "SQL, contexto externo isolado e governanca transversal.",
        fontsize=9,
        fontname="helv",
        color=MUTED,
    )
    page.draw_line(fitz.Point(48, 82), fitz.Point(PAGE_WIDTH - 48, 82), color=LINE, width=0.8)

    grouped = _tools_by_category()

    # --- Coluna 1: camada de dados -------------------------------------------
    fonte = draw_box(
        page,
        Box(48, 108, 268, 196),
        "1. FONTE OFICIAL",
        [
            "Open DATASUS / SIVEP-Gripe",
            "CSV anual, 194 colunas, latin-1",
            "URL resolvida dinamicamente",
            "manifest.json com sha256",
        ],
        stroke=ACCENT_DATA,
        fill=FILL_DATA,
    )

    limpeza = draw_box(
        page,
        Box(48, 224, 268, 336),
        "2. INGESTAO E LIMPEZA",
        [
            "download.py -> data/raw/",
            f"schema.py: allowlist de {len(ALLOWED_COLUMNS)} colunas",
            "  + denylist de dados pessoais",
            "preprocess.py: datas, codigos, idade",
            "Codigo 9 (Ignorado) preservado",
            "Nada excluido: flag + quality_report",
        ],
        stroke=ACCENT_DATA,
        fill=FILL_DATA,
    )

    banco = draw_box(
        page,
        Box(48, 364, 268, 470),
        "3. BANCO ANALITICO (DuckDB)",
        [
            "data/analytics/srag.duckdb",
            f"tabela srag_cases ({len(ALLOWED_COLUMNS) - 2 + len(ALL_DERIVED_COLUMNS)} colunas)",
            "view srag_analytics: definicao unica",
            "  de caso, obito, UTI e vacinacao",
            "Conexao somente leitura",
            "+ referencias: populacao IBGE (e",
            "  cobertura SI-PNI, se fornecida)",
        ],
        stroke=ACCENT_DATA,
        fill=FILL_DATA,
    )

    vector = draw_box(
        page,
        Box(48, 498, 268, 604),
        "4. VECTOR DB DE NOTICIAS",
        [
            "data/analytics/news_vectors.duckdb",
            "Rotina: Google News RSS ->",
            "  allowlist de fontes + filtro de tema",
            "  -> embedding -> upsert (dedup)",
            "Atualiza por relatorio; cache em falha",
            "Busca por similaridade de cosseno",
        ],
        stroke=ACCENT_EXTERNAL,
        fill=FILL_EXTERNAL,
    )

    draw_arrow(page, fonte.bottom, (limpeza.top[0], limpeza.y0), label="CSV bruto")
    draw_arrow(page, limpeza.bottom, (banco.top[0], banco.y0), label="Parquet")

    # --- Coluna 2: tools ------------------------------------------------------
    tools_box = draw_box(
        page,
        Box(308, 108, 560, 470),
        "5. TOOLS DETERMINISTICAS",
        [
            "Schemas Pydantic fechados (extra=forbid)",
            "SQL literal com binding de parametros",
            "Sem tool de consulta livre ao banco",
            "Casos de uso: src/metrics (SQL parametrizado)",
            "",
            "INDICADORES",
            *[f"  - {name}" for name in grouped.get("indicador", [])],
            "",
            "DIAGNOSTICO",
            *[f"  - {name}" for name in grouped.get("diagnostico", [])],
            "",
            "SERIES TEMPORAIS",
            *[f"  - {name}" for name in grouped.get("serie", [])],
            "",
            "VISUALIZACAO",
            *[f"  - {name}" for name in grouped.get("grafico", [])],
            "",
            "CONTEXTO EXTERNO",
            *[f"  - {name}" for name in grouped.get("contexto_externo", [])],
        ],
        stroke=ACCENT_TOOLS,
        fill=FILL_TOOLS,
    )

    draw_arrow(page, banco.right, (tools_box.x0, 300), label="consulta")
    draw_arrow(page, vector.right, (tools_box.x0, 430), label="busca semantica")

    # --- Coluna 3: agente -----------------------------------------------------
    agente = draw_box(
        page,
        Box(600, 108, 852, 470),
        "6. AGENTE / ORQUESTRADOR (LangGraph)",
        [
            "StateGraph linear, sem ciclos",
            "",
            *[f"  {index}. {node}" for index, node in enumerate(NODE_SEQUENCE, start=1)],
            "",
            "Estado compartimentado:",
            "  metrics | series | charts   -> DADO",
            "  external_context            -> CONTEXTO",
            "  interpretation              -> INFERENCIA",
            "",
            "Recusa na entrada encerra o fluxo",
            "antes de qualquer consulta ao banco.",
        ],
        stroke=ACCENT_AGENT,
        fill=FILL_AGENT,
    )

    draw_arrow(page, tools_box.right, (agente.x0, 260), label="envelope com fonte")

    llm = draw_box(
        page,
        Box(600, 498, 852, 618),
        "7. LLM (OpenAI)",
        [
            "Usado para: planejar, selecionar tools,",
            "sintetizar e explicar o cenario.",
            "NUNCA para calcular numero.",
            "Porta `Interpreter`: adaptadores OpenAI e",
            "  deterministico; `Embedder`: OpenAI e hashing.",
            "Revisor semantico independente da saida",
            "Fallback deterministico sem credencial",
            "(`--no-llm`), com a via registrada.",
        ],
        stroke=ACCENT_AGENT,
        fill=FILL_AGENT,
    )
    draw_arrow(page, llm.top, (agente.bottom[0], agente.y1), label="interpretacao")
    draw_arrow(
        page,
        (agente.bottom[0] - 40, agente.y1),
        (llm.x0 + 60, llm.y0),
        label="contexto",
    )

    # --- Coluna 4: usuario e saida --------------------------------------------
    usuario = draw_box(
        page,
        Box(892, 108, 1143, 186),
        "USUARIO",
        [
            "python main.py [--uf SP] [--no-llm]",
            "Profissional de saude solicita o",
            "relatorio de monitoramento.",
        ],
        stroke=INK,
        fill=(0.96, 0.96, 0.96),
    )

    saida = draw_box(
        page,
        Box(892, 214, 1143, 470),
        "8. OUTPUT FINAL",
        [
            "outputs/reports/*.md e *.html",
            "outputs/charts/*.png",
            "",
            "Conteudo do relatorio:",
            "  - 4 indicadores exigidos + 2 complementares",
            "    (incidencia/100 mil, baseline sazonal)",
            "  - alertas por limiar e variacao",
            "    desde a execucao anterior",
            "  - 2 series temporais + 2 graficos",
            "  - interpretacao (INFERENCIA)",
            "  - noticias (CONTEXTO EXTERNO)",
            "  - limitacoes declaradas",
            "  - metricas nao calculaveis, ditas",
            "  - trilha de auditoria da execucao",
        ],
        stroke=ACCENT_AGENT,
        fill=FILL_AGENT,
    )

    draw_arrow(page, (usuario.x0, 147), (agente.x1, 147), label="solicitacao")
    draw_arrow(page, agente.right, (saida.x0, 300), label="relatorio")

    # --- Faixa transversal: governanca ----------------------------------------
    governanca = Box(48, 636, 1143, 808)
    page.draw_rect(
        fitz.Rect(governanca.x0, governanca.y0, governanca.x1, governanca.y1),
        color=ACCENT_GOVERNANCE,
        fill=FILL_GOVERNANCE,
        width=1.1,
        radius=0.03,
    )
    page.insert_text(
        (governanca.x0 + 14, governanca.y0 + 18),
        "CAMADA TRANSVERSAL - GOVERNANCA, AUDITORIA E GUARDRAILS",
        fontsize=10.5,
        fontname="hebo",
        color=ACCENT_GOVERNANCE,
    )

    column_width = (governanca.x1 - governanca.x0 - 28) / 3
    blocks = [
        (
            "Trilha de auditoria",
            [
                "run_id por execucao (UUID4)",
                "Evento por no e por tool:",
                "  timestamp, seq, node, tool,",
                "  parameters, status, duration_ms,",
                "  result_summary, source, error",
                "Logging estruturado em JSON por evento",
                "outputs/audit/<run_id>.jsonl",
                "  (fonte primaria, evento a evento)",
                "+ replica em audit_events no DuckDB",
                "  (consulta SQL entre execucoes)",
                "Sem chain-of-thought do modelo:",
                "so evento operacional observavel",
            ],
        ),
        (
            "Protecao de dados pessoais",
            [
                "Minimizacao na origem: colunas",
                "identificaveis nunca sao lidas",
                "Idade agregada em faixa etaria",
                "Granularidade geografica: UF",
                "Mascaramento de CPF, CNS, e-mail,",
                "telefone e CEP em log e saida",
                "Tools expoem somente agregados;",
                "sem acesso a registros individuais",
            ],
        ),
        (
            f"Guardrails ({len(ALL_POLICIES)} politicas)",
            [f"{index}. {policy.name}" for index, policy in enumerate(ALL_POLICIES, start=1)]
            + [
                "",
                "Saida reprovada e descartada e",
                "substituida pela redacao",
                "deterministica, com o bloqueio",
                "registrado no relatorio.",
            ],
        ),
    ]

    for index, (title, lines) in enumerate(blocks):
        x = governanca.x0 + 14 + index * column_width
        page.insert_text((x, governanca.y0 + 40), title, fontsize=8.6, fontname="hebo", color=INK)
        cursor = governanca.y0 + 40
        for line in lines:
            cursor += 10.2
            page.insert_text((x, cursor), line, fontsize=7.0, fontname="helv", color=INK)

    # --- Rodape ---------------------------------------------------------------
    page.insert_text(
        (48, PAGE_HEIGHT - 24),
        f"Fonte dos dados: {DATASUS_SOURCE_LABEL}.  "
        "Principio de projeto: Python/SQL para calcular, Tools para acessar, "
        "LangGraph para orquestrar, LLM para interpretar e comunicar.",
        fontsize=7.6,
        fontname="helv",
        color=MUTED,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    document.close()
    return output_path


def main() -> int:
    settings = get_settings()
    path = build_diagram(settings.docs_dir / "arquitetura.pdf")
    print(f"Diagrama gerado: {path} ({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
