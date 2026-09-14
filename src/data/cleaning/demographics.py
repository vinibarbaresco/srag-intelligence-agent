"""Regras demograficas: normalizacao e agregacao da idade."""

from __future__ import annotations

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import AGE_BANDS

#: Limites de plausibilidade da idade humana, em anos.
#:
#: O dicionario oficial declara "Idade deve ser <= 150" -- uma validacao de
#: entrada do sistema, nao uma afirmacao biologica. Adotamos 120, proximo ao
#: maior valor humano ja verificado (122 anos), porque o objetivo aqui e
#: plausibilidade e nao compatibilidade com o formulario. A divergencia e
#: deliberada e os registros afetados ficam rastreaveis pelo ajuste
#: `idade_anulada`: na base de referencia sao 2, com idades de -1 e 141 anos.
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


#: Dominio de `NU_IDADE_N` por unidade declarada em `TP_IDADE`, do dicionario
#: oficial: 1-Dia admite 0 a 30, 2-Mes admite 1 a 11, 3-Ano nao tem limite
#: proprio (a plausibilidade biologica, em anos, ja o cobre).
#:
#: Uma divergencia deliberada: o piso de `2-Mes` e 0, e nao o 1 do dicionario.
#: Foi medido que `TP_IDADE = 2` com `NU_IDADE_N = 0` ocorre em 954 registros da
#: safra de referencia -- contra 5 registros em todo o resto do dominio (-9, -1,
#: 13, 37 e 63 meses). "Zero meses" esta fora da faixa declarada, mas e o unico
#: valor fora dela cuja conversao e comprovadamente correta: 0 meses da 0 anos,
#: que e a mesma faixa etaria de qualquer idade expressa em dias. Anular esses
#: 954 registros tiraria lactentes corretamente classificados da faixa 0-4 sem
#: corrigir erro nenhum -- o oposto do que esta regra existe para fazer. O 0
#: continua sendo uma divergencia do dicionario, declarada aqui.
AGE_UNIT_DOMAIN: dict[int, tuple[int, int]] = {1: (0, 30), 2: (0, 11)}


def age_unit_out_of_domain(values: pd.Series, units: pd.Series) -> pd.Series:
    """Marca idades fora do dominio declarado para a sua propria unidade.

    `TP_IDADE = 2` com 63 meses e um registro internamente contraditorio: 63
    meses nao existem na escala de meses do formulario, que vai ate 11. A
    conversao silenciosa produziria 5,25 anos -- um numero plausivel, e por isso
    mesmo indetectavel depois. Na safra de referencia sao 5 registros (-9, -1,
    13, 37 e 63 meses); ver :data:`AGE_UNIT_DOMAIN` para o tratamento do zero.

    A unidade **nao** e reinterpretada. Nao ha como saber se o erro esta no
    numero ou na unidade, e escolher um dos dois seria inventar dado; a idade e
    anulada e o registro fica localizavel pelo ajuste
    `idade_unidade_implausivel`.

    Args:
        values: coluna `NU_IDADE_N`.
        units: coluna `TP_IDADE`.

    Returns:
        Serie booleana com os registros fora do dominio da unidade.
    """
    amount = pd.to_numeric(values, errors="coerce")
    unit = pd.to_numeric(units, errors="coerce")

    outside = pd.Series(False, index=amount.index)
    for code, (low, high) in AGE_UNIT_DOMAIN.items():
        in_unit = (unit == code).fillna(False)
        outside |= in_unit & amount.notna() & ((amount < low) | (amount > high))
    return outside.fillna(False).astype(bool)


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
        "Converte NU_IDADE_N para anos conforme TP_IDADE (1-dia, 2-mes, 3-ano), "
        "anula a idade quando o numero esta fora do dominio da propria unidade "
        "(1-dia admite 0 a 30; 2-mes admite 1 a 11) registrando o ajuste "
        "`idade_unidade_implausivel` -- sem reinterpretar a unidade, que seria "
        "inventar dado -- "
        f"e anula valores fora de [{MIN_PLAUSIBLE_AGE}, {MAX_PLAUSIBLE_AGE}] "
        "anos, registrando o ajuste `idade_anulada`. O dicionario oficial "
        "valida ate 150 anos; adotamos um teto biologicamente plausivel, e a "
        "divergencia e declarada. Em seguida agrega a idade "
        "em faixa etaria e descarta a idade exata e os campos brutos: a camada "
        "analitica trabalha so com a faixa, por minimizacao de dados -- nenhuma "
        "metrica consome idade exata, e persisti-la sem consumidor formaria um "
        "quase-identificador com UF, sexo e datas."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        frame["idade_anos"] = age_in_years(frame["NU_IDADE_N"], frame["TP_IDADE"])

        # Coerencia entre o numero e a sua unidade, antes da plausibilidade em
        # anos: 63 meses viram 5,25 anos, que passariam pelo teste seguinte.
        unit_conflict = age_unit_out_of_domain(frame["NU_IDADE_N"], frame["TP_IDADE"])
        context.report.age_unit_out_of_domain += int(unit_conflict.sum())
        context.adjustments.add("idade_unidade_implausivel", unit_conflict)
        frame.loc[unit_conflict, "idade_anos"] = pd.NA

        implausible = frame["idade_anos"].notna() & (
            (frame["idade_anos"] < MIN_PLAUSIBLE_AGE) | (frame["idade_anos"] > MAX_PLAUSIBLE_AGE)
        )
        context.report.age_out_of_range += int(implausible.sum())
        context.adjustments.add("idade_anulada", implausible)
        frame.loc[implausible, "idade_anos"] = pd.NA

        frame["faixa_etaria"] = age_band(frame["idade_anos"])

        # A idade exata cumpriu seu papel (derivar a faixa e detectar valores
        # implausiveis). Nao segue para a camada analitica.
        return frame.drop(columns=["idade_anos", "NU_IDADE_N", "TP_IDADE"])


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
