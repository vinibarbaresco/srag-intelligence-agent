"""Regra de normalizacao das colunas de data."""

from __future__ import annotations

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import DATE_COLUMNS, VACCINE_DATE_COLUMNS


def parse_dates(series: pd.Series) -> pd.Series:
    """Converte uma coluna de datas aceitando os tres formatos ja publicados.

    O DATASUS ja distribuiu o mesmo campo de tres maneiras, conforme a safra do
    arquivo:

    * ISO-8601 com sufixo `Z` -- `2026-04-30T00:00:00.000Z` (publicacoes atuais);
    * ISO-8601 simples -- `2024-12-29` (ex.: INFLUD25 versao 26-06-2025);
    * formato brasileiro -- `30/04/2026` (safras antigas).

    O parse tenta ISO primeiro, que cobre os dois primeiros, e recorre ao
    formato brasileiro apenas nos valores restantes -- evitando que `03/04/2026`
    seja interpretado como 3 de abril ou 4 de marco conforme o acaso.

    Args:
        series: coluna bruta, como lida do CSV.

    Returns:
        Coluna de `datetime64`, com `NaT` onde o valor nao pode ser interpretado.
    """
    text = series.astype("string").str.strip().replace({"": pd.NA})

    parsed = pd.to_datetime(text, format="ISO8601", utc=True, errors="coerce")
    parsed = parsed.dt.tz_localize(None)

    pending = parsed.isna() & text.notna()
    if pending.any():
        fallback = pd.to_datetime(text[pending], format="%d/%m/%Y", errors="coerce")
        parsed.loc[pending] = fallback

    return parsed


class ParseDateColumns(CleaningRule):
    """Converte todas as colunas de data para `datetime`."""

    name = "parse_datas"
    description = (
        "Converte as colunas de data dos tres formatos ja publicados pela fonte "
        "(ISO-8601 com e sem sufixo Z, e dd/mm/aaaa como fallback). Um valor "
        "presente no arquivo mas ilegivel vira nulo e e registrado como ajuste "
        "`data_ilegivel`, para nao se confundir com um campo vazio na origem."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        for column in DATE_COLUMNS + VACCINE_DATE_COLUMNS:
            if column not in frame.columns:
                continue

            present_before = (
                frame[column].astype("string").str.strip().replace({"": pd.NA}).notna()
            )
            frame[column] = parse_dates(frame[column])

            unreadable = present_before & frame[column].isna()
            context.report.invalid_dates[column] += int(unreadable.sum())
            context.adjustments.add(f"data_ilegivel:{column}", unreadable)
            context.report.null_counts[column] += int(frame[column].isna().sum())

        return frame
