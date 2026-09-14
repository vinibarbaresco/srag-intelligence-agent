"""Regras de normalizacao de codigos categoricos, texto e numeros."""

from __future__ import annotations

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import (
    CATEGORICAL_COLUMNS,
    CODE_LABELS,
    GEOGRAPHIC_COLUMNS,
    MISSING_CODES,
    NUMERIC_COLUMNS,
    UF_CODES,
)

#: Coluna categorica textual, tratada a parte das numericas.
_SEX_COLUMN = "CS_SEXO"


def _normalize_text(series: pd.Series) -> pd.Series:
    """Normaliza texto e trata string vazia como ausencia.

    A distincao importa: `""` e ausencia na origem, nao um valor invalido. Sem
    esta normalizacao, um campo vazio seria contado como fora do dominio e
    marcado como ajuste -- atribuindo ao pipeline uma alteracao que ele nao fez.
    """
    return series.astype("string").str.strip().str.upper().replace({"": pd.NA})


class NormalizeCategoricalCodes(CleaningRule):
    """Converte os codigos categoricos numericos para inteiro nulavel."""

    name = "normaliza_codigos_categoricos"
    description = (
        "Converte os campos categoricos do SIVEP-Gripe para inteiro nulavel, "
        f"contabiliza os codigos de ausencia {sorted(MISSING_CODES)} (Ignorado) e "
        "conta os codigos fora do dominio declarado no dicionario oficial. Nada e "
        "anulado nem convertido: o codigo e preservado como esta, e quem o exclui "
        "e a camada de metricas, ao montar numerador e denominador. Um codigo "
        "fora do dominio fica visivel no relatorio de qualidade em vez de se "
        "confundir com um caso em aberto."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        for column in CATEGORICAL_COLUMNS:
            if column not in frame.columns or column == _SEX_COLUMN:
                continue

            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int16")
            context.report.null_counts[column] += int(frame[column].isna().sum())
            for code in MISSING_CODES:
                context.report.ignored_code_counts[column] += int(
                    (frame[column] == code).sum()
                )

            domain = CODE_LABELS.get(column)
            if domain:
                outside = frame[column].notna() & ~frame[column].isin(list(domain))
                context.report.out_of_domain_counts[column] += int(outside.sum())

        return frame


class NormalizeSex(CleaningRule):
    """Normaliza o campo textual de sexo."""

    name = "normaliza_sexo"
    description = (
        "Normaliza CS_SEXO para caixa alta sem espacos (dominio M/F/I). String "
        "vazia e tratada como ausencia, nao como valor."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        if _SEX_COLUMN in frame.columns:
            frame[_SEX_COLUMN] = _normalize_text(frame[_SEX_COLUMN])
            context.report.null_counts[_SEX_COLUMN] += int(frame[_SEX_COLUMN].isna().sum())
        return frame


class NormalizeNumericColumns(CleaningRule):
    """Converte as colunas numericas para inteiro nulavel."""

    name = "normaliza_numericos"
    description = (
        "Converte a idade bruta (NU_IDADE_N) para inteiro nulavel. Valores nao "
        "numericos viram nulo em vez de interromper a carga."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        for column in NUMERIC_COLUMNS:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int32")
        return frame


class NormalizeStateCodes(CleaningRule):
    """Valida as siglas de UF contra o dominio oficial."""

    name = "valida_uf"
    description = (
        "Normaliza as siglas de UF e anula as que estao fora das 27 unidades "
        "federativas. A alteracao e registrada por registro como "
        "`uf_anulada:<coluna>`, de modo que um valor anulado pela limpeza nao "
        "se confunda com um campo vazio na origem."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        for column in GEOGRAPHIC_COLUMNS:
            if column not in frame.columns:
                continue

            frame[column] = _normalize_text(frame[column])
            outside = frame[column].notna() & ~frame[column].isin(UF_CODES)

            context.report.unknown_uf += int(outside.sum())
            context.adjustments.add(f"uf_anulada:{column}", outside)
            frame.loc[outside, column] = pd.NA

        return frame
