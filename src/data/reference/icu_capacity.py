"""Capacidade instalada de leitos de UTI por UF e competencia (CNES).

O SIVEP-Gripe conta pacientes; nao conta leitos. Sem um denominador de
capacidade, "taxa de ocupacao de UTI" nao existe -- por isso este modulo, que e
a **unica** porta de entrada da capacidade instalada no sistema.

Fonte
-----
Portal de Dados Abertos do SUS, conjunto "Hospitais e Leitos" (CGHID/MS), que
publica o extrato do CNES por competencia mensal, estabelecimento e tipo de
leito::

    https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos
    https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_csv_<ano>.zip

O arquivo bruto tem 35 colunas, varias delas de contato do estabelecimento
(telefone, e-mail, logradouro). Este modulo le **apenas seis**: a competencia, a
UF e os quatro pares de contagem de UTI que interessam. As demais nunca entram
em memoria -- a mesma regra de minimizacao aplicada ao SIVEP-Gripe.

Contrato do arquivo `data/reference/leitos_uti_uf.csv`
------------------------------------------------------
Uma linha por UF, competencia e tipo de leito::

    uf,competencia,tipo_leito,leitos_existentes,leitos_sus,fonte,url,data_extracao

* `competencia`: `AAAA-MM`, a competencia do CNES;
* `tipo_leito`: um de :data:`ICU_BED_TYPES` (as UTIs adulto, pediatrica,
  neonatal, coronariana e de queimados publicadas pelo CNES);
* `leitos_existentes`: leitos cadastrados, independentemente do financiador;
* `leitos_sus`: subconjunto destinado ao SUS;
* `fonte`, `url`, `data_extracao`: proveniencia, publicada junto do indicador.

O arquivo e acompanhado de `leitos_uti_uf.provenance.json`, com a URL, a data de
extracao, o sha256 do arquivo bruto baixado e o sha256 do CSV gerado.

Uso::

    python -m src.data.reference.icu_capacity                 # ano corrente
    python -m src.data.reference.icu_capacity --year 2026
    python -m src.data.reference.icu_capacity --from-zip Leitos_csv_2026.zip
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd
import requests

from src.config import get_settings
from src.data.schema import UF_CODES
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

CNES_DATASET_URL: Final[str] = "https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos"
CNES_BEDS_URL_TEMPLATE: Final[str] = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_csv_{year}.zip"
)
CNES_SOURCE_LABEL: Final[str] = (
    "CNES / Portal de Dados Abertos do SUS - Hospitais e Leitos (CGHID/MS), "
    "capacidade instalada por competencia"
)

#: Colunas do CSV de referencia, nesta ordem.
ICU_CAPACITY_COLUMNS: Final[tuple[str, ...]] = (
    "uf",
    "competencia",
    "tipo_leito",
    "leitos_existentes",
    "leitos_sus",
    "fonte",
    "url",
    "data_extracao",
)

#: Tipos de leito de UTI aceitos, e o par de colunas do arquivo bruto do CNES
#: que os alimenta (`existentes`, `SUS`).
#:
#: `UTI_TOTAL` fica **fora** de proposito: no arquivo do CNES ele e a soma dos
#: demais, e somar total com as partes contaria cada leito duas vezes.
ICU_BED_SOURCE_COLUMNS: Final[dict[str, tuple[str, str]]] = {
    "UTI_ADULTO": ("UTI_ADULTO_EXIST", "UTI_ADULTO_SUS"),
    "UTI_PEDIATRICA": ("UTI_PEDIATRICO_EXIST", "UTI_PEDIATRICO_SUS"),
    "UTI_NEONATAL": ("UTI_NEONATAL_EXIST", "UTI_NEONATAL_SUS"),
    "UTI_QUEIMADOS": ("UTI_QUEIMADO_EXIST", "UTI_QUEIMADO_SUS"),
    "UTI_CORONARIANA": ("UTI_CORONARIANA_EXIST", "UTI_CORONARIANA_SUS"),
}

#: Dominio fechado do campo `tipo_leito`.
ICU_BED_TYPES: Final[frozenset[str]] = frozenset(ICU_BED_SOURCE_COLUMNS)

#: Tipos de leito que compoem o denominador da ocupacao por SRAG.
#:
#: SRAG hospitalizada e uma doenca respiratoria aguda de adultos e criancas. A
#: UTI neonatal atende recem-nascidos por outras causas, a de queimados e a
#: coronariana sao unidades fechadas para outras condicoes: incluir as tres no
#: denominador diluiria a ocupacao com capacidade que nao esta disponivel para o
#: paciente de SRAG. As tres continuam no arquivo de referencia -- a decisao de
#: escopo e do calculo, nao da ingestao, e fica auditavel.
ICU_BED_TYPES_FOR_SRAG: Final[tuple[str, ...]] = ("UTI_ADULTO", "UTI_PEDIATRICA")

#: Colunas realmente lidas do arquivo bruto do CNES (minimizacao).
_RAW_COLUMNS_READ: Final[tuple[str, ...]] = (
    "COMP",
    "UF",
    *(column for pair in ICU_BED_SOURCE_COLUMNS.values() for column in pair),
)

#: O arquivo do CNES e publicado em latin-1, com `;` como separador.
_RAW_ENCODING: Final[str] = "latin-1"
_RAW_SEPARATOR: Final[str] = ";"

_HTTP_TIMEOUT = (15, 300)


class ICUCapacityReferenceError(ValueError):
    """Referencia de capacidade de UTI ausente, ilegivel ou incompleta."""


# =============================================================================
# Ingestao
# =============================================================================


def _competence(raw: str) -> str | None:
    """Converte a competencia `AAAAMM` do CNES em `AAAA-MM`."""
    text = (raw or "").strip().strip('"')
    if len(text) != 6 or not text.isdigit():
        return None
    month = int(text[4:])
    if not 1 <= month <= 12:
        return None
    return f"{text[:4]}-{text[4:]}"


def _int(raw: str) -> int:
    text = (raw or "").strip().strip('"')
    try:
        return int(float(text)) if text else 0
    except ValueError:
        return 0


def aggregate_cnes_beds(handle: io.TextIOBase, *, url: str, extracted_at: str) -> pd.DataFrame:
    """Agrega o extrato do CNES em leitos de UTI por UF, competencia e tipo.

    A leitura e por linha, nunca inteira em memoria: o arquivo anual tem dezenas
    de milhares de estabelecimentos, e apenas seis das 35 colunas sao lidas.

    Args:
        handle: arquivo de texto ja aberto no extrato `Leitos_<ano>.csv`.
        url: URL de origem, gravada na proveniencia de cada linha.
        extracted_at: data de extracao (ISO), gravada em cada linha.

    Returns:
        Tabela com as colunas de :data:`ICU_CAPACITY_COLUMNS`.

    Raises:
        ICUCapacityReferenceError: se o arquivo nao tiver as colunas esperadas.
    """
    reader = csv.reader(handle, delimiter=_RAW_SEPARATOR)
    try:
        header = [name.strip().strip('"') for name in next(reader)]
    except StopIteration as exc:
        raise ICUCapacityReferenceError("Extrato do CNES vazio.") from exc

    index = {name: position for position, name in enumerate(header)}
    missing = [column for column in _RAW_COLUMNS_READ if column not in index]
    if missing:
        raise ICUCapacityReferenceError(
            f"O extrato do CNES nao tem as colunas esperadas: {missing}. "
            "O layout da fonte pode ter mudado; confira "
            f"{CNES_DATASET_URL} antes de regerar a referencia."
        )

    totals: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    rows_read = 0
    for row in reader:
        if len(row) <= index["UF"]:
            continue
        rows_read += 1
        competence = _competence(row[index["COMP"]])
        uf = row[index["UF"]].strip().strip('"').upper()
        if competence is None or uf not in UF_CODES:
            continue
        for bed_type, (existing_column, sus_column) in ICU_BED_SOURCE_COLUMNS.items():
            existing = _int(row[index[existing_column]])
            sus = _int(row[index[sus_column]])
            if existing == 0 and sus == 0:
                continue
            bucket = totals[(uf, competence, bed_type)]
            bucket[0] += existing
            bucket[1] += sus

    if not totals:
        raise ICUCapacityReferenceError(
            f"O extrato do CNES tem {rows_read} linhas mas nenhum leito de UTI "
            "por UF e competencia; nada a gravar."
        )

    frame = pd.DataFrame(
        [
            {
                "uf": uf,
                "competencia": competence,
                "tipo_leito": bed_type,
                "leitos_existentes": existing,
                "leitos_sus": sus,
                "fonte": CNES_SOURCE_LABEL,
                "url": url,
                "data_extracao": extracted_at,
            }
            for (uf, competence, bed_type), (existing, sus) in sorted(totals.items())
        ],
        columns=list(ICU_CAPACITY_COLUMNS),
    )
    logger.info(
        "extrato do CNES agregado",
        extra={
            "linhas_lidas": rows_read,
            "linhas_geradas": len(frame),
            "competencias": sorted(frame["competencia"].unique()),
        },
    )
    return frame


def fetch_icu_capacity(
    year: int, *, zip_path: Path | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Baixa (ou le do disco) o extrato anual do CNES e o agrega.

    Args:
        year: ano da publicacao do CNES.
        zip_path: arquivo `Leitos_csv_<ano>.zip` ja em disco. Quando informado,
            nada e baixado -- util em ambiente sem rede e para reproduzir uma
            extracao exata.

    Returns:
        Par `(tabela, proveniencia)`.

    Raises:
        ICUCapacityReferenceError: em falha de rede, arquivo invalido ou layout
            inesperado.
    """
    url = CNES_BEDS_URL_TEMPLATE.format(year=year)
    extracted_at = datetime.now(tz=UTC).isoformat()

    if zip_path is not None:
        if not zip_path.exists():
            raise ICUCapacityReferenceError(f"Extrato do CNES nao encontrado em {zip_path}.")
        payload = zip_path.read_bytes()
        origin = f"arquivo local: {zip_path}"
    else:
        try:
            response = requests.get(url, timeout=_HTTP_TIMEOUT)
            response.raise_for_status()
            payload = response.content
        except requests.RequestException as exc:
            raise ICUCapacityReferenceError(
                f"Falha ao baixar o extrato do CNES em {url}: {exc}"
            ) from exc
        origin = url

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise ICUCapacityReferenceError(f"O pacote do CNES em {origin} nao contem CSV.")
        with archive.open(names[0]) as raw:
            frame = aggregate_cnes_beds(
                io.TextIOWrapper(raw, encoding=_RAW_ENCODING, newline=""),
                url=url,
                extracted_at=extracted_at,
            )
    except zipfile.BadZipFile as exc:
        raise ICUCapacityReferenceError(
            f"O pacote do CNES em {origin} nao e um zip valido."
        ) from exc

    _validate(frame)
    provenance = {
        "fonte": CNES_SOURCE_LABEL,
        "dataset": CNES_DATASET_URL,
        "url": url,
        "origem": origin,
        "arquivo_bruto": names[0],
        "sha256_arquivo_bruto": hashlib.sha256(payload).hexdigest(),
        "ano_da_publicacao": year,
        "competencias_obtidas": sorted(frame["competencia"].unique()),
        "tipos_de_leito": sorted(frame["tipo_leito"].unique()),
        "colunas_lidas_do_bruto": list(_RAW_COLUMNS_READ),
        "obtido_em": extracted_at,
    }
    return frame, provenance


