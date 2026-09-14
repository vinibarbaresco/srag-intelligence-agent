"""Cobertura vacinal da populacao por UF (referencia externa, SI-PNI).

O SIVEP-Gripe so contem a informacao vacinal de quem adoeceu. A taxa de
vacinacao **da populacao** exige doses aplicadas (SI-PNI / LocalizaSUS) e um
denominador demografico (IBGE). O Ministerio da Saude nao expoe uma API estavel
e publica para as doses por UF e campanha; o dado esta disponivel em paineis e
extracoes periodicas.

Por isso a referencia entra pelo contrato abaixo, um CSV que a equipe preenche a
partir da extracao oficial, e o sistema calcula a cobertura a partir dele. Sem o
arquivo, o indicador permanece explicitamente **nao calculavel** -- nunca
estimado.

Contrato do arquivo `data/reference/cobertura_vacinal_uf.csv`::

    uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url
    SP,2026,influenza,12345678,15000000,SI-PNI,https://...

* `campanha`: `influenza` ou `covid19`;
* `doses_aplicadas`: total de doses da campanha no ano, na UF;
* `populacao_alvo`: publico-alvo da campanha (opcional). Quando ausente, o
  denominador e a populacao residente do IBGE e a cobertura e rotulada como
  "sobre a populacao total";
* `fonte` e `url`: proveniencia da extracao, publicadas com o indicador.

O modelo do arquivo esta em `cobertura_vacinal_uf.template.csv`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

from src.config import get_settings
from src.data.schema import UF_CODES

VACCINATION_COLUMNS: Final[tuple[str, ...]] = (
    "uf",
    "ano",
    "campanha",
    "doses_aplicadas",
    "populacao_alvo",
    "fonte",
    "url",
)

CAMPAIGNS: Final[frozenset[str]] = frozenset({"influenza", "covid19"})

VACCINATION_SOURCE_LABEL: Final[str] = (
    "SI-PNI / LocalizaSUS - doses aplicadas por UF (extracao oficial fornecida em "
    "data/reference/cobertura_vacinal_uf.csv)"
)

#: Conteudo do arquivo-modelo distribuido com o repositorio.
TEMPLATE: Final[str] = (
    "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url\n"
    "# Preencha a partir da extracao oficial do SI-PNI / LocalizaSUS e salve como\n"
    "# cobertura_vacinal_uf.csv (sem estas linhas de comentario).\n"
    "# campanha: influenza | covid19. populacao_alvo e opcional.\n"
)


class VaccinationReferenceError(ValueError):
    """Referencia de cobertura vacinal ausente, ilegivel ou incompleta."""


def read_vaccination_reference(path: Path | None = None) -> pd.DataFrame:
    """Le e valida o CSV de doses aplicadas por UF e campanha.

    Raises:
        VaccinationReferenceError: se o arquivo nao existir ou violar o contrato.
    """
    source = path or get_settings().vaccination_reference_file
    if not source.exists():
        raise VaccinationReferenceError(
            f"Referencia de cobertura vacinal nao encontrada em {source}. Preencha o "
            "arquivo a partir do SI-PNI conforme cobertura_vacinal_uf.template.csv."
        )
    try:
        frame = pd.read_csv(source, comment="#", dtype={"uf": "string", "campanha": "string"})
    except (ValueError, OSError) as exc:
        raise VaccinationReferenceError(f"Referencia de cobertura vacinal ilegivel: {exc}") from exc

    missing = set(VACCINATION_COLUMNS) - set(frame.columns)
    if missing:
        raise VaccinationReferenceError(
            f"Colunas ausentes na referencia vacinal: {sorted(missing)}"
        )
    if frame.empty:
        raise VaccinationReferenceError("Referencia de cobertura vacinal vazia.")

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
    if frame.duplicated(subset=["uf", "ano", "campanha"]).any():
        raise VaccinationReferenceError(
            "Referencia vacinal com mais de uma linha para a mesma UF, ano e campanha."
        )

    return frame[list(VACCINATION_COLUMNS)]
