"""Derivacoes semanticas: o que cada codigo do dicionario significa.

Estas colunas traduzem os codigos do SIVEP-Gripe para conceitos epidemiologicos
-- "obito por SRAG", "caso encerrado", "admissao em UTI". Ficam aqui, ao lado de
`CODE_LABELS`, e nao no SQL da view, por dois motivos:

1. **Coesao.** A semantica dos codigos vem do dicionario oficial de dados, que
   ja e declarado em Python. Manter a traducao em outra linguagem, em outro
   arquivo, permitiria que uma mudanca no dicionario nao se refletisse no
   calculo -- sem que nada falhasse.
2. **Testabilidade.** Uma definicao em SQL so pode ser exercitada com um banco
   montado. Em Python, cada uma e verificavel num teste de unidade.

A view analitica continua existindo, mas faz apenas projecao de tipo (CAST de
timestamp para data e truncamento por mes), nao semantica.
"""

from __future__ import annotations

import pandas as pd

from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import DERIVED_SEMANTIC_COLUMNS


def _is(series: pd.Series, *codes: int) -> pd.Series:
    """Booleano estrito: nulo e codigo de ausencia nunca viram `True`."""
    return series.isin(codes).fillna(False).astype(bool)


class DeriveSemanticFlags(CleaningRule):
    """Traduz os codigos do dicionario em conceitos epidemiologicos."""

    name = "deriva_semantica"
    description = (
        "Traduz os codigos do dicionario oficial em conceitos usados pelas "
        "metricas (obito por SRAG, caso encerrado, hospitalizacao, admissao em "
        "UTI, vacinacao declarada). Uma unica definicao por conceito, "
        "compartilhada por indicadores, series e graficos, de modo que nao "
        "existam duas nocoes de 'caso encerrado' no projeto."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        # --- Desfecho (campo 82: 1-Cura, 2-Obito, 3-Obito outras causas) -----
        frame["eh_obito_srag"] = _is(frame["EVOLUCAO"], 2)
        frame["caso_encerrado"] = _is(frame["EVOLUCAO"], 1, 2, 3)

        # --- Assistencia (campos 48 e 53: 1-Sim, 2-Nao, 9-Ignorado) ----------
        frame["foi_hospitalizado"] = _is(frame["HOSPITAL"], 1)
        frame["teve_admissao_uti"] = _is(frame["UTI"], 1)
        frame["uti_informado"] = _is(frame["UTI"], 1, 2)

        # Estadia utilizavel no censo diario: exige admissao declarada, data de
        # entrada e ausencia de inconsistencia na dimensao UTI. Depende da regra
        # de coerencia, por isso esta regra roda depois dela no pipeline.
        frame["estadia_uti_utilizavel"] = (
            frame["teve_admissao_uti"]
            & frame["DT_ENTUTI"].notna()
            & ~frame["flag_uti_inconsistente"]
        )

        # --- Vacinacao (campo 36 e VACINA: 1-Sim, 2-Nao, 9-Ignorado) ---------
        frame["vacinado_covid"] = _is(frame["VACINA_COV"], 1)
        frame["vacina_covid_informada"] = _is(frame["VACINA_COV"], 1, 2)
        frame["vacinado_influenza"] = _is(frame["VACINA"], 1)
        frame["vacina_influenza_informada"] = _is(frame["VACINA"], 1, 2)

        missing = sorted(set(DERIVED_SEMANTIC_COLUMNS) - set(frame.columns))
        if missing:  # pragma: no cover - erro de programacao, nao de dado
            raise ValueError(
                f"Regra de derivacao semantica nao produziu as colunas: {missing}"
            )

        return frame
