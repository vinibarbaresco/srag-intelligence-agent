"""Contrato das regras de limpeza.

Cada regra de tratamento e um objeto nomeado, documentado e testavel em
isolamento, em vez de um trecho dentro de uma funcao longa. A consequencia
pratica: o tratamento de dados pode ser **lido como uma lista de regras** --
pelo revisor, pela documentacao gerada e pelo relatorio de qualidade -- e cada
regra pode ser exercitada por um teste sem executar o pipeline inteiro.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.data.quality import AdjustmentLog, QualityReport


@dataclass
class CleaningContext:
    """Estado compartilhado por todas as regras durante um bloco.

    Attributes:
        year: ano do arquivo de origem.
        report: agregado da carga, alimentado pelas regras.
        adjustments: registro por linha das alteracoes de valor aplicadas.
    """

    year: int
    report: QualityReport
    adjustments: AdjustmentLog
    notes: dict[str, Any] = field(default_factory=dict)


class CleaningRule(ABC):
    """Uma regra de tratamento aplicada a um bloco de registros.

    Implementacoes devem ser puras em relacao ao bloco recebido: recebem um
    `DataFrame`, devolvem o `DataFrame` transformado e registram no contexto o
    que fizeram. Nao devem ler nem escrever em disco.
    """

    #: Identificador curto e estavel, usado na documentacao e no relatorio.
    name: str

    #: O que a regra faz e por que, em uma frase. Aparece na documentacao
    #: gerada e no relatorio de qualidade -- e, portanto, e parte da entrega.
    description: str

    @abstractmethod
    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        """Aplica a regra ao bloco e devolve o resultado."""

    def describe(self) -> dict[str, str]:
        """Descricao auditavel da regra."""
        return {"name": self.name, "description": self.description}

    def __repr__(self) -> str:  # pragma: no cover - conveniencia de depuracao
        return f"{type(self).__name__}(name={self.name!r})"
