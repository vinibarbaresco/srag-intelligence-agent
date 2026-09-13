"""Entrypoint do SRAG Intelligence Agent.

Uso tipico::

    python main.py                       # relatorio nacional completo
    python main.py --uf SP               # recorte por unidade federativa
    python main.py --no-llm              # sem credencial de LLM
    python main.py --setup               # prepara dados e banco antes de rodar
    python main.py --audit <run_id>      # imprime a trilha de uma execucao

O comando `--setup` executa a cadeia completa de preparacao (download,
pre-processamento, carga no DuckDB e ingestao de noticias) e e a forma
recomendada de rodar o projeto pela primeira vez.
"""

from __future__ import annotations

import argparse
import json
import sys

from src.config import get_settings
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


def _run_setup(years: list[int] | None = None) -> int:
    """Prepara dados, banco analitico e acervo de noticias."""
    from src.data.download import DownloadError, download_years
    from src.data.load_database import load_database
    from src.data.preprocess import preprocess
    from src.news.ingest import ingest_news

    settings = get_settings()
    target_years = years or settings.srag_years

    try:
        print(f"[1/4] Baixando dados do DATASUS (anos: {target_years})...")
        download_years(target_years)

        print("[2/4] Pre-processando e aplicando o contrato de colunas...")
        preprocess(target_years)

        print("[3/4] Carregando o banco analitico DuckDB...")
        load_database()

        print("[4/4] Coletando noticias e populando o Vector DB...")
        summary = ingest_news()
        print(f"      {summary['noticias_gravadas']} noticias gravadas.")
    except (DownloadError, FileNotFoundError, ValueError) as exc:
        logger.error("preparacao falhou", extra={"motivo": str(exc)})
        print(f"\nERRO na preparacao: {exc}", file=sys.stderr)
        return 1

    print("\nPreparacao concluida.\n")
    return 0


def _show_audit(run_id: str) -> int:
    """Imprime a trilha de auditoria de uma execucao."""
    settings = get_settings()
    path = settings.audit_dir / f"{run_id}.jsonl"
    if not path.exists():
        print(f"Trilha nao encontrada: {path}", file=sys.stderr)
        return 1

    print(f"Trilha de auditoria da execucao {run_id}\n")
    header = f"{'seq':>4} {'status':<9} {'ms':>9}  {'no / tool':<38} resumo"
    print(header)
    print("-" * len(header))

    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        label = event.get("node") or event.get("tool") or "-"
        if event.get("node") and event.get("tool"):
            label = f"{event['node']} / {event['tool']}"
        print(
            f"{event['seq']:>4} {event['status']:<9} {event['duration_ms']:>9.1f}  "
            f"{label:<38} {event['result_summary'][:60]}"
        )
        if event.get("error"):
            print(f"{'':>4} {'':<9} {'':>9}  ERRO: {event['error'][:90]}")
    return 0


def _database_ready() -> bool:
    settings = get_settings()
    return settings.database_path.exists()


def _print_summary(state: dict) -> None:
    """Resumo legivel da execucao no terminal."""
    validation = state.get("validation") or {}
    if not validation.get("allowed", True):
        print("\nSolicitacao recusada pelos guardrails de entrada.")
        print(f"  Guardrail: {validation.get('blocked_by')}")
        print(f"  Motivo:    {validation.get('reason')}")
        print(f"\nRelatorio de recusa: {state['report_paths']['markdown']}")
        return

    print("\n" + "=" * 78)
    print(f"RELATORIO DE SRAG - run_id {state['run_id']}")
    print("=" * 78)

    print("\nIndicadores:")
    for metric in (state.get("metrics") or {}).values():
        value = metric.get("value")
        unit = metric.get("unit", "")
        rendered = (
            "nao calculavel" if value is None else f"{value}{'%' if unit == '%' else ''}"
        )
        print(f"  - {metric['metric']:36s} {rendered:>16s}"
              f"   (n={metric.get('numerator')}/{metric.get('denominator')})")

    print("\nSeries:")
    for key, series in (state.get("series") or {}).items():
        summary = series["summary"]
        print(f"  - {key:36s} {summary['total_de_casos']:>10} casos em "
              f"{summary['pontos']} pontos")

    print("\nGraficos:")
    for chart in (state.get("charts") or {}).values():
        print(f"  - {chart['path']}")

    articles = (state.get("external_context") or {}).get("articles") or []
    print(f"\nContexto externo: {len(articles)} noticias")
    for article in articles[:3]:
        print(f"  - [{article['data']}] {article['fonte']}: {article['titulo'][:62]}")

    audit = state.get("audit_summary") or {}
    print(f"\nAuditoria: {audit.get('total_events')} eventos, "
          f"{audit.get('total_duration_ms')} ms")
    print(f"  {audit.get('audit_file')}")

    print("\nRelatorio:")
    for label, path in (state.get("report_paths") or {}).items():
        print(f"  {label:9s} {path}")

    warnings = state.get("warnings") or []
    errors = state.get("errors") or []
    if warnings:
        print(f"\nAvisos ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        print(f"\nErros ({len(errors)}):")
        for error in errors:
            print(f"  - {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Agente de monitoramento de SRAG - Indicium HealthCare (PoC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--request",
        default=None,
        help="solicitacao em linguagem natural (padrao: relatorio completo)",
    )
    parser.add_argument("--uf", default=None, help="recorte por UF (ex.: SP)")
    parser.add_argument(
        "--classification",
        type=int,
        choices=[1, 2, 3, 4, 5],
        default=None,
        help="classificacao final do caso (CLASSI_FIN)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="usa a interpretacao deterministica, sem chamada a modelo de linguagem",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="prepara dados, banco analitico e acervo de noticias antes de executar",
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help="anos do dataset a preparar (usado com --setup)",
    )
    parser.add_argument(
        "--audit",
        metavar="RUN_ID",
        default=None,
        help="imprime a trilha de auditoria da execucao informada e encerra",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_directories()

    args = build_parser().parse_args(argv)

    if args.audit:
        return _show_audit(args.audit)

    if args.setup:
        status = _run_setup(args.years)
        if status != 0:
            return status

    if not _database_ready():
        print(
            "Banco analitico nao encontrado.\n"
            "Execute a preparacao dos dados antes do primeiro relatorio:\n\n"
            "    python main.py --setup\n",
            file=sys.stderr,
        )
        return 1

    from src.agent.orchestrator import DEFAULT_REQUEST, run_report

    state = run_report(
        args.request or DEFAULT_REQUEST,
        uf=args.uf,
        classification=args.classification,
        use_llm=not args.no_llm,
    )
    _print_summary(dict(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
