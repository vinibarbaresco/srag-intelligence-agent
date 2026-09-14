"""Avaliacao de coerencia entre campos do mesmo registro."""

from __future__ import annotations

import pandas as pd

from src.config import MIN_VALID_DATE
from src.data.cleaning.base import CleaningContext, CleaningRule
from src.data.schema import DATE_COLUMNS, VACCINE_DATE_COLUMNS


def implausible_dates(frame: pd.DataFrame, ceiling: pd.Timestamp | None = None) -> pd.Series:
    """Marca registros com alguma data fora do intervalo fisicamente possivel.

    As regras de ordem entre datas nao capturam esta classe de erro: um registro
    cujo ano foi digitado como 5202 mantem a ordem relativa correta em todos os
    pares e passa por todas elas. Foram medidos na fonte `DT_INTERNA` minima de
    1695-01-17 e maxima de 2202-06-07, `DT_ENTUTI` ate 2028-05-07, e os anos
    5202 e 8202 nos arquivos de `data/raw/`.

    O teto e a **data de execucao**, nao `DT_DIGITA`. Usar `DT_DIGITA` como teto
    das datas de desfecho foi medido e descartado: `DT_EVOLUCA > DT_DIGITA`
    ocorre em 52,88% dos pares porque `DT_DIGITA` e a digitacao inicial e nao a
    ultima atualizacao do registro. Aplicar essa regra marcaria 36,66% da base
    como invalida sem que houvesse erro nenhum.

    Args:
        frame: bloco ja com as colunas de data convertidas.
        ceiling: teto superior; padrao, a data de execucao da carga.

    Returns:
        Serie booleana, verdadeira onde ao menos uma data e implausivel.
    """
    floor = pd.Timestamp(MIN_VALID_DATE)
    ceiling = ceiling or pd.Timestamp.today().normalize()

    implausible = pd.Series(False, index=frame.index)
    for column in DATE_COLUMNS + VACCINE_DATE_COLUMNS:
        if column not in frame.columns:
            continue
        values = frame[column]
        implausible |= values.notna() & ((values < floor) | (values > ceiling))
    return implausible.fillna(False).astype(bool)


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
    # `NOSOCOMIAL = 1` (infeccao adquirida no hospital) torna DT_INTERNA anterior
    # a DT_SIN_PRI **esperado**, nao incoerente: o paciente ja estava internado
    # quando os sintomas comecaram. Dos 1.365 registros que a regra anterior
    # marcava na safra de referencia, 1.363 eram exatamente isso; sobram 2, com
    # NOSOCOMIAL = 2, que continuam marcados.
    #
    # O teste e feito sobre o codigo bruto, e nao sobre `caso_nosocomial`, de
    # proposito. A derivacao semantica roda **depois** desta regra, porque
    # `estadia_uti_utilizavel` depende de `flag_uti_inconsistente`; antecipa-la
    # exigiria quebrar a derivacao em duas etapas. Reconstruir aqui um unico
    # predicado de uma linha e mais simples do que reordenar o pipeline, e a
    # definicao continua unica -- `derived.py` usa o mesmo codigo 1, e o teste
    # `test_nosocomial_nao_flaga_internacao` cobre as duas pontas.
    nosocomial = (frame["NOSOCOMIAL"] == 1).fillna(False)

    invalid_admission = admission.notna() & symptoms.notna() & (admission < symptoms) & ~nosocomial
    invalid_admission |= (frame["HOSPITAL"] == 1) & admission.isna()

    # --- UTI ------------------------------------------------------------------
    invalid_icu = icu_in.notna() & symptoms.notna() & (icu_in < symptoms)
    invalid_icu |= icu_in.notna() & icu_out.notna() & (icu_out < icu_in)
    invalid_icu |= (frame["UTI"] == 1) & icu_in.isna()
    # Saida de UTI sem entrada: a estadia e incalculavel e a admissao, nao
    # comprovada. Sao 25 registros na safra de referencia -- todos com UTI = 1, e
    # portanto ja alcancados pela clausula anterior. A regra fica assim mesmo:
    # ela nao depende de `UTI` estar preenchido, e a coincidencia de hoje e uma
    # propriedade desta safra, nao da fonte. O custo e uma comparacao.
    invalid_icu |= icu_out.notna() & icu_in.isna()

    # --- Evolucao -------------------------------------------------------------
    invalid_outcome = outcome.notna() & symptoms.notna() & (outcome < symptoms)
    invalid_outcome |= frame["EVOLUCAO"].isin([1, 2, 3]) & outcome.isna()

    return {
        "flag_data_invalida": invalid_axis.fillna(False).astype(bool),
        "flag_internacao_inconsistente": invalid_admission.fillna(False).astype(bool),
        "flag_uti_inconsistente": invalid_icu.fillna(False).astype(bool),
        "flag_evolucao_inconsistente": invalid_outcome.fillna(False).astype(bool),
        "flag_data_implausivel": implausible_dates(frame),
    }


class EvaluateCoherence(CleaningRule):
    """Marca as inconsistencias entre campos, sem remover nenhum registro."""

    name = "avalia_coerencia"
    description = (
        "Confere a coerencia entre campos do mesmo registro (datas fora de "
        "ordem, datas fora do intervalo fisicamente possivel, campos "
        "obrigatorios ausentes dada a resposta declarada) e marca uma flag por "
        "dimensao: eixo temporal, internacao, UTI, evolucao e plausibilidade "
        "das datas. Casos nosocomiais nao sao marcados por internacao anterior "
        "aos sintomas, porque no dicionario isso e o esperado. "
        "Nenhum registro e removido -- cada metrica decide quais flags a "
        "afetam, e apenas o eixo temporal exclui o registro da camada analitica."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        for name, values in coherence_flags(frame).items():
            frame[name] = values
            context.report.coherence_flags[name] += int(values.sum())
        return frame
