"""Cobertura vacinal da populacao por UF (referencia externa, SI-PNI).

O SIVEP-Gripe so contem a informacao vacinal de quem adoeceu. A taxa de
vacinacao **da populacao** exige doses aplicadas (SI-PNI, via RNDS) e um
denominador demografico -- a populacao-alvo da campanha ou, na falta dela, a
populacao residente estimada pelo IBGE. Este modulo e a unica porta de entrada
dessa informacao no sistema.

Fonte
-----
Portal de Dados Abertos do SUS, conjunto "Doses aplicadas pelo Programa Nacional
de Imunizacoes (PNI)", publicado em arquivos mensais::

    https://dadosabertos.saude.gov.br/dataset/doses-aplicadas-pelo-programa-de-nacional-de-imunizacoes-pni-<ano>
    https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/PNI/csv/vacinacao_<mes>_<ano>_csv.zip

O arquivo mensal e um extrato **por dose aplicada**, com 60 colunas e entre 1,8
e 4,3 GB comprimidos por mes. Duas consequencias de projeto:

1. **Ele nunca e persistido.** :func:`aggregate_pni_csv` le o arquivo em fluxo,
   consome apenas quatro das 60 colunas (UF do paciente, vacina, data e status
   do documento) e emite somente o agregado por UF, campanha e ano. Nenhuma
   linha individual -- e nenhum `co_paciente` -- chega ao disco, ao banco ou ao
   modelo.
2. **A agregacao nao roda no CI nem no `--setup`.** Baixar dezenas de GB por
   execucao seria irreal. O artefato versionado e o CSV agregado; regera-lo e
   uma operacao deliberada, com o comando abaixo.

Uso::

    # agrega os extratos mensais ja baixados (aceita .zip ou .csv)
    python -m src.data.reference.vaccination --from-pni vacinacao_*_2026_csv.zip --year 2026
    # informa a populacao-alvo de cada campanha, quando publicada
    python -m src.data.reference.vaccination --from-pni ... --target-population alvo.csv

Contrato do arquivo `data/reference/cobertura_vacinal_uf.csv`
--------------------------------------------------------------
::

    uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url,data_extracao
    SP,2026,influenza,12345678,15000000,SI-PNI,https://...,2026-09-17

* `campanha`: `influenza` ou `covid19` -- as duas campanhas com equivalente no
  SIVEP-Gripe (`VACINA` e `VACINA_COV`), mantidas separadas porque tem publico
  alvo, esquema de doses e sazonalidade distintos;
* `doses_aplicadas`: total de doses da campanha no ano, na UF;
* `populacao_alvo`: publico-alvo da campanha. **Opcional**: quando ausente, o
  denominador passa a ser a populacao residente do IBGE e a cobertura e
  rotulada como "sobre a populacao total", que subestima a cobertura do
  publico-alvo -- a substituicao nunca e silenciosa;
* `fonte`, `url`, `data_extracao`: proveniencia, publicada com o indicador.
  `data_extracao` e opcional para compatibilidade com referencias antigas.

Sem o arquivo, o indicador permanece explicitamente **nao calculavel** -- nunca
estimado a partir dos casos de SRAG.

O modelo do arquivo esta em `cobertura_vacinal_uf.template.csv`; a fixture de
teste, claramente rotulada como sintetica, em `tests/`.
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

from src.config import get_settings
from src.data.schema import UF_CODES
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

PNI_DATASET_URL_TEMPLATE: Final[str] = (
    "https://dadosabertos.saude.gov.br/dataset/"
    "doses-aplicadas-pelo-programa-de-nacional-de-imunizacoes-pni-{year}"
)
PNI_MONTHLY_URL_TEMPLATE: Final[str] = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/PNI/csv/vacinacao_{month}_{year}_csv.zip"
)

VACCINATION_COLUMNS: Final[tuple[str, ...]] = (
    "uf",
    "ano",
    "campanha",
    "doses_aplicadas",
    "populacao_alvo",
    "fonte",
    "url",
    "data_extracao",
)

#: Colunas exigidas no arquivo. `data_extracao` fica de fora: referencias
#: preenchidas a mao antes desta versao continuam validas, e a ausencia vira
#: `None` -- declarada no relatorio como proveniencia incompleta.
_REQUIRED_COLUMNS: Final[tuple[str, ...]] = VACCINATION_COLUMNS[:-1]

CAMPAIGNS: Final[frozenset[str]] = frozenset({"influenza", "covid19"})

VACCINATION_SOURCE_LABEL: Final[str] = (
    "SI-PNI / Portal de Dados Abertos do SUS - doses aplicadas pelo Programa "
    "Nacional de Imunizacoes, agregadas por UF, campanha e ano"
)

# --- Classificacao da campanha ----------------------------------------------
#
# O extrato do PNI nomeia o imunobiologico em texto livre (`ds_vacina`, ex.:
# "VACINA COVID-19 COMIRNATY", "VACINA INFLUENZA TRIVALENTE"). A classificacao
# e por palavra-chave, deliberadamente conservadora: uma dose que nao case com
# nenhuma das duas campanhas e **descartada e contada**, nunca atribuida por
# proximidade. As demais vacinas do calendario (BCG, tríplice, HPV) nao tem
# equivalente no SIVEP-Gripe e nao entram no indicador.

#: Palavras-chave que identificam cada campanha no texto do imunobiologico.
CAMPAIGN_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "covid19": ("COVID", "CORONAVIRUS", "SARS-COV"),
    "influenza": ("INFLUENZA", "GRIPE"),
}

#: Status de documento aceito no agregado.
#:
#: A RNDS mantem o historico de correcoes: um registro substituido continua no
#: extrato ao lado do que o substituiu. Somar os dois contaria a mesma dose duas
#: vezes, entao apenas o registro final entra.
_FINAL_DOCUMENT_STATUS: Final[str] = "final"

#: Colunas realmente lidas do extrato do PNI (quatro de 60).
_RAW_COLUMNS_READ: Final[tuple[str, ...]] = (
    "sg_uf_paciente",
    "ds_vacina",
    "dt_vacina",
    "st_documento",
)

_RAW_SEPARATOR: Final[str] = ";"
_RAW_ENCODING: Final[str] = "utf-8"

#: Conteudo do arquivo-modelo distribuido com o repositorio.
TEMPLATE: Final[str] = (
    "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url,data_extracao\n"
    "# Gere com `python -m src.data.reference.vaccination --from-pni <extratos> --year <ano>`\n"
    "# ou preencha a partir da extracao oficial do SI-PNI e salve como\n"
    "# cobertura_vacinal_uf.csv (sem estas linhas de comentario).\n"
    "# campanha: influenza | covid19. populacao_alvo e data_extracao sao opcionais.\n"
)


class VaccinationReferenceError(ValueError):
    """Referencia de cobertura vacinal ausente, ilegivel ou incompleta."""


# =============================================================================
# Ingestao a partir do extrato oficial do PNI
# =============================================================================


def classify_campaign(description: str) -> str | None:
    """Classifica um imunobiologico em `influenza`, `covid19` ou `None`.

    A ordem importa: "covid" e testado antes de "influenza" porque ha
    apresentacoes que citam as duas (campanhas conjuntas), e nesses casos o
    registro descreve a dose de covid-19.
    """
    text = (description or "").upper()
    for campaign, keywords in CAMPAIGN_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return campaign
    return None


def _open_pni_extract(path: Path) -> Any:
    """Abre o extrato mensal, seja `.zip` (como publicado) ou `.csv` solto."""
    if path.suffix.lower() == ".zip":
        archive = zipfile.ZipFile(path)
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise VaccinationReferenceError(f"O pacote do PNI em {path} nao contem CSV.")
        return io.TextIOWrapper(archive.open(names[0]), encoding=_RAW_ENCODING, newline="")
    return path.open(encoding=_RAW_ENCODING, newline="")


def aggregate_pni_csv(
    handle: io.TextIOBase, *, year: int, totals: dict[tuple[str, str], int] | None = None
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Agrega um extrato de doses do PNI em doses por UF e campanha.

    A leitura e linha a linha e nada e retido: o extrato mensal tem dezenas de
    milhoes de registros e contem um identificador pseudonimizado de paciente,
    que este agregador nunca le nem grava.

    Args:
        handle: extrato do PNI ja aberto como texto.
        year: ano da campanha; doses de outro ano sao descartadas e contadas.
        totals: acumulador, para somar varios meses na mesma execucao.

    Returns:
        Par `(doses por (uf, campanha), diagnostico)`. O diagnostico traz o que
        foi descartado e por que -- descarte silencioso nao e aceitavel aqui.

    Raises:
        VaccinationReferenceError: se o extrato nao tiver as colunas esperadas.
    """
    reader = csv.reader(handle, delimiter=_RAW_SEPARATOR)
    try:
        header = [name.strip().strip('"').lower() for name in next(reader)]
    except StopIteration as exc:
        raise VaccinationReferenceError("Extrato do PNI vazio.") from exc

    index = {name: position for position, name in enumerate(header)}
    missing = [column for column in _RAW_COLUMNS_READ if column not in index]
    if missing:
        raise VaccinationReferenceError(
            f"O extrato do PNI nao tem as colunas esperadas: {missing}. Confira o "
            "dicionario de dados do conjunto antes de regerar a referencia."
        )

    accumulated = totals if totals is not None else defaultdict(int)
    discarded = {
        "linhas_lidas": 0,
        "documento_nao_final": 0,
        "outro_ano": 0,
        "campanha_fora_do_escopo": 0,
        "uf_invalida": 0,
        "data_ilegivel": 0,
    }

    for row in reader:
        if len(row) < len(header):
            continue
        discarded["linhas_lidas"] += 1

        status = row[index["st_documento"]].strip().strip('"').lower()
        if status and status != _FINAL_DOCUMENT_STATUS:
            discarded["documento_nao_final"] += 1
            continue

        raw_date = row[index["dt_vacina"]].strip().strip('"')[:10]
        try:
            applied = date.fromisoformat(raw_date)
        except ValueError:
            discarded["data_ilegivel"] += 1
            continue
        if applied.year != year:
            discarded["outro_ano"] += 1
            continue

        campaign = classify_campaign(row[index["ds_vacina"]])
        if campaign is None:
            discarded["campanha_fora_do_escopo"] += 1
            continue

        uf = row[index["sg_uf_paciente"]].strip().strip('"').upper()
        if uf not in UF_CODES:
            discarded["uf_invalida"] += 1
            continue

        accumulated[(uf, campaign)] += 1

    return accumulated, discarded


