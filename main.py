"""Entrypoint do SRAG Intelligence Agent.

Uso tipico::

    python main.py                       # relatorio nacional completo
    python main.py --uf SP               # recorte por unidade federativa
    python main.py --no-llm              # sem credencial de LLM
    python main.py --setup               # preparacao minima (rapida)
    python main.py --setup --setup-mode completo   # todos os indicadores
    python main.py --audit <run_id>      # imprime a trilha de uma execucao

O comando `--setup` executa a cadeia de preparacao (download, pre-processamento,
carga no DuckDB e ingestao de noticias) e e a forma recomendada de rodar o
projeto pela primeira vez. Ele tem dois modos, e a diferenca e de **cobertura de
indicadores**, nao de conveniencia:

* `--setup-mode minimo` (padrao) carrega so os anos de `SRAG_YEARS`. Rapido, mas
  o excesso sobre o baseline sazonal sai declarado indisponivel -- ele compara a
  janela atual com anos que nao estao na base.
* `--setup-mode completo` carrega tambem `BASELINE_YEARS` e atualiza as
  referencias externas (populacao do IBGE e leitos de UTI do CNES), de modo que
  todos os indicadores saiam calculaveis.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


#: Modos de preparacao.
#:
#: A diferenca entre os dois nao e de conveniencia, e de **quais indicadores
#: saem calculaveis**:
#:
#: * `minimo` carrega apenas `SRAG_YEARS` (padrao: o ano corrente e o anterior).
#:   Basta para os indicadores de janela -- crescimento, letalidade, UTI,
#:   vacinacao, incidencia. O baseline sazonal fica **indisponivel**, porque ele
#:   compara a janela atual com a mesma epoca de anos anteriores que nao estao
#:   na base. E o modo rapido, para uma primeira execucao ou uma demonstracao.
#: * `completo` carrega tambem os anos de `BASELINE_YEARS` e atualiza as
#:   referencias externas (populacao do IBGE e leitos do CNES), de modo que
#:   todos os indicadores saiam calculaveis. E o modo correto para uso real, e
#:   custa o download de varios anos do DATASUS.
SETUP_MODES: tuple[str, ...] = ("minimo", "completo")


def _setup_years(mode: str, years: list[int] | None) -> list[int]:
    """Anos a preparar, conforme o modo. Anos explicitos sempre prevalecem."""
    settings = get_settings()
    if years:
        return sorted(set(years))
    if mode == "completo":
        # O baseline sazonal so existe se os anos de referencia estiverem na
        # base. Carregar SRAG_YEARS e depois publicar "baseline indisponivel"
        # seria oferecer um modo completo que nao completa nada.
        return sorted(set(settings.srag_years) | set(settings.baseline_years))
    return sorted(set(settings.srag_years))


def _update_references() -> None:
    """Atualiza as referencias externas com fonte automatizavel.

    Populacao (IBGE) e leitos de UTI (CNES) tem API e arquivo publicos, pequenos
    e versionaveis: cabem no modo completo. A referencia de doses aplicadas
    (SI-PNI) **nao** entra aqui de proposito -- o extrato mensal tem alguns GB e
    a agregacao e uma operacao deliberada, descrita em
    `src/data/reference/vaccination.py`. Uma falha aqui degrada indicadores
    especificos, nunca a preparacao inteira.
    """
    from src.data.reference.icu_capacity import (
        ICUCapacityReferenceError,
        fetch_icu_capacity,
        write_icu_capacity_reference,
    )
    from src.data.reference.population import (
        PopulationReferenceError,
        fetch_population,
        write_population_reference,
    )

    print("      populacao residente (IBGE)...")
    try:
        frame, provenance = fetch_population()
        write_population_reference(frame, provenance)
    except PopulationReferenceError as exc:
        print(f"      AVISO: referencia populacional nao atualizada ({exc}).")
        print("      A incidencia por 100 mil habitantes ficara indisponivel.")

    print("      leitos de UTI (CNES)...")
    try:
        frame, provenance = fetch_icu_capacity(date.today().year)
        write_icu_capacity_reference(frame, provenance)
    except ICUCapacityReferenceError as exc:
        print(f"      AVISO: referencia de leitos nao atualizada ({exc}).")
        print("      A taxa de ocupacao de UTI ficara indisponivel.")


def _run_setup(
    years: list[int] | None = None,
    csv_paths: list[Path] | None = None,
    *,
    accept_drift: bool = False,
    mode: str = "minimo",
) -> int:
    """Prepara dados, banco analitico e acervo de noticias.

    Args:
        years: anos a baixar do DATASUS; ignorado quando `csv_paths` e usado.
            Quando informado, prevalece sobre o modo.
        csv_paths: arquivos CSV ja presentes em disco, registrados em vez de
            baixados. Util para quem recebeu o dataset junto com o enunciado.
        accept_drift: repassado a `preprocess`. Sem ele, uma mudanca de esquema
            classificada como ERROR interrompe a preparacao com uma mensagem
            explicita (ver `SchemaDriftError` abaixo) em vez de um traceback.
        mode: `minimo` ou `completo` (ver :data:`SETUP_MODES`).
    """
    from src.data.download import DownloadError, download_years, register_local_file
    from src.data.drift import SchemaDriftError
    from src.data.load_database import load_database
    from src.data.preprocess import preprocess
    from src.news.ingest import ingest_news
    from src.observability.audit import AuditTrail

    settings = get_settings()
    # Uma unica trilha para toda a preparacao: download, transformacao, carga e
    # ingestao de noticias compartilham o mesmo run_id, de modo que a origem de
    # uma base possa ser reconstituida a partir de um unico identificador.
    trail = AuditTrail()
    print(f"Preparacao (run_id): {trail.run_id}\n")

    total_steps = 5 if mode == "completo" else 4
    print(f"Modo de preparacao: {mode}\n")

    try:
        if csv_paths:
            print(f"[1/{total_steps}] Registrando {len(csv_paths)} arquivo(s) local(is)...")
            target_years = []
            for csv_path in csv_paths:
                year, path = register_local_file(csv_path)
                target_years.append(year)
                print(f"      {year}: {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
        else:
            target_years = _setup_years(mode, years)
            print(f"[1/{total_steps}] Baixando dados do DATASUS (anos: {target_years})...")
            download_years(target_years)

        step = 2
        if mode == "completo":
            print(f"[{step}/{total_steps}] Atualizando referencias externas...")
            _update_references()
            step += 1

        print(f"[{step}/{total_steps}] Pre-processando e aplicando o contrato de colunas...")
        preprocess(target_years, trail=trail, accept_drift=accept_drift)
        step += 1

        print(f"[{step}/{total_steps}] Carregando o banco analitico DuckDB...")
        load_database()
        step += 1

        print(f"[{step}/{total_steps}] Coletando noticias e populando o Vector DB...")
        summary = ingest_news(trail=trail)
        print(f"      {summary['noticias_gravadas']} noticias gravadas.")

        missing_baseline = sorted(set(settings.baseline_years) - set(target_years))
        if missing_baseline:
            print(
                f"\nAVISO: os anos de baseline {missing_baseline} nao foram "
                "carregados. O excesso sobre o baseline sazonal sera declarado "
                "indisponivel no relatorio.\n"
                "Para calcula-lo, prepare a base no modo completo:\n\n"
                "    python main.py --setup --setup-mode completo\n"
            )
    except (DownloadError, FileNotFoundError, ValueError, SchemaDriftError) as exc:
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
            "nao calculavel"
            if value is None
            else f"{value}{'%' if unit == '%' else (' ' + unit if unit else '')}"
        )
        print(
            f"  - {metric['metric']:36s} {rendered:>16s}"
            f"   (n={metric.get('numerator')}/{metric.get('denominator')})"
        )

    alerts = state.get("alerts") or {}
    if alerts:
        print(f"\nAlertas: nivel {str(alerts.get('nivel')).upper()} - {alerts.get('resumo')}")
        for item in alerts.get("disparados") or []:
            print(f"  ! {item.get('mensagem')}")
        history = alerts.get("historico") or {}
        if history.get("execucao_anterior"):
            changes = ", ".join(
                f"{key} {item['variacao']:+}"
                for key, item in (history.get("variacao") or {}).items()
                if item.get("variacao") is not None
            )
            print(f"  vs. execucao anterior ({history['execucao_anterior'][:8]}): {changes}")

    print("\nSeries:")
    for key, series in (state.get("series") or {}).items():
        summary = series["summary"]
        print(f"  - {key:36s} {summary['total_de_casos']:>10} casos em {summary['pontos']} pontos")

    print("\nGraficos:")
    for chart in (state.get("charts") or {}).values():
        print(f"  - {chart['path']}")

    articles = (state.get("external_context") or {}).get("articles") or []
    print(f"\nContexto externo: {len(articles)} noticias")
    for article in articles[:3]:
        print(f"  - [{article['data']}] {article['fonte']}: {article['titulo'][:62]}")

    audit = state.get("audit_summary") or {}
    print(f"\nAuditoria: {audit.get('total_events')} eventos, {audit.get('total_duration_ms')} ms")
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
        "--setup-mode",
        choices=SETUP_MODES,
        default="minimo",
        help=(
            "minimo: so os anos de SRAG_YEARS -- rapido, mas o baseline sazonal "
            "fica indisponivel. completo: carrega tambem os anos de "
            "BASELINE_YEARS e atualiza as referencias externas (IBGE e CNES), "
            "deixando todos os indicadores calculaveis (padrao: minimo)"
        ),
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=None,
        help="anos do dataset a preparar (usado com --setup)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        nargs="+",
        default=None,
        help=(
            "CSV(s) de SRAG ja presentes em disco, usados no lugar do download (usado com --setup)"
        ),
    )
    parser.add_argument(
        "--accept-drift",
        action="store_true",
        help=(
            "aceita as mudancas de esquema desta carga e regrava a linha de base "
            "(usado com --setup; ver data/processed/schema_drift.json apos uma "
            "falha para decidir se e o caso de aceitar)"
        ),
    )
    parser.add_argument(
        "--audit",
        metavar="RUN_ID",
        default=None,
        help="imprime a trilha de auditoria da execucao informada e encerra",
    )
    parser.add_argument(
        "--fail-on-alert",
        action="store_true",
        help=(
            "encerra com codigo 2 quando alguma regra de alerta dispara; e o sinal "
            "usado pela execucao agendada"
        ),
    )
    return parser


#: Codigo de saida quando --fail-on-alert encontra regra disparada.
EXIT_ALERT = 2


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_directories()

    args = build_parser().parse_args(argv)

    if args.audit:
        return _show_audit(args.audit)

    if args.setup:
        status = _run_setup(
            args.years,
            args.csv,
            accept_drift=args.accept_drift,
            mode=args.setup_mode,
        )
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

    if args.fail_on_alert and (state.get("alerts") or {}).get("total_disparados"):
        return EXIT_ALERT
    return 0


if __name__ == "__main__":
    sys.exit(main())
