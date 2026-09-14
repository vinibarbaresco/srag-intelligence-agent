"""Pipeline de tratamento: a ordem das regras, declarada.

A sequencia abaixo **e** a especificacao do tratamento de dados. Ela e lida pela
documentacao gerada e pelo relatorio de qualidade, de modo que o que se publica
sobre o tratamento venha da mesma fonte que o executa.

A ordem nao e arbitraria:

1. datas primeiro, porque as regras de coerencia comparam datas;
2. codigos e texto em seguida, porque a semantica depende deles;
3. derivacoes demograficas, que consomem os numericos ja normalizados;
4. semana epidemiologica, que depende de `DT_SIN_PRI` ja convertida e de
   `SEM_PRI` ainda bruta, para reconcilia-las;
5. coerencia, que precisa de todas as datas e codigos prontos;
6. semantica por ultimo, porque `estadia_uti_utilizavel` depende da flag de
   coerencia de UTI.
"""

from __future__ import annotations

from typing import Final

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.cleaning.codes import (
    NormalizeCategoricalCodes,
    NormalizeNumericColumns,
    NormalizeSex,
    NormalizeStateCodes,
)
from src.data.cleaning.coherence import EvaluateCoherence
from src.data.cleaning.dates import ParseDateColumns
from src.data.cleaning.demographics import DeriveAge, TagSourceYear
from src.data.cleaning.derived import DeriveSemanticFlags
from src.data.cleaning.epiweek import DeriveEpidemiologicalWeek
from src.data.quality import AdjustmentLog, QualityReport
from src.data.schema import ADJUSTMENT_COLUMN, assert_no_denied_columns

#: Regras de tratamento, na ordem em que sao aplicadas.
CLEANING_PIPELINE: Final[tuple[CleaningRule, ...]] = (
    ParseDateColumns(),
    NormalizeCategoricalCodes(),
    NormalizeSex(),
    NormalizeNumericColumns(),
    NormalizeStateCodes(),
    DeriveAge(),
    TagSourceYear(),
    DeriveEpidemiologicalWeek(),
    EvaluateCoherence(),
    DeriveSemanticFlags(),
)


def describe_pipeline() -> list[dict[str, str]]:
    """Descreve o pipeline para a documentacao e o relatorio de qualidade."""
    return [
        {"ordem": str(position), **rule.describe()}
        for position, rule in enumerate(CLEANING_PIPELINE, start=1)
    ]


def clean_chunk(
    chunk: pd.DataFrame,
    year: int,
    report: QualityReport,
    rules: tuple[CleaningRule, ...] = CLEANING_PIPELINE,
) -> pd.DataFrame:
    """Aplica o pipeline de tratamento a um bloco de registros.

    Args:
        chunk: bloco lido do arquivo bruto, ja restrito as colunas permitidas.
        year: ano do arquivo de origem.
        report: acumulador de qualidade da carga, alimentado pelas regras.
        rules: pipeline a aplicar; o padrao e :data:`CLEANING_PIPELINE`. Um
            subconjunto pode ser passado para exercitar uma regra isolada.

    Returns:
        Bloco tratado, com as colunas derivadas, as flags de coerencia e a
        coluna de ajustes aplicados.

    Raises:
        ValueError: se alguma coluna com dado pessoal alcancar a saida.
    """
    report.rows_read += len(chunk)
    frame = chunk.copy()

    context = CleaningContext(year=year, report=report, adjustments=AdjustmentLog(frame.index))

    for rule in rules:
        frame = rule.apply(frame, context)

    # O registro por linha das alteracoes e escrito ao final, quando todas as
    # regras ja declararam o que fizeram.
    frame[ADJUSTMENT_COLUMN] = context.adjustments.to_series()
    report.absorb(context.adjustments)
    report.rows_written += len(frame)

    assert_no_denied_columns(list(frame.columns))
    return frame