def build_vaccination_reference(
    extracts: list[Path],
    *,
    year: int,
    target_population: dict[tuple[str, str], int] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Agrega um ou mais extratos do PNI no contrato da referencia.

    Args:
        extracts: arquivos `vacinacao_<mes>_<ano>_csv.zip` (ou CSV) ja em disco.
        year: ano da campanha.
        target_population: populacao-alvo por `(uf, campanha)`, quando publicada.

    Returns:
        Par `(tabela, proveniencia)`.
    """
    if not extracts:
        raise VaccinationReferenceError("Nenhum extrato do PNI informado.")

    extracted_at = datetime.now(tz=UTC).isoformat()
    totals: dict[tuple[str, str], int] = defaultdict(int)
    files: list[dict[str, Any]] = []

    for path in extracts:
        if not path.exists():
            raise VaccinationReferenceError(f"Extrato do PNI nao encontrado em {path}.")
        with _open_pni_extract(path) as handle:
            _, discarded = aggregate_pni_csv(handle, year=year, totals=totals)
        files.append(
            {
                "arquivo": path.name,
                "sha256": _sha256_file(path),
                "linhas_lidas": discarded["linhas_lidas"],
                "descartes": {
                    key: value for key, value in discarded.items() if key != "linhas_lidas"
                },
            }
        )
        logger.info(
            "extrato do PNI agregado",
            extra={"arquivo": path.name, **discarded},
        )

    if not totals:
        raise VaccinationReferenceError(
            f"Nenhuma dose de influenza ou covid-19 de {year} encontrada nos "
            "extratos informados; nada a gravar."
        )

    dataset_url = PNI_DATASET_URL_TEMPLATE.format(year=year)
    targets = target_population or {}
    frame = pd.DataFrame(
        [
            {
                "uf": uf,
                "ano": year,
                "campanha": campaign,
                "doses_aplicadas": doses,
                "populacao_alvo": targets.get((uf, campaign)),
                "fonte": VACCINATION_SOURCE_LABEL,
                "url": dataset_url,
                "data_extracao": extracted_at[:10],
            }
            for (uf, campaign), doses in sorted(totals.items())
        ],
        columns=list(VACCINATION_COLUMNS),
    )

    provenance = {
        "fonte": VACCINATION_SOURCE_LABEL,
        "dataset": dataset_url,
        "url": dataset_url,
        "ano_da_campanha": year,
        "extratos": files,
        "colunas_lidas_do_bruto": list(_RAW_COLUMNS_READ),
        "campanhas_obtidas": sorted({campaign for _, campaign in totals}),
        "populacao_alvo_informada": bool(targets),
        "nota_de_privacidade": (
            "O extrato bruto do PNI e por dose aplicada e contem identificador "
            "pseudonimizado de paciente. Ele nao e persistido: apenas quatro "
            "colunas sao lidas em fluxo e somente o agregado por UF, campanha e "
            "ano e gravado."
        ),
        "obtido_em": extracted_at,
    }
    return frame, provenance


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_target_population(path: Path) -> dict[tuple[str, str], int]:
    """Le a populacao-alvo por UF e campanha de um CSV `uf,campanha,populacao_alvo`."""
    frame = pd.read_csv(path, comment="#")
    required = {"uf", "campanha", "populacao_alvo"}
    if not required.issubset(frame.columns):
        raise VaccinationReferenceError(
            f"O arquivo de populacao-alvo precisa das colunas {sorted(required)}."
        )
    return {
        (str(row.uf).upper(), str(row.campanha).lower()): int(row.populacao_alvo)
        for row in frame.itertuples(index=False)
        if pd.notna(row.populacao_alvo)
    }


# =============================================================================
# Leitura, validacao e persistencia
# =============================================================================


def provenance_path(csv_path: Path) -> Path:
    return csv_path.with_name(csv_path.stem + ".provenance.json")


def write_vaccination_reference(
    frame: pd.DataFrame, provenance: dict[str, Any], path: Path | None = None
) -> Path:
    """Grava o CSV e o arquivo de proveniencia (com sha256 do CSV)."""
    target = path or get_settings().vaccination_reference_file
    target.parent.mkdir(parents=True, exist_ok=True)

    ordered = frame.sort_values(["ano", "campanha", "uf"]).reset_index(drop=True)
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
        "referencia de cobertura vacinal gravada",
        extra={"arquivo": str(target), "linhas": len(ordered)},
    )
    return target


def read_vaccination_reference(path: Path | None = None) -> pd.DataFrame:
    """Le e valida o CSV de doses aplicadas por UF e campanha.

    Raises:
        VaccinationReferenceError: se o arquivo nao existir ou violar o contrato.
    """
    source = path or get_settings().vaccination_reference_file
    if not source.exists():
        raise VaccinationReferenceError(
            f"Referencia de cobertura vacinal nao encontrada em {source}. Gere-a com "
            "`python -m src.data.reference.vaccination --from-pni <extratos> --year <ano>` "
            "ou preencha o arquivo conforme cobertura_vacinal_uf.template.csv."
        )
    try:
        frame = pd.read_csv(source, comment="#", dtype={"uf": "string", "campanha": "string"})
    except (ValueError, OSError) as exc:
        raise VaccinationReferenceError(f"Referencia de cobertura vacinal ilegivel: {exc}") from exc

    missing = set(_REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise VaccinationReferenceError(
            f"Colunas ausentes na referencia vacinal: {sorted(missing)}"
        )
    if frame.empty:
        raise VaccinationReferenceError("Referencia de cobertura vacinal vazia.")

    if "data_extracao" not in frame.columns:
        frame["data_extracao"] = None

    frame["uf"] = frame["uf"].str.upper()
    frame["campanha"] = frame["campanha"].str.lower()

    unknown_uf = sorted(set(frame["uf"]) - set(UF_CODES))
    if unknown_uf:
        raise VaccinationReferenceError(f"UFs desconhecidas na referencia vacinal: {unknown_uf}")
    unknown_campaign = sorted(set(frame["campanha"]) - CAMPAIGNS)
    if unknown_campaign:
        raise VaccinationReferenceError(
            f"Campanhas desconhecidas: {unknown_campaign}. Aceitas: {sorted(CAMPAIGNS)}"
        )

    frame["ano"] = pd.to_numeric(frame["ano"], errors="raise").astype("int64")
    frame["doses_aplicadas"] = pd.to_numeric(frame["doses_aplicadas"], errors="raise").astype(
        "int64"
    )
    frame["populacao_alvo"] = pd.to_numeric(frame["populacao_alvo"], errors="coerce").astype(
        "Int64"
    )
    if (frame["doses_aplicadas"] < 0).any():
        raise VaccinationReferenceError("Doses aplicadas negativas na referencia vacinal.")
    # Populacao-alvo nula e legitima (o denominador cai para o IBGE); zero ou
    # negativa nao e: produziria divisao por zero ou cobertura negativa, e um
    # denominador impossivel precisa ser rejeitado na porta de entrada.
    if (frame["populacao_alvo"].notna() & (frame["populacao_alvo"] <= 0)).any():
        raise VaccinationReferenceError(
            "Populacao-alvo nao positiva na referencia vacinal. Deixe a celula "
            "vazia para usar a populacao residente do IBGE como denominador."
        )
    if frame.duplicated(subset=["uf", "ano", "campanha"]).any():
        raise VaccinationReferenceError(
            "Referencia vacinal com mais de uma linha para a mesma UF, ano e campanha."
        )

    return frame[list(VACCINATION_COLUMNS)]


def read_provenance(path: Path | None = None) -> dict[str, Any] | None:
    """Proveniencia da referencia, ou `None` se nao houver arquivo."""
    source = provenance_path(path or get_settings().vaccination_reference_file)
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
        description=(
            "Agrega extratos de doses aplicadas do PNI na referencia de cobertura "
            "vacinal por UF, campanha e ano."
        )
    )
    parser.add_argument(
        "--from-pni",
        type=Path,
        nargs="+",
        required=True,
        help="extratos mensais do PNI (vacinacao_<mes>_<ano>_csv.zip ou .csv)",
    )
    parser.add_argument("--year", type=int, required=True, help="ano da campanha")
    parser.add_argument(
        "--target-population",
        type=Path,
        default=None,
        help="CSV uf,campanha,populacao_alvo com o publico-alvo de cada campanha",
    )
    parser.add_argument("--output", type=Path, default=None, help="CSV de destino")
    args = parser.parse_args(argv)

    try:
        targets = read_target_population(args.target_population) if args.target_population else None
        frame, provenance = build_vaccination_reference(
            args.from_pni, year=args.year, target_population=targets
        )
        target = write_vaccination_reference(frame, provenance, args.output)
    except VaccinationReferenceError as exc:
        logger.error("atualizacao da referencia vacinal falhou", extra={"motivo": str(exc)})
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    print(f"{target}: {len(frame)} linhas")
    print(f"Campanhas: {', '.join(provenance['campanhas_obtidas'])}")
    if not provenance["populacao_alvo_informada"]:
        print(
            "AVISO: sem populacao-alvo informada; a cobertura sera calculada "
            "sobre a populacao residente do IBGE e rotulada como tal."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
