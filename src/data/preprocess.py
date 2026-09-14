"""Transformacao do CSV bruto do SIVEP-Gripe na camada processada.

Responsabilidades:

1. **Minimizacao** -- apenas as colunas de :data:`ALLOWED_COLUMNS` sao lidas do
   disco; as demais 160+ colunas nunca entram em memoria.
2. **Normalizacao de tipos** -- datas nos tres formatos ja publicados pela fonte
   (ver :func:`_parse_dates`), codigos categoricos como inteiros nulaveis.
3. **Tratamento explicito de ausencia** -- o codigo `9-Ignorado` e preservado
   como esta e excluido dos denominadores na camada de metricas; nunca e
   convertido em `Nao` nem em zero.
4. **Transparencia** -- nenhum registro e descartado nem alterado
   silenciosamente. Inconsistencias viram flags de coerencia por dimensao, e
   toda alteracao de valor e registrada no proprio registro, na coluna
   `ajustes_aplicados`. Os totais vao para
   `data/processed/quality_report.json`, e cada carga recebe um `run_id`
   e uma linha em `data/processed/ingestion_history.jsonl`.

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
    ADJUSTMENT_CODES,
    ADJUSTMENT_COLUMN,
    AGE_BANDS,
    ALLOWED_COLUMNS,
    COHERENCE_FLAGS,
    CATEGORICAL_COLUMNS,
    DATE_COLUMNS,
    GEOGRAPHIC_COLUMNS,
    NUMERIC_COLUMNS,
    UF_CODES,
    VACCINE_DATE_COLUMNS,
    assert_no_denied_columns,
)
from src.observability.audit import AuditTrail
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
    coherence_flags: Counter = field(default_factory=Counter)
    age_out_of_range: int = 0
    adjusted_rows: int = 0
    adjustments: Counter = field(default_factory=Counter)

    def to_dict(self, *, source_files: list[str], run_id: str = "") -> dict:
        return {
            "run_id": run_id,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": DATASUS_SOURCE_LABEL,
            "source_files": source_files,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_dropped": self.rows_read - self.rows_written,
            "rows_adjusted": self.adjusted_rows,
            "adjustments": {
                "por_codigo": dict(self.adjustments),
                "significado": ADJUSTMENT_CODES,
                "como_localizar": (
                    "SELECT * FROM srag_cases WHERE ajustes_aplicados <> '' -- "
                    "cada registro alterado carrega os codigos aplicados a ele"
                ),
            },
            "coherence_flags": {
                name: {
                    "registros": self.coherence_flags[name],
                    "percentual": (
                        round(self.coherence_flags[name] / self.rows_read * 100, 3)
                        if self.rows_read
                        else 0.0
                    ),
                    "significado": description,
                    "exclui_da_view_analitica": name == "flag_data_invalida",
                }
                for name, description in COHERENCE_FLAGS.items()
            },
            "rules": {
                "datas_nao_parseaveis_por_coluna": dict(self.invalid_dates),
                "valores_nulos_por_coluna": dict(self.null_counts),
                "codigo_9_ignorado_por_coluna": dict(self.ignored_code_counts),
                "uf_fora_do_dominio": self.unknown_uf,
                "idade_fora_do_intervalo_plausivel": self.age_out_of_range,
            },
            "notes": [
                "Nenhum registro e excluido: inconsistencias sao marcadas em "
                "flags de coerencia e permanecem na base.",
                "As flags sao por dimensao. Apenas flag_data_invalida exclui o "
                "registro da view analitica, porque sem eixo temporal nenhuma "
                "metrica pode situar o caso. As demais sao respeitadas apenas "
                "pelas metricas que dependem daquela dimensao.",
                "O codigo 9 (Ignorado) e preservado e excluido dos denominadores "
                "na camada de metricas, nunca convertido em 'Nao' ou zero.",
                "Colunas com dados pessoais nao sao lidas do arquivo bruto "
                "(ver DENIED_COLUMNS em src/data/schema.py).",
                "Toda alteracao de valor e registrada por registro na coluna "
                "ajustes_aplicados, alem de contabilizada aqui.",
            ],
        }


def _parse_dates(series: pd.Series) -> pd.Series:
    """Converte uma coluna de datas aceitando os tres formatos ja publicados.

    O DATASUS ja distribuiu o mesmo campo de tres maneiras, conforme a safra do
    arquivo:

    * ISO-8601 com sufixo `Z` -- `2026-04-30T00:00:00.000Z` (publicacoes atuais);
    * ISO-8601 simples -- `2024-12-29` (ex.: INFLUD25 versao 26-06-2025);
    * formato brasileiro -- `30/04/2026` (safras antigas).

    O parse tenta ISO primeiro, que cobre os dois primeiros, e recorre ao
    formato brasileiro apenas nos valores restantes -- evitando que `03/04/2026`
    seja interpretado como 3 de abril ou 4 de marco conforme o acaso.
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
        Bloco transformado, com as colunas derivadas, as flags de coerencia e a
        coluna de ajustes aplicados.
    """
    report.rows_read += len(chunk)
    frame = chunk.copy()

    # Cada alteracao de valor e registrada por registro, e nao apenas contada.
    # Sem isso, um campo anulado pelo pipeline ficaria indistinguivel de um que
    # ja veio vazio da fonte -- e a alteracao seria, na pratica, silenciosa.
    adjustments = _AdjustmentLog(frame.index)

    for column in DATE_COLUMNS + VACCINE_DATE_COLUMNS:
        if column not in frame.columns:
            continue
        raw_present = frame[column].astype("string").str.strip().replace({"": pd.NA})
        frame[column] = _parse_dates(frame[column])
        unreadable = raw_present.notna() & frame[column].isna()
        report.invalid_dates[column] += int(unreadable.sum())
        adjustments.add(f"data_ilegivel:{column}", unreadable)
        report.null_counts[column] += int(frame[column].isna().sum())

    for column in CATEGORICAL_COLUMNS:
        if column not in frame.columns or column == "CS_SEXO":
            continue
        frame[column] = _to_nullable_int(frame[column])
        report.null_counts[column] += int(frame[column].isna().sum())
        report.ignored_code_counts[column] += int((frame[column] == 9).sum())

    if "CS_SEXO" in frame.columns:
        frame["CS_SEXO"] = (
            frame["CS_SEXO"]
            .astype("string")
            .str.strip()
            .str.upper()
            .replace({"": pd.NA})
        )
        report.null_counts["CS_SEXO"] += int(frame["CS_SEXO"].isna().sum())

    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int32")

    for column in GEOGRAPHIC_COLUMNS:
        if column not in frame.columns:
            continue
        # A string vazia e ausencia na origem, nao valor fora do dominio:
        # normaliza-la antes evita contar (e marcar como ajuste) um campo que o
        # pipeline nunca alterou.
        frame[column] = (
            frame[column]
            .astype("string")
            .str.strip()
            .str.upper()
            .replace({"": pd.NA})
        )
        outside = frame[column].notna() & ~frame[column].isin(UF_CODES)
        report.unknown_uf += int(outside.sum())
        adjustments.add(f"uf_anulada:{column}", outside)
        frame.loc[outside, column] = pd.NA

    frame["idade_anos"] = _age_in_years(frame["NU_IDADE_N"], frame["TP_IDADE"])
    implausible = frame["idade_anos"].notna() & (
        (frame["idade_anos"] < 0) | (frame["idade_anos"] > 120)
    )
    report.age_out_of_range += int(implausible.sum())
    adjustments.add("idade_anulada", implausible)
    frame.loc[implausible, "idade_anos"] = pd.NA
    frame["faixa_etaria"] = _age_band(frame["idade_anos"])

    frame["ano_referencia"] = year
    for name, values in _coherence_flags(frame).items():
        frame[name] = values
        report.coherence_flags[name] += int(values.sum())

    frame[ADJUSTMENT_COLUMN] = adjustments.to_series()
    report.adjusted_rows += int(adjustments.affected_rows())
    for code, count in adjustments.counts().items():
        report.adjustments[code] += count
    report.rows_written += len(frame)

    assert_no_denied_columns(list(frame.columns))
    return frame


class _AdjustmentLog:
    """Acumula, por registro, os ajustes que a ingestao aplicou.

    Existe para que a afirmacao "nada e alterado silenciosamente" valha no nivel
    do registro, e nao apenas no agregado: depois da carga e possivel localizar
    exatamente quais linhas o pipeline tocou, e por que.
    """

    def __init__(self, index: pd.Index) -> None:
        self._index = index
        self._entries: list[tuple[str, pd.Series]] = []

    def add(self, code: str, mask: pd.Series) -> None:
        """Registra que `code` foi aplicado aos registros marcados em `mask`."""
        mask = mask.fillna(False).astype(bool)
        if mask.any():
            self._entries.append((code, mask))

    def to_series(self) -> pd.Series:
        """Codigos aplicados a cada registro, separados por virgula."""
        result = pd.Series("", index=self._index, dtype="string")
        for code, mask in self._entries:
            result[mask] = result[mask].str.cat([code] * int(mask.sum()), sep=",")
        return result.str.lstrip(",")

    def affected_rows(self) -> int:
        """Numero de registros que sofreram ao menos um ajuste."""
        if not self._entries:
            return 0
        combined = pd.Series(False, index=self._index)
        for _, mask in self._entries:
            combined |= mask
        return int(combined.sum())

    def counts(self) -> dict[str, int]:
        """Quantidade de registros afetados por codigo de ajuste."""
        return {code: int(mask.sum()) for code, mask in self._entries}


def _coherence_flags(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Avalia a coerencia do registro, uma flag por dimensao.

    As regras vem das restricoes declaradas no dicionario oficial (por exemplo:
    "data de entrada na UTI deve ser maior ou igual a data dos primeiros
    sintomas") e de impossibilidades logicas diretas.

    A separacao por dimensao e deliberada. Uma data de internacao impossivel
    compromete indicadores de internacao, mas nao a contagem de casos, que
    depende apenas de `DT_SIN_PRI`. Um unico booleano forcaria descartar o
    registro inteiro -- 4.452 registros a mais, na base de referencia -- por um
    defeito que nao afeta a maior parte das metricas.

    Nenhum registro e removido: as flags apenas descrevem o que ha de errado,
    e cada metrica decide o que e relevante para si.

    Args:
        frame: bloco ja com as colunas de data convertidas.

    Returns:
        Mapa `nome da flag -> serie booleana`, nas chaves de `COHERENCE_FLAGS`.
    """
    floor = pd.Timestamp(MIN_VALID_DATE)
    symptoms = frame["DT_SIN_PRI"]
    typed = frame["DT_DIGITA"]
    admission = frame["DT_INTERNA"]
    icu_in, icu_out = frame["DT_ENTUTI"], frame["DT_SAIDUTI"]
    outcome = frame["DT_EVOLUCA"]

    # --- Eixo temporal primario ---------------------------------------------
    invalid_axis = symptoms.isna()
    invalid_axis |= symptoms.notna() & (symptoms < floor)
    invalid_axis |= symptoms.notna() & typed.notna() & (symptoms > typed)

    # --- Internacao -----------------------------------------------------------
    invalid_admission = admission.notna() & symptoms.notna() & (admission < symptoms)
    invalid_admission |= (frame["HOSPITAL"] == 1) & admission.isna()

    # --- UTI ------------------------------------------------------------------
    invalid_icu = icu_in.notna() & symptoms.notna() & (icu_in < symptoms)
    invalid_icu |= icu_in.notna() & icu_out.notna() & (icu_out < icu_in)
    invalid_icu |= (frame["UTI"] == 1) & icu_in.isna()

    # --- Evolucao -------------------------------------------------------------
    invalid_outcome = outcome.notna() & symptoms.notna() & (outcome < symptoms)
    invalid_outcome |= frame["EVOLUCAO"].isin([1, 2, 3]) & outcome.isna()

    return {
        "flag_data_invalida": invalid_axis.fillna(False).astype(bool),
        "flag_internacao_inconsistente": invalid_admission.fillna(False).astype(bool),
        "flag_uti_inconsistente": invalid_icu.fillna(False).astype(bool),
        "flag_evolucao_inconsistente": invalid_outcome.fillna(False).astype(bool),
    }


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
            "data/raw/manifest.json nao encontrado. Execute primeiro: "
            "python -m src.data.download"
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
                "origin": entry.get("origin", "desconhecida"),
            }
        )

    combined = pd.concat(frames, ignore_index=True)
    assert_no_denied_columns(list(combined.columns))

    combined.to_parquet(settings.processed_parquet_path, index=False)

    payload = report.to_dict(source_files=source_files, run_id=trail.run_id)
    payload["provenance"] = provenance
    settings.quality_report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _append_history(settings.ingestion_history_path, payload)

    trail.record(
        node="preprocess",
        status="ok",
        result_summary=(
            f"{report.rows_written} linhas gravadas, {report.adjusted_rows} ajustadas"
        ),
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
            name: detail["registros"]
            for name, detail in payload["coherence_flags"].items()
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
