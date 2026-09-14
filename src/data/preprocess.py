"""Orquestracao da carga: do CSV bruto a camada processada.

Este modulo cuida apenas do **fluxo**: ler o manifesto, abrir o arquivo em
blocos com as colunas permitidas, aplicar o pipeline de tratamento, concatenar,
gravar o Parquet e registrar a carga.

As **regras** de tratamento vivem em :mod:`src.data.cleaning`, uma por classe
nomeada e testavel, com a ordem declarada em `CLEANING_PIPELINE`. A
contabilidade vive em :mod:`src.data.quality`. Essa separacao mantem cada
arquivo com uma responsabilidade e permite auditar o tratamento como uma lista
de regras, sem ler codigo de orquestracao.

Garantias da carga:

1. **Minimizacao** -- apenas as colunas de :data:`ALLOWED_COLUMNS` sao lidas do
   disco; as demais 160+ colunas nunca entram em memoria.
2. **Transparencia** -- nenhum registro e descartado nem alterado
   silenciosamente. Inconsistencias viram flags de coerencia por dimensao, e
   toda alteracao de valor e registrada no proprio registro, na coluna
   `ajustes_aplicados`.
3. **Rastreabilidade** -- cada carga recebe um `run_id`, grava proveniencia e
   pipeline em `data/processed/quality_report.json` e acrescenta uma linha a
   `data/processed/ingestion_history.jsonl`.
4. **Estabilidade do contrato com a fonte** -- cada arquivo bruto e comparado
   contra a safra anterior do **mesmo ano** (:mod:`src.data.drift`). Coluna
   nova, coluna sumida, tipo, categoria, completude e contagem de registros
   entram na secao `schema` do relatorio, classificados em ERROR (interrompe a
   carga) ou WARNING. Aceitar uma mudanca conhecida exige `--accept-drift`, e o
   aceite fica gravado na linha de base.

Uso::

    python -m src.data.preprocess
    python -m src.data.preprocess --years 2026 --chunk-size 100000
    python -m src.data.preprocess --years 2026 --accept-drift

Os anos processados sao lidos de `data/raw/manifest.json`, que registra tanto os
arquivos baixados quanto os arquivos locais informados pelo usuario.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from src.config import (
    DATASUS_SOURCE_LABEL,
    RAW_CSV_SEPARATOR,
    get_settings,
)
from src.data.cleaning import clean_chunk, describe_pipeline
from src.data.drift import (
    DriftThresholds,
    SchemaDriftError,
    SchemaObservation,
    SchemaObserver,
    detect_drift,
    save_baseline,
)
from src.data.encoding import detect_encoding
from src.data.quality import QualityReport
from src.data.schema import ADJUSTMENT_COLUMN, ALLOWED_COLUMNS, assert_no_denied_columns
from src.observability.audit import AuditTrail
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

_DEFAULT_CHUNK_SIZE = 100_000


def _available_columns(path: Path, encoding: str) -> set[str]:
    header = pd.read_csv(path, sep=RAW_CSV_SEPARATOR, encoding=encoding, nrows=0)
    return set(header.columns)


def preprocess_year(
    path: Path,
    year: int,
    report: QualityReport,
    chunk_size: int,
    encoding: str | None = None,
    observer: SchemaObserver | None = None,
) -> pd.DataFrame:
    """Le e transforma um arquivo anual completo, em blocos.

    Args:
        path: CSV bruto do ano.
        year: ano do arquivo de origem.
        report: acumulador de qualidade da carga.
        chunk_size: numero de linhas lidas por bloco.
        encoding: encoding do arquivo; detectado automaticamente quando omitido.
        observer: acumulador do retrato de esquema do arquivo bruto. Recebe o
            cabecalho completo e cada bloco **antes** da limpeza -- depois dela
            os tipos sao os que o pipeline impos, e nao os que a fonte publicou.
    """
    encoding = encoding or detect_encoding(path)
    logger.info(
        "processando arquivo",
        extra={"arquivo": path.name, "ano": year, "encoding": encoding},
    )

    available = _available_columns(path, encoding)
    if observer is not None:
        # O cabecalho inteiro, e nao so a allowlist: e assim que uma coluna nova
        # ou sumida na fonte e percebida sem que uma celula dela seja lida.
        observer.observe_header(sorted(available))
    usecols = [column for column in ALLOWED_COLUMNS if column in available]
    absent = sorted(set(ALLOWED_COLUMNS) - set(usecols))
    if absent:
        # Continua avisado aqui para quem le o log da carga; a decisao de
        # interromper ou seguir e do detector de mudanca de esquema, que compara
        # a ausencia contra a safra anterior do mesmo ano.
        logger.warning(
            "colunas ausentes no arquivo bruto",
            extra={"arquivo": path.name, "colunas": absent},
        )

    frames: list[pd.DataFrame] = []
    reader = pd.read_csv(
        path,
        sep=RAW_CSV_SEPARATOR,
        encoding=encoding,
        usecols=usecols,
        dtype="string",
        chunksize=chunk_size,
        low_memory=False,
    )
    for chunk in reader:
        if observer is not None:
            observer.observe_chunk(chunk)
        for column in absent:
            chunk[column] = pd.NA
        frames.append(clean_chunk(chunk, year, report))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def preprocess(
    years: list[int],
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    *,
    trail: AuditTrail | None = None,
    accept_drift: bool = False,
) -> Path:
    """Transforma os anos solicitados e grava a camada processada em Parquet.

    Args:
        years: anos ja baixados em `data/raw`.
        chunk_size: numero de linhas lidas por bloco.
        trail: trilha de auditoria da carga; criada automaticamente se omitida,
            para que toda ingestao tenha um `run_id` rastreavel.
        accept_drift: aceita explicitamente as mudancas de esquema desta carga.
            Os achados continuam sendo reportados e passam a ficar gravados na
            linha de base com data e lista -- o aceite e registrado, nunca
            apagado.

    Returns:
        Caminho do Parquet gerado.

    Raises:
        FileNotFoundError: se algum ano nao estiver presente em `data/raw`.
        SchemaDriftError: se a fonte mudou de um jeito classificado como ERROR
            e a mudanca nao foi aceita com `accept_drift`.
    """
    settings = get_settings()
    settings.ensure_directories()
    trail = trail or AuditTrail()

    manifest_path = settings.raw_manifest_path
    if not manifest_path.exists():
        raise FileNotFoundError(
            "data/raw/manifest.json nao encontrado. Execute primeiro: python -m src.data.download"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report = QualityReport()
    frames: list[pd.DataFrame] = []
    source_files: list[str] = []
    provenance: list[dict] = []
    observations: list[SchemaObservation] = []

    for year in sorted(years):
        entry = manifest.get(str(year))
        if entry is None:
            raise FileNotFoundError(
                f"Ano {year} ausente do manifesto. Execute: "
                f"python -m src.data.download --years {year}"
            )
        # Arquivos baixados ficam em data/raw; arquivos locais registrados
        # permanecem onde estao e o manifesto guarda o caminho absoluto.
        path = Path(entry.get("path") or settings.raw_dir / entry["filename"])
        if not path.exists():
            raise FileNotFoundError(
                f"Arquivo bruto ausente: {path}. Ele consta no manifesto "
                f"(origem: {entry.get('origin', 'desconhecida')}) mas nao esta "
                "acessivel; registre-o novamente."
            )
        source_files.append(entry["filename"])
        # Detectado uma vez por arquivo e publicado na proveniencia: o encoding
        # nao e estavel entre safras do DATASUS, e uma mudanca precisa ficar
        # visivel no historico em vez de virar mojibake silencioso.
        encoding = detect_encoding(path)
        observer = SchemaObserver(year=year, filename=entry["filename"])
        with trail.step(
            node="preprocess",
            tool=f"preprocess_year:{year}",
            parameters={"ano": year, "arquivo": entry["filename"], "encoding": encoding},
            source=entry.get("origin", DATASUS_SOURCE_LABEL),
        ) as audit:
            frames.append(preprocess_year(path, year, report, chunk_size, encoding, observer))
            audit["summary"] = f"{report.rows_read} linhas lidas ate aqui"
        observations.append(observer.result())
        provenance.append(
            {
                "year": year,
                "filename": entry["filename"],
                "sha256": entry.get("sha256"),
                "encoding": encoding,
                # Manifestos anteriores ao campo `origin` nao o registram; a
                # presenca de URL identifica um download do DATASUS.
                "origin": entry.get("origin")
                or ("download do Open DATASUS" if entry.get("url") else "desconhecida"),
            }
        )

    # A comparacao contra a safra anterior acontece **antes** de qualquer
    # gravacao: um ERROR de esquema interrompe a carga sem deixar Parquet novo
    # nem relatorio de qualidade descrevendo uma base que nao deveria ser usada.
    # Os achados, porem, sao persistidos sempre -- inclusive no caminho que
    # interrompe --, porque nenhum achado pode desaparecer em silencio.
    drift = detect_drift(
        observations,
        settings.schema_baseline_path,
        accept=accept_drift,
        thresholds=DriftThresholds(
            missing_rate_delta_pct=settings.drift_missing_rate_delta_pp,
            record_drop_pct=settings.drift_record_drop_pct,
            record_growth_pct=settings.drift_record_growth_pct,
        ),
    )
    drift.log()
    drift_payload = drift.to_dict()
    settings.schema_drift_path.write_text(
        json.dumps(drift_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if drift.errors and not accept_drift:
        raise SchemaDriftError(
            "A fonte mudou de esquema de um jeito que interrompe a carga:\n"
            + "\n".join(f"  - {finding.message}" for finding in drift.errors)
            + f"\nDetalhe completo em {settings.schema_drift_path}. Para aceitar a "
            "mudanca e regravar a linha de base, repita a carga com --accept-drift."
        )

    combined = pd.concat(frames, ignore_index=True)
    assert_no_denied_columns(list(combined.columns))
    # A duplicidade e medida sobre as colunas **realmente persistidas**, e nao
    # sobre o subconjunto de ALLOWED_COLUMNS que sobrevive ao tratamento. A
    # diferenca nao e detalhe: `faixa_etaria` e persistida e nao e funcao de
    # nenhuma coluna bruta sobrevivente -- NU_IDADE_N e TP_IDADE sao descartadas
    # depois de deriva-la --, entao medir sem ela trata como identicos registros
    # que a base distingue. Medido na safra de referencia: 1.210 excedentes
    # (0,732%) pelo criterio antigo contra 752 (0,455%) sobre o que de fato foi
    # gravado. A coluna de ajustes fica de fora porque descreve o que o pipeline
    # fez, e nao o conteudo do registro.
    #
    # Continua sendo contagem informativa: nada e deduplicado. Sem o
    # identificador da notificacao (negado por minimizacao) nao ha como
    # distinguir duplicata real de pacientes distintos com atributos agregados
    # iguais -- e, medido na fonte, ha 0 linhas identicas nas 194 colunas e 0
    # NU_NOTIFIC repetido. Deduplicar removeria casos reais.
    persisted = [column for column in combined.columns if column != ADJUSTMENT_COLUMN]
    report.identical_rows = int(combined.duplicated(subset=persisted).sum())
    report.identical_rows_subset = persisted

    combined.to_parquet(settings.processed_parquet_path, index=False)

    payload = report.to_dict(
        source_files=source_files,
        run_id=trail.run_id,
        pipeline=describe_pipeline(),
        schema_drift=drift_payload,
    )
    payload["provenance"] = provenance
    settings.quality_report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _append_history(settings.ingestion_history_path, payload)
    # A linha de base so avanca quando a carga foi ate o fim. Uma carga
    # interrompida por ERROR a deixa intacta, de modo que a proxima tentativa
    # detecte exatamente a mesma mudanca em vez de aceita-la por inercia.
    save_baseline(
        settings.schema_baseline_path,
        observations,
        accepted=drift if accept_drift else None,
    )

    trail.record(
        node="preprocess",
        status="ok",
        result_summary=(f"{report.rows_written} linhas gravadas, {report.adjusted_rows} ajustadas"),
        parameters={"anos": sorted(years)},
        source=DATASUS_SOURCE_LABEL,
    )
    trail.persist_to_database()

    logger.info(
        "pre-processamento concluido",
        extra={
            "run_id": trail.run_id,
            "linhas": len(combined),
            "colunas": len(combined.columns),
            "linhas_ajustadas": report.adjusted_rows,
            "parquet": str(settings.processed_parquet_path),
        },
    )
    return settings.processed_parquet_path


def _append_history(path: Path, payload: dict) -> None:
    """Acrescenta o resumo da carga ao historico de ingestoes.

    Guarda apenas os totais: o detalhe fica no `quality_report.json` da carga
    corrente e na coluna de ajustes da propria base. O objetivo aqui e permitir
    comparar cargas -- perceber, por exemplo, que a proporcao de registros
    ajustados dobrou depois de uma republicacao da fonte.
    """
    summary = {
        "run_id": payload["run_id"],
        "generated_at": payload["generated_at"],
        "source_files": payload["source_files"],
        "provenance": payload.get("provenance", []),
        "rows_read": payload["rows_read"],
        "rows_written": payload["rows_written"],
        "rows_dropped": payload["rows_dropped"],
        "rows_adjusted": payload["rows_adjusted"],
        "adjustments": payload["adjustments"]["por_codigo"],
        "coherence_flags": {
            name: detail["registros"] for name, detail in payload["coherence_flags"].items()
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(
        description="Transforma o CSV bruto de SRAG na camada processada."
    )
    parser.add_argument("--years", type=int, nargs="+", default=settings.srag_years)
    parser.add_argument("--chunk-size", type=int, default=_DEFAULT_CHUNK_SIZE)
    parser.add_argument(
        "--accept-drift",
        action="store_true",
        help=(
            "aceita as mudancas de esquema desta carga e regrava a linha de "
            "base. O aceite fica registrado com data e lista do que foi aceito "
            "em data/processed/schema_baseline.json"
        ),
    )
    args = parser.parse_args(argv)

    try:
        path = preprocess(args.years, chunk_size=args.chunk_size, accept_drift=args.accept_drift)
    except (FileNotFoundError, ValueError, SchemaDriftError) as exc:
        logger.error("pre-processamento falhou", extra={"motivo": str(exc)})
        return 1

    print(f"Parquet gerado: {path} ({path.stat().st_size / 1e6:.1f} MB)")
    print(f"Relatorio de qualidade: {settings.quality_report_path}")
    print(f"Mudancas de esquema: {settings.schema_drift_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
