"""Populacao residente estimada por UF (IBGE).

Fonte: API de agregados do IBGE, tabela 6579 ("Populacao residente estimada"),
variavel 9324, nivel territorial N3 (unidade da federacao). A estimativa anual e
a mesma usada pelo Ministerio da Saude como denominador de taxas por 100 mil
habitantes.

Uso::

    python -m src.data.reference.population            # atualiza data/reference/
    python -m src.data.reference.population --period 2025

O CSV gerado tem tres colunas -- `uf`, `ano`, `populacao` -- e e acompanhado de
`populacao_uf.provenance.json`. Nenhum outro dado do IBGE e lido.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd
import requests

from src.config import get_settings
from src.data.schema import UF_CODES
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

IBGE_AGGREGATE: Final[str] = "6579"
IBGE_VARIABLE: Final[str] = "9324"
IBGE_API_URL: Final[str] = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/{aggregate}/periodos/{period}"
    "/variaveis/{variable}?localidades=N3[all]"
)
IBGE_SOURCE_LABEL: Final[str] = "IBGE - Populacao residente estimada (tabela 6579, variavel 9324)"

#: Colunas do CSV de referencia, nesta ordem.
POPULATION_COLUMNS: Final[tuple[str, ...]] = ("uf", "ano", "populacao")

#: Codigo IBGE da UF -> sigla. O nome da localidade nao e usado como chave
#: porque carrega acentos e pode mudar de grafia; o codigo e estavel.
IBGE_UF_CODES: Final[dict[str, str]] = {
    "11": "RO",
    "12": "AC",
    "13": "AM",
    "14": "RR",
    "15": "PA",
    "16": "AP",
    "17": "TO",
    "21": "MA",
    "22": "PI",
    "23": "CE",
    "24": "RN",
    "25": "PB",
    "26": "PE",
    "27": "AL",
    "28": "SE",
    "29": "BA",
    "31": "MG",
    "32": "ES",
    "33": "RJ",
    "35": "SP",
    "41": "PR",
    "42": "SC",
    "43": "RS",
    "50": "MS",
    "51": "MT",
    "52": "GO",
    "53": "DF",
}

_HTTP_TIMEOUT = (15, 60)


class PopulationReferenceError(ValueError):
    """Referencia populacional ausente, ilegivel ou incompleta."""


def fetch_population(period: str = "-1") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Consulta a API do IBGE e devolve a populacao por UF.

    Args:
        period: periodo da estimativa; `-1` e o mais recente publicado.

    Returns:
        Par `(tabela, proveniencia)`. A tabela tem as colunas de
        :data:`POPULATION_COLUMNS`; a proveniencia descreve a consulta.

    Raises:
        PopulationReferenceError: se a API falhar ou devolver menos de 27 UFs.
    """
    url = IBGE_API_URL.format(aggregate=IBGE_AGGREGATE, period=period, variable=IBGE_VARIABLE)
    try:
        response = requests.get(url, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PopulationReferenceError(f"Falha ao consultar o IBGE em {url}: {exc}") from exc

    rows: list[dict[str, Any]] = []
    try:
        for item in payload[0]["resultados"][0]["series"]:
            uf = IBGE_UF_CODES.get(str(item["localidade"]["id"]))
            if uf is None:
                continue
            for year, value in item["serie"].items():
                rows.append({"uf": uf, "ano": int(year), "populacao": int(value)})
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PopulationReferenceError(f"Resposta do IBGE em formato inesperado: {exc}") from exc

    frame = pd.DataFrame(rows, columns=list(POPULATION_COLUMNS))
    _validate(frame)

    provenance = {
        "fonte": IBGE_SOURCE_LABEL,
        "url": url,
        "agregado": IBGE_AGGREGATE,
        "variavel": IBGE_VARIABLE,
        "periodo_solicitado": period,
        "anos_obtidos": sorted(int(year) for year in frame["ano"].unique()),
        "obtido_em": datetime.now(tz=UTC).isoformat(),
    }
    return frame, provenance


def _validate(frame: pd.DataFrame) -> None:
    missing = set(POPULATION_COLUMNS) - set(frame.columns)
    if missing:
        raise PopulationReferenceError(f"Colunas ausentes na referencia populacional: {missing}")
    if frame.empty:
        raise PopulationReferenceError("Referencia populacional vazia.")

    unknown = sorted(set(frame["uf"]) - set(UF_CODES))
    if unknown:
        raise PopulationReferenceError(f"UFs desconhecidas na referencia populacional: {unknown}")
    if (frame["populacao"] <= 0).any():
        raise PopulationReferenceError("Populacao nao positiva na referencia.")

    for year, group in frame.groupby("ano"):
        absent = sorted(set(UF_CODES) - set(group["uf"]))
        if absent:
            raise PopulationReferenceError(
                f"Referencia populacional de {year} sem todas as UFs; faltam {absent}."
            )


def provenance_path(csv_path: Path) -> Path:
    return csv_path.with_name(csv_path.stem + ".provenance.json")


def write_population_reference(
    frame: pd.DataFrame, provenance: dict[str, Any], path: Path | None = None
) -> Path:
    """Grava o CSV e o arquivo de proveniencia (com sha256 do CSV)."""
    target = path or get_settings().population_reference_file
    target.parent.mkdir(parents=True, exist_ok=True)

    ordered = frame.sort_values(["ano", "uf"]).reset_index(drop=True)
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
        "referencia populacional gravada",
        extra={"arquivo": str(target), "linhas": len(ordered), "anos": record["anos_obtidos"]},
    )
    return target


def read_population_reference(path: Path | None = None) -> pd.DataFrame:
    """Le e valida o CSV de populacao por UF.

    Raises:
        PopulationReferenceError: se o arquivo nao existir ou for invalido.
    """
    source = path or get_settings().population_reference_file
    if not source.exists():
        raise PopulationReferenceError(
            f"Referencia populacional nao encontrada em {source}. Execute: "
            "python -m src.data.reference.population"
        )
    try:
        frame = pd.read_csv(source, dtype={"uf": "string", "ano": "int64", "populacao": "int64"})
    except (ValueError, OSError) as exc:
        raise PopulationReferenceError(f"Referencia populacional ilegivel: {exc}") from exc

    frame["uf"] = frame["uf"].str.upper()
    _validate(frame)
    return frame[list(POPULATION_COLUMNS)]


def read_provenance(path: Path | None = None) -> dict[str, Any] | None:
    """Proveniencia da referencia, ou `None` se nao houver arquivo."""
    source = provenance_path(path or get_settings().population_reference_file)
    if not source.exists():
        return None
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(description="Atualiza a referencia populacional do IBGE.")
    parser.add_argument(
        "--period", default="-1", help="periodo da estimativa (padrao: -1, o mais recente)"
    )
    parser.add_argument("--output", type=Path, default=None, help="CSV de destino")
    args = parser.parse_args(argv)

    try:
        frame, provenance = fetch_population(args.period)
        target = write_population_reference(frame, provenance, args.output)
    except PopulationReferenceError as exc:
        logger.error("atualizacao da referencia populacional falhou", extra={"motivo": str(exc)})
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    print(f"{target}: {len(frame)} linhas, anos {provenance['anos_obtidos']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
