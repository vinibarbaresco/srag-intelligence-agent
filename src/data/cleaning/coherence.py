"""Avaliacao de coerencia entre campos do mesmo registro."""

from __future__ import annotations

import pandas as pd

from src.config import MIN_VALID_DATE
from src.data.cleaning.base import CleaningContext, CleaningRule


def coherence_flags(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Avalia a coerencia do registro, uma flag por dimensao.

    As regras vem das restricoes declaradas no dicionario oficial (por exemplo:
    "data de entrada na UTI deve ser maior ou igual a data dos primeiros
    sintomas") e de impossibilidades logicas diretas.

    A separacao por dimensao e deliberada. Uma data de internacao impossivel
    compromete indicadores de internacao, mas nao a contagem de casos, que
    depende apenas de `DT_SIN_PRI`. Um unico booleano forcaria descartar o
    registro inteiro -- 4.452 registros a mais, na base de referencia -- por um
    defeito que nao afeta a maior parte das metricas.

    Nenhum registro e removido: as flags apenas descrevem o que ha de errado,
    e cada metrica decide o que e relevante para si.

    Args:
        frame: bloco ja com as colunas de data convertidas.

    Returns:
        Mapa `nome da flag -> serie booleana`, nas chaves de `COHERENCE_FLAGS`.
    """
    floor = pd.Timestamp(MIN_VALID_DATE)
    symptoms = frame["DT_SIN_PRI"]
    typed = frame["DT_DIGITA"]
    admission = frame["DT_INTERNA"]
    icu_in, icu_out = frame["DT_ENTUTI"], frame["DT_SAIDUTI"]
    outcome = frame["DT_EVOLUCA"]

    # --- Eixo temporal primario ---------------------------------------------
    invalid_axis = symptoms.isna()
    invalid_axis |= symptoms.notna() & (symptoms < floor)
    invalid_axis |= symptoms.notna() & typed.notna() & (symptoms > typed)

    # --- Internacao -----------------------------------------------------------
    invalid_admission = admission.notna() & symptoms.notna() & (admission < symptoms)
    invalid_admission |= (frame["HOSPITAL"] == 1) & admission.isna()

    # --- UTI ------------------------------------------------------------------
    invalid_icu = icu_in.notna() & symptoms.notna() & (icu_in < symptoms)
    invalid_icu |= icu_in.notna() & icu_out.notna() & (icu_out < icu_in)
    invalid_icu |= (frame["UTI"] == 1) & icu_in.isna()

    # --- Evolucao -------------------------------------------------------------
    invalid_outcome = outcome.notna() & symptoms.notna() & (outcome < symptoms)
    invalid_outcome |= frame["EVOLUCAO"].isin([1, 2, 3]) & outcome.isna()

    return {
        "flag_data_invalida": invalid_axis.fillna(False).astype(bool),
        "flag_internacao_inconsistente": invalid_admission.fillna(False).astype(bool),
        "flag_uti_inconsistente": invalid_icu.fillna(False).astype(bool),
        "flag_evolucao_inconsistente": invalid_outcome.fillna(False).astype(bool),
    }


class EvaluateCoherence(CleaningRule):
    """Marca as inconsistencias entre campos, sem remover nenhum registro."""

    name = "avalia_coerencia"
    description = (
        "Confere a coerencia entre campos do mesmo registro (datas fora de "
        "ordem, campos obrigatorios ausentes dada a resposta declarada) e marca "
        "uma flag por dimensao: eixo temporal, internacao, UTI e evolucao. "
        "Nenhum registro e removido -- cada metrica decide quais flags a "
        "afetam, e apenas o eixo temporal exclui o registro da camada analitica."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        for name, values in coherence_flags(frame).items():
            frame[name] = values
            context.report.coherence_flags[name] += int(values.sum())
        return frame
