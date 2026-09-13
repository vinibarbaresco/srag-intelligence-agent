"""Transformacao do CSV bruto do SIVEP-Gripe na camada processada.

Responsabilidades:

1. **Minimizacao** -- apenas as colunas de :data:`ALLOWED_COLUMNS` sao lidas do
   disco; as demais 160+ colunas nunca entram em memoria.
2. **Normalizacao de tipos** -- datas ISO-8601 (formato atual) com fallback para
   `dd/mm/aaaa` (safras antigas), codigos categoricos como inteiros nulaveis.
3. **Tratamento explicito de ausencia** -- o codigo `9-Ignorado` e preservado
   como esta e excluido dos denominadores na camada de metricas; nunca e
   convertido em `Nao` nem em zero.
4. **Transparencia** -- nenhum registro e descartado silenciosamente.
   Inconsistencias sao marcadas em `flag_data_invalida` e contabilizadas em
   `data/processed/quality_report.json`.

Uso::

    python -m src.data.preprocess
    python -m src.data.preprocess --years 2026 --chunk-size 100000
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import (
    DATASUS_SOURCE_LABEL,
    MIN_VALID_DATE,
    RAW_CSV_ENCODING,
    RAW_CSV_SEPARATOR,
    get_settings,
)
from src.data.schema import (
    AGE_BANDS,
    ALLOWED_COLUMNS,
    CATEGORICAL_COLUMNS,
    DATE_COLUMNS,
    GEOGRAPHIC_COLUMNS,
    NUMERIC_COLUMNS,
    UF_CODES,
    VACCINE_DATE_COLUMNS,
    assert_no_denied_columns,
)
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

_DEFAULT_CHUNK_SIZE = 100_000


@dataclass
class QualityReport:
    """Contabilidade das regras de transformacao aplicadas.

    Existe para cumprir a regra "nunca remova ou altere registros
    silenciosamente": cada decisao da limpeza vira um numero auditavel.
    """

    rows_read: int = 0
    rows_written: int = 0
    invalid_dates: Counter = field(default_factory=Counter)
    null_counts: Counter = field(default_factory=Counter)
    ignored_code_counts: Counter = field(default_factory=Counter)
    unknown_uf: int = 0
    inconsistent_timeline: int = 0
    rows_flagged: int = 0
    age_out_of_range: int = 0

    def to_dict(self, *, source_files: list[str]) -> dict:
        return {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": DATASUS_SOURCE_LABEL,
            "source_files": source_files,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_dropped": self.rows_read - self.rows_written,
            "rows_flagged_invalid_date": self.rows_flagged,
            "rules": {
                "datas_nao_parseaveis_por_coluna": dict(self.invalid_dates),
                "valores_nulos_por_coluna": dict(self.null_counts),
                "codigo_9_ignorado_por_coluna": dict(self.ignored_code_counts),
                "uf_fora_do_dominio": self.unknown_uf,
                "linha_do_tempo_inconsistente": self.inconsistent_timeline,
                "idade_fora_do_intervalo_plausivel": self.age_out_of_range,
            },
            "notes": [
                "Nenhum registro e excluido: inconsistencias sao marcadas em "
                "flag_data_invalida e permanecem na base.",
                "O codigo 9 (Ignorado) e preservado e excluido dos denominadores "
                "na camada de metricas, nunca convertido em 'Nao' ou zero.",
                "Colunas com dados pessoais nao sao lidas do arquivo bruto "
                "(ver DENIED_COLUMNS em src/data/schema.py).",
            ],
        }


def _parse_dates(series: pd.Series) -> pd.Series:
    """Converte uma coluna de datas aceitando os dois formatos ja publicados.

    O DATASUS publica hoje datas em ISO-8601 com sufixo `Z`
    (`2026-04-30T00:00:00.000Z`); safras anteriores usaram `dd/mm/aaaa`. O parse
    tenta ISO primeiro e recorre ao formato brasileiro apenas nos valores que
    sobraram, evitando a ambiguidade dia/mes.
    """
    text = series.astype("string").str.strip()
    text = text.replace({"": pd.NA})

    parsed = pd.to_datetime(text, format="ISO8601", utc=True, errors="coerce")
    parsed = parsed.dt.tz_localize(None)

    pending = parsed.isna() & text.notna()
    if pending.any():
        fallback = pd.to_datetime(text[pending], format="%d/%m/%Y", errors="coerce")
        parsed.loc[pending] = fallback

    return parsed


def _to_nullable_int(series: pd.Series) -> pd.Series:
    """Converte codigos categoricos numericos para inteiro nulavel."""
    return pd.to_numeric(series, errors="coerce").astype("Int16")


def _age_in_years(values: pd.Series, units: pd.Series) -> pd.Series:
    """Normaliza `NU_IDADE_N` para anos usando `TP_IDADE` (1-dia, 2-mes, 3-ano)."""
    amount = pd.to_numeric(values, errors="coerce")
    unit = pd.to_numeric(units, errors="coerce")

    years = pd.Series(pd.NA, index=amount.index, dtype="Float32")
    years[unit == 1] = amount[unit == 1] / 365.25
    years[unit == 2] = amount[unit == 2] / 12
    years[unit == 3] = amount[unit == 3]
    return years


def _age_band(years: pd.Series) -> pd.Series:
    """Agrega a idade em faixas, para nao expor idade exata a camada analitica."""
    band = pd.Series(pd.NA, index=years.index, dtype="string")
    for low, high, label in AGE_BANDS:
        band[(years >= low) & (years <= high)] = label
    return band


def transform_chunk(chunk: pd.DataFrame, year: int, report: QualityReport) -> pd.DataFrame:
    """Aplica todas as regras de transformacao a um bloco do CSV.

    Args:
        chunk: bloco lido do arquivo bruto, ja restrito as colunas permitidas.
        year: ano do arquivo de origem (vira `ano_referencia`).
        report: acumulador das estatisticas de qualidade.

    Returns:
        Bloco transformado, com as colunas derivadas e a flag de inconsistencia.
    """
    report.rows_read += len(chunk)
    frame = chunk.copy()

    for column in DATE_COLUMNS + VACCINE_DATE_COLUMNS:
        if column not in frame.columns:
            continue
        raw_present = frame[column].astype("string").str.strip().replace({"": pd.NA})
        frame[column] = _parse_dates(frame[column])
        report.invalid_dates[column] += int(
            (raw_present.notna() & frame[column].isna()).sum()
        )
        report.null_counts[column] += int(frame[column].isna().sum())

    for column in CATEGORICAL_COLUMNS:
        if column not in frame.columns or column == "CS_SEXO":
            continue
        frame[column] = _to_nullable_int(frame[column])
        report.null_counts[column] += int(frame[column].isna().sum())
        report.ignored_code_counts[column] += int((frame[column] == 9).sum())

    if "CS_SEXO" in frame.columns:
        frame["CS_SEXO"] = frame["CS_SEXO"].astype("string").str.strip().str.upper()
        report.null_counts["CS_SEXO"] += int(frame["CS_SEXO"].isna().sum())

    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int32")

    for column in GEOGRAPHIC_COLUMNS:
        if column not in frame.columns:
            continue
        frame[column] = frame[column].astype("string").str.strip().str.upper()
        outside = frame[column].notna() & ~frame[column].isin(UF_CODES)
        report.unknown_uf += int(outside.sum())
        frame.loc[outside, column] = pd.NA

    frame["idade_anos"] = _age_in_years(frame["NU_IDADE_N"], frame["TP_IDADE"])
    implausible = frame["idade_anos"].notna() & (
        (frame["idade_anos"] < 0) | (frame["idade_anos"] > 120)
    )
    report.age_out_of_range += int(implausible.sum())
    frame.loc[implausible, "idade_anos"] = pd.NA
    frame["faixa_etaria"] = _age_band(frame["idade_anos"])

    frame["ano_referencia"] = year
    frame["flag_data_invalida"] = _flag_invalid_timeline(frame, report)
    report.rows_flagged += int(frame["flag_data_invalida"].sum())
    report.rows_written += len(frame)

    assert_no_denied_columns(list(frame.columns))
    return frame


def _flag_invalid_timeline(frame: pd.DataFrame, report: QualityReport) -> pd.Series:
    """Marca registros com linha do tempo impossivel.

    Regras derivadas das restricoes declaradas no dicionario de dados:

    * data dos primeiros sintomas anterior ao inicio da serie publicada;
    * data dos primeiros sintomas posterior a data de digitacao;
    * data de evolucao anterior a data dos primeiros sintomas;
    * saida da UTI anterior a entrada na UTI.

    Os registros continuam na base -- apenas sinalizados.
    """
    floor = pd.Timestamp(MIN_VALID_DATE)
    symptoms = frame["DT_SIN_PRI"]
    typed = frame["DT_DIGITA"]

    invalid = pd.Series(False, index=frame.index)
    invalid |= symptoms.notna() & (symptoms < floor)
    invalid |= symptoms.notna() & typed.notna() & (symptoms > typed)
    invalid |= (
        frame["DT_EVOLUCA"].notna() & symptoms.notna() & (frame["DT_EVOLUCA"] < symptoms)
    )
    invalid |= (
        frame["DT_SAIDUTI"].notna()
        & frame["DT_ENTUTI"].notna()
        & (frame["DT_SAIDUTI"] < frame["DT_ENTUTI"])
    )

    report.inconsistent_timeline += int(invalid.sum())
    return invalid


def _available_columns(path: Path) -> set[str]:
    header = pd.read_csv(path, sep=RAW_CSV_SEPARATOR, encoding=RAW_CSV_ENCODING, nrows=0)
    return set(header.columns)


def preprocess_year(
    path: Path, year: int, report: QualityReport, chunk_size: int
) -> pd.DataFrame:
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
        frames.append(transform_chunk(chunk, year, report))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def preprocess(years: list[int], chunk_size: int = _DEFAULT_CHUNK_SIZE) -> Path:
    """Transforma os anos solicitados e grava a camada processada em Parquet.

    Args:
        years: anos ja baixados em `data/raw`.
        chunk_size: numero de linhas lidas por bloco.

    Returns:
        Caminho do Parquet gerado.

    Raises:
        FileNotFoundError: se algum ano nao estiver presente em `data/raw`.
    """
    settings = get_settings()
    settings.ensure_directories()

    manifest_path = settings.raw_manifest_path
    if not manifest_path.exists():
        raise FileNotFoundError(
            "data/raw/manifest.json nao encontrado. Execute primeiro: "
            "python -m src.data.download"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report = QualityReport()
    frames: list[pd.DataFrame] = []
    source_files: list[str] = []

    for year in sorted(years):
        entry = manifest.get(str(year))
        if entry is None:
            raise FileNotFoundError(
                f"Ano {year} ausente do manifesto. Execute: "
                f"python -m src.data.download --years {year}"
            )
        path = settings.raw_dir / entry["filename"]
        if not path.exists():
            raise FileNotFoundError(f"Arquivo bruto ausente: {path}")
        source_files.append(entry["filename"])
        frames.append(preprocess_year(path, year, report, chunk_size))

    combined = pd.concat(frames, ignore_index=True)
    assert_no_denied_columns(list(combined.columns))

    combined.to_parquet(settings.processed_parquet_path, index=False)
    settings.quality_report_path.write_text(
        json.dumps(report.to_dict(source_files=source_files), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "pre-processamento concluido",
        extra={
            "linhas": len(combined),
            "colunas": len(combined.columns),
            "parquet": str(settings.processed_parquet_path),
        },
    )
    return settings.processed_parquet_path


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
