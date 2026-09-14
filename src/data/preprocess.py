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

Uso::

    python -m src.data.preprocess
    python -m src.data.preprocess --years 2026 --chunk-size 100000

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
    RAW_CSV_ENCODING,
    RAW_CSV_SEPARATOR,
    get_settings,
)
from src.data.cleaning import clean_chunk, describe_pipeline
from src.data.quality import QualityReport
from src.data.schema import ALLOWED_COLUMNS, assert_no_denied_columns
from src.observability.audit import AuditTrail
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

_DEFAULT_CHUNK_SIZE = 100_000


def _available_columns(path: Path) -> set[str]:
    header = pd.read_csv(path, sep=RAW_CSV_SEPARATOR, encoding=RAW_CSV_ENCODING, nrows=0)
    return set(header.columns)


def preprocess_year(path: Path, year: int, report: QualityReport, chunk_size: int) -> pd.DataFrame:
    """Le e transforma um arquivo anual completo, em blocos."""
    logger.info("processando arquivo", extra={"arquivo": path.name, "ano": year})

    available = _available_columns(path)
    usecols = [column for column in ALLOWED_COLUMNS if column in available]
    absent = sorted(set(ALLOWED_COLUMNS) - set(usecols))
    if absent:
        logger.warning(
            "colunas ausentes no arquivo bruto",
            extra={"arquivo": path.name, "colunas": absent},
        )

    frames: list[pd.DataFrame] = []
    reader = pd.read_csv(
        path,
        sep=RAW_CSV_SEPARATOR,
        encoding=RAW_CSV_ENCODING,
        usecols=usecols,
        dtype="string",
        chunksize=chunk_size,
        low_memory=False,
    )
    for chunk in reader:
        for column in absent:
            chunk[column] = pd.NA
        frames.append(clean_chunk(chunk, year, report))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def preprocess(
    years: list[int],
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    *,
    trail: AuditTrail | None = None,
) -> Path:
    """Transforma os anos solicitados e grava a camada processada em Parquet.

    Args:
        years: anos ja baixados em `data/raw`.
        chunk_size: numero de linhas lidas por bloco.
        trail: trilha de auditoria da carga; criada automaticamente se omitida,
            para que toda ingestao tenha um `run_id` rastreavel.

    Returns:
        Caminho do Parquet gerado.

    Raises:
        FileNotFoundError: se algum ano nao estiver presente em `data/raw`.
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
        with trail.step(
            node="preprocess",
            tool=f"preprocess_year:{year}",
            parameters={"ano": year, "arquivo": entry["filename"]},
            source=entry.get("origin", DATASUS_SOURCE_LABEL),
        ) as audit:
            frames.append(preprocess_year(path, year, report, chunk_size))
            audit["summary"] = f"{report.rows_read} linhas lidas ate aqui"
        provenance.append(
            {
                "year": year,
                "filename": entry["filename"],
                "sha256": entry.get("sha256"),
                # Manifestos anteriores ao campo `origin` nao o registram; a
                # presenca de URL identifica um download do DATASUS.
                "origin": entry.get("origin")
                or ("download do Open DATASUS" if entry.get("url") else "desconhecida"),
            }
        )

    combined = pd.concat(frames, ignore_index=True)
    assert_no_denied_columns(list(combined.columns))
    # Colunas lidas que sobrevivem ao tratamento (a idade exata e descartada
    # apos derivar a faixa). Contagem informativa: nada e deduplicado.
    surviving = [column for column in ALLOWED_COLUMNS if column in combined.columns]
    report.identical_rows = int(combined.duplicated(subset=surviving).sum())

    combined.to_parquet(settings.processed_parquet_path, index=False)

    payload = report.to_dict(
        source_files=source_files,
        run_id=trail.run_id,
        pipeline=describe_pipeline(),
    )
    payload["provenance"] = provenance
    settings.quality_report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _append_history(settings.ingestion_history_path, payload)

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
    args = parser.parse_args(argv)

    try:
        path = preprocess(args.years, chunk_size=args.chunk_size)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("pre-processamento falhou", extra={"motivo": str(exc)})
        return 1

    print(f"Parquet gerado: {path} ({path.stat().st_size / 1e6:.1f} MB)")
    print(f"Relatorio de qualidade: {settings.quality_report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
