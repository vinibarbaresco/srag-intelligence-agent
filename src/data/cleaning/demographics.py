"""Regras demograficas: normalizacao e agregacao da idade."""

from __future__ import annotations

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import AGE_BANDS

#: Limites de plausibilidade da idade humana, em anos.
MIN_PLAUSIBLE_AGE = 0
MAX_PLAUSIBLE_AGE = 120


def age_in_years(values: pd.Series, units: pd.Series) -> pd.Series:
    """Normaliza a idade para anos usando a unidade declarada no registro.

    O SIVEP-Gripe guarda a idade em `NU_IDADE_N` e a unidade em `TP_IDADE`
    (1-dia, 2-mes, 3-ano). Ler o numero sem a unidade transformaria um recem
    nascido de 10 dias em uma pessoa de 10 anos.

    Args:
        values: coluna `NU_IDADE_N`.
        units: coluna `TP_IDADE`.

    Returns:
        Idade em anos, com nulo onde a unidade e desconhecida.
    """
    amount = pd.to_numeric(values, errors="coerce")
    unit = pd.to_numeric(units, errors="coerce")

    years = pd.Series(pd.NA, index=amount.index, dtype="Float32")
    years[unit == 1] = amount[unit == 1] / 365.25
    years[unit == 2] = amount[unit == 2] / 12
    years[unit == 3] = amount[unit == 3]
    return years


def age_band(years: pd.Series) -> pd.Series:
    """Agrega a idade em faixas."""
    band = pd.Series(pd.NA, index=years.index, dtype="string")
    for low, high, label in AGE_BANDS:
        band[(years >= low) & (years <= high)] = label
    return band


class DeriveAge(CleaningRule):
    """Normaliza a idade em anos e a agrega em faixa etaria."""

    name = "deriva_idade"
    description = (
        "Converte NU_IDADE_N para anos conforme TP_IDADE (1-dia, 2-mes, 3-ano) "
        f"e anula valores fora de [{MIN_PLAUSIBLE_AGE}, {MAX_PLAUSIBLE_AGE}] "
        "anos, registrando o ajuste `idade_anulada`. Em seguida agrega a idade "
        "em faixa etaria: a camada analitica trabalha com a faixa, nao com a "
        "idade exata, por minimizacao de dados."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        frame["idade_anos"] = age_in_years(frame["NU_IDADE_N"], frame["TP_IDADE"])

        implausible = frame["idade_anos"].notna() & (
            (frame["idade_anos"] < MIN_PLAUSIBLE_AGE)
            | (frame["idade_anos"] > MAX_PLAUSIBLE_AGE)
        )
        context.report.age_out_of_range += int(implausible.sum())
        context.adjustments.add("idade_anulada", implausible)
        frame.loc[implausible, "idade_anos"] = pd.NA

        frame["faixa_etaria"] = age_band(frame["idade_anos"])
        return frame


class TagSourceYear(CleaningRule):
    """Marca de qual arquivo anual o registro veio."""

    name = "marca_ano_de_origem"
    description = (
        "Grava o ano do arquivo de origem em `ano_referencia`, permitindo "
        "rastrear de qual safra do DATASUS cada registro veio."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        frame["ano_referencia"] = context.year
        return frame
