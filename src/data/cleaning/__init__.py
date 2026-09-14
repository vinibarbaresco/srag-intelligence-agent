"""Tratamento dos dados brutos do SIVEP-Gripe.

O tratamento e um **pipeline declarado de regras nomeadas**, e nao um
procedimento. Cada regra e uma classe com nome, descricao e teste proprio; a
ordem de aplicacao esta declarada em :data:`CLEANING_PIPELINE`.

A consequencia pratica e de governanca: o tratamento de dados pode ser lido como
uma lista de regras -- pelo revisor, pela documentacao gerada e pelo relatorio de
qualidade -- em vez de exigir a leitura de uma funcao longa. E a documentacao
publicada sobre o tratamento e gerada da mesma estrutura que o executa, o que
impede que as duas divirjam.

Uso tipico::

    from src.data.cleaning import clean_chunk
    from src.data.quality import QualityReport

    report = QualityReport()
    tratado = clean_chunk(bloco_bruto, year=2026, report=report)
"""

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.cleaning.pipeline import CLEANING_PIPELINE, clean_chunk, describe_pipeline

__all__ = [
    "CLEANING_PIPELINE",
    "CleaningContext",
    "CleaningRule",
    "clean_chunk",
    "describe_pipeline",
]