# =============================================================================
# Leitura, validacao e persistencia
# =============================================================================


def _validate(frame: pd.DataFrame) -> None:
    missing = set(ICU_CAPACITY_COLUMNS) - set(frame.columns)
    if missing:
        raise ICUCapacityReferenceError(
            f"Colunas ausentes na referencia de leitos de UTI: {sorted(missing)}"
        )
    if frame.empty:
        raise ICUCapacityReferenceError("Referencia de leitos de UTI vazia.")

    unknown_uf = sorted(set(frame["uf"]) - set(UF_CODES))
    if unknown_uf:
        raise ICUCapacityReferenceError(
            f"UFs desconhecidas na referencia de leitos de UTI: {unknown_uf}"
        )
    unknown_type = sorted(set(frame["tipo_leito"]) - ICU_BED_TYPES)
    if unknown_type:
        raise ICUCapacityReferenceError(
            f"Tipos de leito desconhecidos: {unknown_type}. Aceitos: {sorted(ICU_BED_TYPES)}"
        )
    invalid = sorted(
        competence
        for competence in set(frame["competencia"])
        if _competence(str(competence).replace("-", "")) is None
    )
    if invalid:
        raise ICUCapacityReferenceError(
            f"Competencias fora do formato AAAA-MM na referencia de leitos: {invalid}"
        )
    if (frame["leitos_existentes"] < 0).any() or (frame["leitos_sus"] < 0).any():
        raise ICUCapacityReferenceError("Contagem de leitos negativa na referencia.")
    if (frame["leitos_sus"] > frame["leitos_existentes"]).any():
        raise ICUCapacityReferenceError(
            "Ha linhas com mais leitos SUS do que leitos existentes; o "
            "subconjunto SUS nao pode exceder o total cadastrado."
        )
    if frame.duplicated(subset=["uf", "competencia", "tipo_leito"]).any():
        raise ICUCapacityReferenceError(
            "Referencia de leitos com mais de uma linha para a mesma UF, "
            "competencia e tipo de leito."
        )


