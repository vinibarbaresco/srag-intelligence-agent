"""Derivacao da semana epidemiologica a partir de `DT_SIN_PRI`.

A semana epidemiologica adotada pelo Ministerio da Saude **nao e a semana ISO**:
ela comeca no **domingo** e termina no sabado, e a SE 1 de um ano e a primeira
semana iniciada em domingo que tem ao menos 4 dias no ano novo. A diferenca nao
e cosmetica: na virada de ano as duas convencoes atribuem semanas -- e ate anos
-- diferentes ao mesmo dia.

Por que derivar em vez de usar `SEM_PRI`, que a fonte ja publica:

1. **Uma unica definicao.** A serie precisa de uma regra so, aplicada a todo
   registro, inclusive aos que chegam sem `SEM_PRI`.
2. **Reconciliacao em vez de correcao.** `SEM_PRI` continua sendo lida e
   comparada com a derivada; a divergencia e **contada** no relatorio de
   qualidade. A derivada manda no calculo, e em nenhuma hipotese uma preenche a
   outra por coalesce: misturar dois criterios dentro da mesma serie produziria
   um agregado que nao corresponde a nenhum dos dois.
"""

from __future__ import annotations

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import EPIWEEK_COLUMNS, EPIWEEK_DERIVED_COLUMNS

#: Coluna de semana epidemiologica publicada pela fonte, usada so para conferir.
_SOURCE_EPIWEEK = EPIWEEK_COLUMNS[0]


def _sunday_on_or_before(dates: pd.Series) -> pd.Series:
    """Inicio (domingo) da semana epidemiologica que contem cada data."""
    # `dayofweek` e 0 na segunda e 6 no domingo; a distancia ate o domingo
    # anterior e, portanto, (dayofweek + 1) % 7.
    offset = (dates.dt.dayofweek + 1) % 7
    return dates - pd.to_timedelta(offset, unit="D")


def epidemiological_week(dates: pd.Series) -> pd.DataFrame:
    """Calcula ano e numero da semana epidemiologica de cada data.

    A regra dos "ao menos 4 dias no ano novo" e equivalente a uma formulacao
    mais direta, e e esta que o codigo usa: **a SE 1 e a semana que contem o dia
    4 de janeiro**. A equivalencia e exata -- a semana iniciada no domingo `S`
    tem `7 - (1 de janeiro - S)` dias no ano novo, e esse valor e maior ou igual
    a 4 exatamente quando `S` cai em 29 de dezembro ou depois, que e tambem a
    condicao para a semana conter o dia 4. Escrita assim, a regra lida sozinha
    com os anos de 53 semanas, sem enumeracao de casos.

    Args:
        dates: coluna `datetime64` (tipicamente `DT_SIN_PRI`).

    Returns:
        DataFrame com `semana_epi_ano` (Int16) e `semana_epi_num` (Int8), nulos
        onde a data e nula.
    """
    week_start = _sunday_on_or_before(dates)

    # A semana pertence ao ano em que cai a sua quarta-feira: e o dia que so
    # existe do lado majoritario da virada, pela mesma aritmetica dos 4 dias.
    epi_year = (week_start + pd.Timedelta(days=3)).dt.year

    known = epi_year.notna()
    january_fourth = pd.Series(pd.NaT, index=dates.index, dtype="datetime64[ns]")
    if known.any():
        january_fourth.loc[known] = pd.to_datetime(
            {
                "year": epi_year[known].astype("int64"),
                "month": 1,
                "day": 4,
            }
        )
    first_week_start = _sunday_on_or_before(january_fourth)

    week_number = (week_start - first_week_start).dt.days // 7 + 1

    return pd.DataFrame(
        {
            "semana_epi_ano": epi_year.astype("Int16"),
            "semana_epi_num": week_number.astype("Int16").astype("Int8"),
        },
        index=dates.index,
    )


class DeriveEpidemiologicalWeek(CleaningRule):
    """Deriva a semana epidemiologica dos primeiros sintomas."""

    name = "deriva_semana_epidemiologica"
    description = (
        "Deriva de DT_SIN_PRI a semana epidemiologica pela regra do Ministerio "
        "da Saude -- semana iniciada no domingo, SE 1 sendo a primeira com ao "
        "menos 4 dias no ano novo -- e publica `semana_epi_ano`, "
        "`semana_epi_num`, o rotulo ordenavel `semana_epi`, o ano civil dos "
        "sintomas e o mes `AAAA-MM`. A semana ISO nao serve: na virada do ano "
        "ela atribui outro numero, e as vezes outro ano, ao mesmo dia. SEM_PRI, "
        "publicada pela fonte, e lida apenas para reconciliacao: a divergencia "
        "e contada no relatorio de qualidade e nenhuma das duas colunas corrige "
        "ou preenche a outra -- misturar dois criterios na mesma serie "
        "produziria um agregado que nao corresponde a nenhum deles."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        symptoms = frame["DT_SIN_PRI"]
        weeks = epidemiological_week(symptoms)

        frame["semana_epi_ano"] = weeks["semana_epi_ano"]
        frame["semana_epi_num"] = weeks["semana_epi_num"]
        # Rotulo "AAAA-SS" com a semana em duas casas, para que a ordenacao
        # lexicografica coincida com a cronologica.
        frame["semana_epi"] = (
            weeks["semana_epi_ano"].astype("string")
            + "-"
            + weeks["semana_epi_num"].astype("string").str.zfill(2)
        ).astype("string")
        frame["ano_sintomas"] = symptoms.dt.year.astype("Int16")
        frame["mes_sintomas"] = symptoms.dt.strftime("%Y-%m").astype("string")

        self._reconcile(frame, context)

        missing = sorted(set(EPIWEEK_DERIVED_COLUMNS) - set(frame.columns))
        if missing:  # pragma: no cover - erro de programacao, nao de dado
            raise ValueError(f"Regra de semana epidemiologica nao produziu: {missing}")

        return frame

    @staticmethod
    def _reconcile(frame: pd.DataFrame, context: CleaningContext) -> None:
        """Compara a semana derivada com a publicada, sem alterar nenhuma das duas."""
        if _SOURCE_EPIWEEK not in frame.columns:
            return

        published = pd.to_numeric(frame[_SOURCE_EPIWEEK], errors="coerce")
        derived = frame["semana_epi_num"]

        # Safras diferentes publicam SEM_PRI com granularidades diferentes (o
        # numero da semana, ou "AAAASS"). So o que cabe num numero de semana e
        # comparavel; o resto nao e divergencia, e outro formato, e contar como
        # divergencia produziria 100% de erro aparente.
        comparable = published.notna() & derived.notna() & published.between(1, 53)
        context.report.epiweek_compared += int(comparable.sum())
        context.report.epiweek_mismatch += int((comparable & (published != derived)).sum())
