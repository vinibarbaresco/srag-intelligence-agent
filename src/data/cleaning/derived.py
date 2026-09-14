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


#: Grupo etiologico por codigo de CLASSI_FIN (campo 80).
#:
#: O vazio **nao** e mapeado aqui de proposito: ele vira `nao_encerrado`, e nunca
#: `nao_especificado`. Sao conceitos distintos -- "o laboratorio nao identificou
#: o agente" e "o caso ainda nao foi encerrado" -- e confundi-los transferiria
#: 22.748 registros da safra de referencia (13,75%) para uma categoria
#: etiologica que eles nao tem.
_ETIOLOGIC_GROUPS: dict[int, str] = {
    1: "influenza",
    2: "outro_virus_respiratorio",
    3: "outro_agente",
    4: "nao_especificado",
    5: "covid_19",
}

#: Desfecho por codigo de EVOLUCAO (campo 82).
#:
#: Pelo mesmo motivo, o vazio vira `em_aberto` e nao `cura`: um caso sem
#: desfecho registrado nao e um caso que sobreviveu.
_CASE_STATUS: dict[int, str] = {
    1: "cura",
    2: "obito_srag",
    3: "obito_outras",
    9: "desfecho_ignorado",
}


def _label(series: pd.Series, mapping: dict[int, str], *, absent: str) -> pd.Series:
    """Traduz codigos em rotulos, separando ausencia de codigo desconhecido.

    Args:
        series: coluna de codigos ja normalizada para inteiro nulavel.
        mapping: dominio declarado no dicionario oficial.
        absent: rotulo dos registros sem codigo -- deliberadamente distinto de
            qualquer rotulo do dominio.

    Returns:
        Coluna textual, com `fora_do_dominio` onde ha codigo que o dicionario
        nao preve. Nenhum codigo desconhecido e silenciosamente agrupado com um
        conhecido.
    """
    labels = series.map(mapping).astype("string")
    labels = labels.mask(series.notna() & labels.isna(), "fora_do_dominio")
    return labels.fillna(absent)


class DeriveSemanticFlags(CleaningRule):
    """Traduz os codigos do dicionario em conceitos epidemiologicos."""

    name = "deriva_semantica"
    description = (
        "Traduz os codigos do dicionario oficial em conceitos usados pelas "
        "metricas (obito por SRAG, caso encerrado, hospitalizacao, admissao em "
        "UTI, suporte ventilatorio, infeccao nosocomial, grupo etiologico e "
        "vacinacao declarada). Uma unica definicao por conceito, "
        "compartilhada por indicadores, series e graficos, de modo que nao "
        "existam duas nocoes de 'caso encerrado' no projeto."
    )

    def apply(self, frame: pd.DataFrame, context: CleaningContext) -> pd.DataFrame:
        # --- Desfecho (campo 82: 1-Cura, 2-Obito, 3-Obito outras causas) -----
        frame["eh_obito_srag"] = _is(frame["EVOLUCAO"], 2)
        frame["caso_encerrado"] = _is(frame["EVOLUCAO"], 1, 2, 3)

        frame["status_caso"] = _label(frame["EVOLUCAO"], _CASE_STATUS, absent="em_aberto")

        # --- Assistencia (campos 48 e 53: 1-Sim, 2-Nao, 9-Ignorado) ----------
        frame["foi_hospitalizado"] = _is(frame["HOSPITAL"], 1)
        frame["hospitalizacao_informada"] = _is(frame["HOSPITAL"], 1, 2)
        frame["teve_admissao_uti"] = _is(frame["UTI"], 1)
        frame["uti_informado"] = _is(frame["UTI"], 1, 2)

        # --- Suporte ventilatorio (campo 56) ---------------------------------
        # ATENCAO: este campo NAO e Sim/Nao/Ignorado. O dominio oficial e
        # 1=Sim invasivo, 2=Sim NAO invasivo, 3=Nao, 9=Ignorado. O "sim" e
        # {1, 2}; ler o 2 como "nao" inverteria 75.120 registros da safra de
        # referencia -- a maioria dos ventilados. O "informado" inclui o 3,
        # porque "nao ventilou" tambem e informacao.
        frame["foi_ventilado"] = _is(frame["SUPORT_VEN"], 1, 2)
        frame["ventilacao_invasiva"] = _is(frame["SUPORT_VEN"], 1)
        frame["ventilacao_nao_invasiva"] = _is(frame["SUPORT_VEN"], 2)
        frame["ventilacao_informada"] = _is(frame["SUPORT_VEN"], 1, 2, 3)

        # --- Origem da infeccao (campo 30) -----------------------------------
        # Caso nosocomial torna legitimo DT_INTERNA < DT_SIN_PRI: a infeccao foi
        # adquirida no hospital, entao os sintomas comecam depois da internacao.
        frame["caso_nosocomial"] = _is(frame["NOSOCOMIAL"], 1)

        # --- Encerramento (campos 80 e 81) -----------------------------------
        frame["etiologia_laboratorial"] = _is(frame["CRITERIO"], 1)
        frame["etiologia_criterio_informado"] = _is(frame["CRITERIO"], 1, 2, 3, 4)
        frame["grupo_etiologico"] = _label(
            frame["CLASSI_FIN"], _ETIOLOGIC_GROUPS, absent="nao_encerrado"
        )

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
            raise ValueError(f"Regra de derivacao semantica nao produziu as colunas: {missing}")

        return frame