def provenance_path(csv_path: Path) -> Path:
    return csv_path.with_name(csv_path.stem + ".provenance.json")


def write_icu_capacity_reference(
    frame: pd.DataFrame, provenance: dict[str, Any], path: Path | None = None
) -> Path:
    """Grava o CSV de capacidade e o arquivo de proveniencia (com sha256)."""
    target = path or get_settings().icu_capacity_reference_file
    target.parent.mkdir(parents=True, exist_ok=True)

    ordered = frame.sort_values(["competencia", "uf", "tipo_leito"]).reset_index(drop=True)
    ordered.to_csv(target, index=False, encoding="utf-8", lineterminator="\n")

    record = {
        **provenance,
        "arquivo": target.name,
        "linhas": int(len(ordered)),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    provenance_path(target).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "referencia de leitos de UTI gravada",
        extra={"arquivo": str(target), "linhas": len(ordered)},
    )
    return target


def read_icu_capacity_reference(path: Path | None = None) -> pd.DataFrame:
    """Le e valida o CSV de capacidade instalada de UTI.

    Raises:
        ICUCapacityReferenceError: se o arquivo nao existir ou violar o contrato.
    """
    source = path or get_settings().icu_capacity_reference_file
    if not source.exists():
        raise ICUCapacityReferenceError(
            f"Referencia de leitos de UTI nao encontrada em {source}. Execute: "
            "python -m src.data.reference.icu_capacity"
        )
    try:
        frame = pd.read_csv(
            source,
            comment="#",
            dtype={"uf": "string", "competencia": "string", "tipo_leito": "string"},
        )
    except (ValueError, OSError) as exc:
        raise ICUCapacityReferenceError(f"Referencia de leitos de UTI ilegivel: {exc}") from exc

    missing = set(ICU_CAPACITY_COLUMNS) - set(frame.columns)
    if missing:
        raise ICUCapacityReferenceError(
            f"Colunas ausentes na referencia de leitos de UTI: {sorted(missing)}"
        )
    if frame.empty:
        raise ICUCapacityReferenceError("Referencia de leitos de UTI vazia.")

    frame["uf"] = frame["uf"].str.upper()
    frame["tipo_leito"] = frame["tipo_leito"].str.upper()
    for column in ("leitos_existentes", "leitos_sus"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("int64")

    _validate(frame)
    return frame[list(ICU_CAPACITY_COLUMNS)]


def read_provenance(path: Path | None = None) -> dict[str, Any] | None:
    """Proveniencia da referencia, ou `None` se nao houver arquivo."""
    source = provenance_path(path or get_settings().icu_capacity_reference_file)
    if not source.exists():
        return None
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(
        description="Atualiza a referencia de leitos de UTI (CNES) por UF e competencia."
    )
    parser.add_argument(
        "--year", type=int, default=date.today().year, help="ano da publicacao do CNES"
    )
    parser.add_argument(
        "--from-zip",
        type=Path,
        default=None,
        help="Leitos_csv_<ano>.zip ja em disco, usado no lugar do download",
    )
    parser.add_argument("--output", type=Path, default=None, help="CSV de destino")
    args = parser.parse_args(argv)

    try:
        frame, provenance = fetch_icu_capacity(args.year, zip_path=args.from_zip)
        target = write_icu_capacity_reference(frame, provenance, args.output)
    except ICUCapacityReferenceError as exc:
        logger.error("atualizacao da referencia de leitos falhou", extra={"motivo": str(exc)})
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    print(f"{target}: {len(frame)} linhas")
    print(f"Competencias: {', '.join(provenance['competencias_obtidas'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
