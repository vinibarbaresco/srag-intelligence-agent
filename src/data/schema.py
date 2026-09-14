"""Contrato de colunas entre o CSV bruto do DATASUS e a camada analitica.

Este modulo e a fronteira de minimizacao de dados do projeto. O arquivo bruto do
SIVEP-Gripe possui 194 colunas; apenas as listadas em :data:`ALLOWED_COLUMNS`
sao lidas do disco. As colunas de :data:`DENIED_COLUMNS` sao enumeradas
explicitamente -- ainda que ja fossem excluidas por omissao -- para que a decisao
de nao processa-las fique auditavel e para que o teste de regressao possa falhar
caso alguem as adicione no futuro.

Os dominios das variaveis categoricas foram lidos no dicionario oficial de dados
(`dicionario-de-dados-2019-a-2025.pdf`), nao inferidos pelo nome do campo.
"""

from __future__ import annotations

from typing import Final

# =============================================================================
# Colunas lidas do arquivo bruto
# =============================================================================

DATE_COLUMNS: Final[tuple[str, ...]] = (
    "DT_SIN_PRI",  # 2  - data dos primeiros sintomas (eixo epidemiologico)
    "DT_INTERNA",  # 49 - data da internacao
    "DT_ENTUTI",  # 54 - data de entrada na UTI
    "DT_SAIDUTI",  # 55 - data de saida da UTI
    "DT_EVOLUCA",  # 83 - data da alta ou do obito
    "DT_ENCERRA",  # 84 - data de encerramento do caso
    "DT_DIGITA",  # -  - data de digitacao (base do corte por atraso)
)

#: Semana epidemiologica publicada pela fonte.
#:
#: Lida para **reconciliacao**, nao para calculo: a semana usada pelas series e
#: derivada de `DT_SIN_PRI` (ver `src/data/cleaning/epiweek.py`), e a divergencia
#: contra este campo e contabilizada no relatorio de qualidade. Nunca ha
#: coalesce entre as duas -- um campo publicado com outro criterio nao pode
#: preencher buracos do criterio adotado aqui sem tornar a serie heterogenea.
EPIWEEK_COLUMNS: Final[tuple[str, ...]] = ("SEM_PRI",)  # 3 - semana epi dos sintomas

CATEGORICAL_COLUMNS: Final[tuple[str, ...]] = (
    "CS_SEXO",  # 10 - sexo
    "TP_IDADE",  # 14 - unidade da idade (1-dia, 2-mes, 3-ano)
    "HOSPITAL",  # 48 - houve internacao
    "UTI",  # 53 - internado em UTI (admissao, NAO ocupacao de leito)
    "SUPORT_VEN",  # 56 - suporte ventilatorio (1-invasivo, 2-nao invasivo, 3-nao)
    "NOSOCOMIAL",  # 30 - infeccao adquirida no ambiente hospitalar
    "CLASSI_FIN",  # 80 - classificacao final do caso
    "CRITERIO",  # 81 - criterio de encerramento do caso
    "EVOLUCAO",  # 82 - evolucao do caso
    "VACINA",  # 40 - vacina contra gripe na ultima campanha
    "VACINA_COV",  # 36 - recebeu vacina COVID-19
)

GEOGRAPHIC_COLUMNS: Final[tuple[str, ...]] = (
    "SG_UF_NOT",  # 6  - UF de notificacao (recorte geografico das metricas)
    "SG_UF",  # 12 - UF de residencia (exposta na view, sem metrica que a recorte)
)

NUMERIC_COLUMNS: Final[tuple[str, ...]] = (
    "NU_IDADE_N",  # 13 - idade na unidade indicada por TP_IDADE
)

#: Colunas de data de dose vacinal. Vazia por decisao de minimizacao: a
#: cobertura vacinal usa o indicador de vacinacao (`VACINA`, `VACINA_COV`), nao
#: as datas -- que sao quase-identificadores e nao entram em nenhuma metrica.
VACCINE_DATE_COLUMNS: Final[tuple[str, ...]] = ()

ALLOWED_COLUMNS: Final[tuple[str, ...]] = (
    DATE_COLUMNS
    + EPIWEEK_COLUMNS
    + CATEGORICAL_COLUMNS
    + GEOGRAPHIC_COLUMNS
    + NUMERIC_COLUMNS
    + VACCINE_DATE_COLUMNS
)

#: Colunas avaliadas no dicionario e **deliberadamente nao selecionadas**.
#:
#: Minimizacao nao e so excluir dado obviamente identificavel: e nao ler o que
#: nenhuma metrica consome. Estas colunas foram consideradas, tem domínio
#: conhecido no dicionario oficial, e ficaram de fora porque nenhum indicador,
#: regra de coerencia ou serie depende delas. Enumeradas aqui para que a decisao
#: de exclusao fique auditavel -- e reversivel com justificativa, caso uma
#: metrica futura precise de alguma.
NOT_SELECTED_COLUMNS: Final[dict[str, str]] = {
    "DT_NOTIFIC": "data de notificacao; o eixo temporal e DT_SIN_PRI e o corte e DT_DIGITA",
    "SEM_NOT": (
        "semana epidemiologica da notificacao; a semana usada e a dos primeiros "
        "sintomas, derivada de DT_SIN_PRI, e SEM_PRI ja e lida para reconcilia-la"
    ),
    "FATOR_RISC": (
        "presenca de fator de risco (35); alem de nenhum indicador estratificar "
        "por comorbidade, o campo e inutilizavel como binario na safra de "
        "referencia: foram medidos apenas os valores vazio (96.949) e '1' "
        "(68.448) em 165.397 registros -- nunca '2' (nao) nem '9' (ignorado). "
        "Sem o polo negativo nao ha denominador, e 'vazio' nao pode ser lido "
        "como 'sem fator de risco'"
    ),
}

# =============================================================================
# Colunas proibidas (minimizacao de dados pessoais)
# =============================================================================

DENIED_COLUMNS: Final[dict[str, str]] = {
    "NU_NOTIFIC": "identificador individual da notificacao",
    "DT_NASC": "data de nascimento (quase-identificador direto)",
    "COD_IDADE": "idade codificada com granularidade desnecessaria",
    "ID_MUNICIP": "municipio de notificacao (risco de reidentificacao)",
    "CO_MUN_NOT": "codigo do municipio de notificacao",
    "ID_MN_RESI": "municipio de residencia",
    "CO_MUN_RES": "codigo do municipio de residencia",
    "ID_REGIONA": "regional de saude de notificacao",
    "ID_RG_RESI": "regional de saude de residencia",
    "NM_UN_INTE": "nome da unidade de saude de internacao",
    "ID_MN_INTE": "municipio da unidade de internacao",
    "CO_MU_INTE": "codigo do municipio da unidade de internacao",
    "PAC_COCBO": "ocupacao do paciente (quase-identificador)",
    "PAC_DSCBO": "descricao da ocupacao do paciente",
    "TEM_CPF": "indicador de presenca de CPF",
    "ESTRANG": "indicador de nacionalidade estrangeira",
    "MORB_DESC": "texto livre sobre comorbidades do paciente",
    "OUTRO_DES": "texto livre sobre outros sintomas",
    "OUT_MORBI": "texto livre sobre outras morbidades",
    "CLASSI_OUT": "texto livre sobre agente etiologico",
    "OBES_IMC": "IMC individual do paciente",
    "CS_ETINIA": "etnia indigena (dado sensivel, nao necessario as metricas)",
    "CS_RACA": "raca/cor (dado sensivel; nenhum indicador estratifica por raca)",
    "CS_GESTANT": "idade gestacional (dado sensivel de saude sem uso nas metricas)",
    "DOSE_1_COV": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DOSE_2_COV": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DOSE_REF": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DOSE_2REF": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DOSE_ADIC": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DOS_RE_BI": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "LOTE_1_COV": "lote do imunizante (rastreavel ao individuo)",
    "LOTE_2_COV": "lote do imunizante (rastreavel ao individuo)",
    "LOTE_REF": "lote do imunizante (rastreavel ao individuo)",
    "LOTE_REF2": "lote do imunizante (rastreavel ao individuo)",
    "LOTE_ADIC": "lote do imunizante (rastreavel ao individuo)",
    "LOT_RE_BI": "lote do imunizante (rastreavel ao individuo)",
    "FAB_ADIC": "fabricante do imunizante (compoe o historico vacinal individual)",
    "FAB_RE_BI": "fabricante do imunizante (compoe o historico vacinal individual)",
    "FAB_COV_1": "fabricante do imunizante (compoe o historico vacinal individual)",
    "FAB_COV_2": "fabricante do imunizante (compoe o historico vacinal individual)",
    "FAB_COVRF": "fabricante do imunizante (compoe o historico vacinal individual)",
    "FAB_COVRF2": "fabricante do imunizante (compoe o historico vacinal individual)",
    "DT_UT_DOSE": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DT_DOSEUNI": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DT_1_DOSE": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "DT_2_DOSE": "data de dose vacinal (quase-identificador; so o indicador vacinal e usado)",
    "PAIS_VGM": "historico de viagem internacional",
    "CO_PS_VGM": "local de viagem internacional",
    "LO_PS_VGM": "local de viagem internacional",
}

# =============================================================================
# Dominios categoricos (fonte: dicionario oficial de dados)
# =============================================================================

YES_NO_IGNORED: Final[dict[int, str]] = {
    1: "Sim",
    2: "Nao",
    9: "Ignorado",
}

CODE_LABELS: Final[dict[str, dict[int, str]]] = {
    "HOSPITAL": YES_NO_IGNORED,
    "UTI": YES_NO_IGNORED,
    "VACINA": YES_NO_IGNORED,
    "VACINA_COV": YES_NO_IGNORED,
    "EVOLUCAO": {
        1: "Cura",
        2: "Obito",
        3: "Obito por outras causas",
        9: "Ignorado",
    },
    "CLASSI_FIN": {
        1: "SRAG por influenza",
        2: "SRAG por outro virus respiratorio",
        3: "SRAG por outro agente etiologico",
        4: "SRAG nao especificado",
        5: "SRAG por covid-19",
    },
    "CS_SEXO": {},  # campo textual: M / F / I
    "TP_IDADE": {1: "Dia", 2: "Mes", 3: "Ano"},
    # ATENCAO: SUPORT_VEN **nao** e um campo Sim/Nao/Ignorado. O dicionario
    # oficial (campo 56) declara 1=Sim invasivo, 2=Sim NAO invasivo, 3=Nao,
    # 9=Ignorado. Reaproveitar YES_NO_IGNORED aqui leria o codigo 2 como "Nao"
    # e inverteria o indicador de ventilacao para os 75.120 registros com
    # SUPORT_VEN=2 da safra de referencia -- que sao justamente os ventilados
    # de forma nao invasiva. O "sim" deste campo e {1, 2}, nunca {1}.
    "SUPORT_VEN": {1: "Sim, invasivo", 2: "Sim, nao invasivo", 3: "Nao", 9: "Ignorado"},
    "NOSOCOMIAL": YES_NO_IGNORED,
    "CRITERIO": {
        1: "Laboratorial",
        2: "Clinico epidemiologico",
        3: "Clinico",
        4: "Clinico imagem",
    },
}

# Codigos que significam ausencia de informacao. Nunca sao tratados como zero
# nem como "Nao": sao excluidos dos denominadores e contabilizados a parte.
#
# Este e o **padrao**, nao uma regra universal: ver MISSING_CODES_BY_COLUMN.
MISSING_CODES: Final[frozenset[int]] = frozenset({9})

#: Codigos de ausencia **por coluna**, quando o dicionario diverge do padrao.
#:
#: Tratar `{9}` como ausencia em todo campo categorico e uma generalizacao que o
#: dicionario nao autoriza. `CLASSI_FIN` (1..5) e `CRITERIO` (1..4) simplesmente
#: nao possuem o codigo 9: um 9 nessas colunas nao e "Ignorado", e um valor fora
#: do dominio -- e precisa aparecer como tal no relatorio, nao se diluir na
#: contagem de ignorados. O conjunto vazio declara isso explicitamente, em vez
#: de deixar a ausencia do codigo implicita no dominio.
MISSING_CODES_BY_COLUMN: Final[dict[str, frozenset[int]]] = {
    "CLASSI_FIN": frozenset(),
    "CRITERIO": frozenset(),
}


def missing_codes_for(column: str) -> frozenset[int]:
    """Codigos de ausencia validos para `column`.

    Args:
        column: nome da coluna categorica.

    Returns:
        Conjunto declarado em :data:`MISSING_CODES_BY_COLUMN` quando a coluna
        diverge do padrao, ou :data:`MISSING_CODES` caso contrario.
    """
    return MISSING_CODES_BY_COLUMN.get(column, MISSING_CODES)


# =============================================================================
# Colunas da tabela analitica final
# =============================================================================

#: Colunas de semana epidemiologica derivadas de `DT_SIN_PRI`.
#:
#: A semana epidemiologica do Ministerio da Saude **comeca no domingo** e nao e
#: a semana ISO: a SE 1 de um ano e a primeira semana iniciada em domingo com ao
#: menos 4 dias no ano novo. Derivar (em `src/data/cleaning/epiweek.py`) em vez
#: de ler `SEM_PRI` mantem uma unica definicao de semana para toda a serie,
#: inclusive quando o campo publicado esta ausente. `SEM_PRI` continua sendo
#: lida, mas apenas para reconciliacao: a divergencia e contada, nunca corrigida
#: nem preenchida por coalesce.
EPIWEEK_DERIVED_COLUMNS: Final[tuple[str, ...]] = (
    "semana_epi_ano",  # ano ao qual a semana pertence (pode diferir do ano civil)
    "semana_epi_num",  # numero da semana no ano epidemiologico (1..53)
    "semana_epi",  # rotulo ordenavel "AAAA-SS"
    "ano_sintomas",  # ano civil de DT_SIN_PRI
    "mes_sintomas",  # rotulo ordenavel "AAAA-MM"
)


#: Colunas derivadas criadas por `preprocess.py` (nao existem no arquivo bruto).
DERIVED_COLUMNS: Final[tuple[str, ...]] = (
    "faixa_etaria",  # faixa etaria agregada; a idade exata nao e persistida
    "ano_referencia",  # ano do arquivo de origem
) + EPIWEEK_DERIVED_COLUMNS

#: Flags de coerencia, uma por dimensao do registro.
#:
#: Sao separadas de proposito. Um unico booleano "registro invalido" seria
#: grosseiro demais: uma data de internacao impossivel nao deveria invalidar o
#: registro para a contagem de casos, que depende apenas de `DT_SIN_PRI`. Com
#: flags por dimensao, cada metrica exclui somente o que de fato compromete o
#: seu calculo, e o volume de cada problema fica visivel no relatorio de
#: qualidade em vez de sumir num descarte agregado.
COHERENCE_FLAGS: Final[dict[str, str]] = {
    "flag_data_invalida": (
        "eixo temporal primario inutilizavel: DT_SIN_PRI ausente, anterior ao "
        "inicio da serie ou posterior a data de digitacao. Unica flag que "
        "exclui o registro da view analitica, porque sem ela nenhuma metrica "
        "pode situar o caso no tempo."
    ),
    "flag_internacao_inconsistente": (
        "DT_INTERNA anterior aos primeiros sintomas, ou HOSPITAL=1 sem data de "
        "internacao. Afeta apenas indicadores que dependem da internacao."
    ),
    "flag_uti_inconsistente": (
        "DT_ENTUTI anterior aos primeiros sintomas, DT_SAIDUTI anterior a "
        "DT_ENTUTI, ou UTI=1 sem data de entrada. Afeta o censo de UTI, que "
        "depende da permanencia."
    ),
    "flag_evolucao_inconsistente": (
        "DT_EVOLUCA anterior aos primeiros sintomas, ou caso encerrado "
        "(EVOLUCAO em 1,2,3) sem data de evolucao. Nao afeta a taxa de "
        "mortalidade, que usa o codigo e nao a data, mas afeta a imputacao de "
        "permanencia em UTI."
    ),
    "flag_data_implausivel": (
        "alguma coluna de data traz um valor fora do intervalo fisicamente "
        "possivel -- anterior ao inicio da serie SIVEP-Gripe ou posterior a "
        "data de execucao da carga. Captura erros de digitacao de ano (1695, "
        "2202, 5202, 8202 foram medidos na fonte) que nenhuma comparacao entre "
        "datas detecta, porque a ordem relativa continua correta. Nao exclui o "
        "registro: marca e conta."
    ),
}

#: Colunas booleanas que traduzem os codigos do dicionario em conceitos
#: epidemiologicos. Sao derivadas em Python (`src/data/cleaning/derived.py`),
#: ao lado de :data:`CODE_LABELS`, e nao no SQL da view: a semantica dos codigos
#: vem do dicionario oficial, e manter a traducao em outra linguagem permitiria
#: que uma mudanca no dicionario nao chegasse ao calculo sem nada falhar.
DERIVED_SEMANTIC_COLUMNS: Final[tuple[str, ...]] = (
    "eh_obito_srag",
    "caso_encerrado",
    "foi_hospitalizado",
    "hospitalizacao_informada",
    "teve_admissao_uti",
    "uti_informado",
    "estadia_uti_utilizavel",
    "foi_ventilado",
    "ventilacao_invasiva",
    "ventilacao_nao_invasiva",
    "ventilacao_informada",
    "caso_nosocomial",
    "etiologia_laboratorial",
    "etiologia_criterio_informado",
    "grupo_etiologico",
    "status_caso",
    "vacinado_covid",
    "vacina_covid_informada",
    "vacinado_influenza",
    "vacina_influenza_informada",
)

#: Coluna que registra, por registro, quais valores a ingestao alterou.
#:
#: Flags de coerencia descrevem o que o dado tem de errado; esta coluna descreve
#: o que o pipeline *fez* com ele. Sao coisas diferentes: um registro pode estar
#: incoerente sem ter sido tocado, e pode ter sido alterado sem estar incoerente.
#:
#: Conteudo: codigos separados por virgula, ou string vazia quando o registro
#: chegou intacto a camada analitica. Permite localizar cada alteracao com SQL:
#: `SELECT * FROM srag_cases WHERE ajustes_aplicados <> ''`.
ADJUSTMENT_COLUMN: Final[str] = "ajustes_aplicados"

#: Codigos de ajuste possiveis e o que cada um significa.
ADJUSTMENT_CODES: Final[dict[str, str]] = {
    "uf_anulada": (
        "sigla de UF fora das 27 unidades federativas; o valor original era "
        "inutilizavel e foi substituido por nulo"
    ),
    "idade_anulada": (
        "idade normalizada fora do intervalo plausivel [0, 120] anos; substituida por nulo"
    ),
    "data_ilegivel": (
        "valor de data presente no arquivo bruto mas nao interpretavel em "
        "nenhum dos formatos publicados pela fonte; substituido por nulo"
    ),
    "codigo_ilegivel": (
        "valor categorico presente no arquivo bruto mas nao numerico (portanto "
        "sem correspondencia possivel no dicionario); substituido por nulo. "
        "Registrado pelo mesmo motivo que `data_ilegivel`: sem isso um campo "
        "preenchido com lixo ficaria indistinguivel de um campo vazio na origem"
    ),
    "idade_unidade_implausivel": (
        "NU_IDADE_N fora do dominio declarado para a unidade em TP_IDADE "
        "(1-dia admite 0 a 30; 2-mes admite 1 a 11); a idade derivada foi "
        "anulada. A unidade NAO e reinterpretada: nao ha como saber se o erro "
        "esta no numero ou na unidade, e escolher um dos dois seria inventar "
        "dado. Foram medidos 3 registros na safra de referencia"
    ),
}

#: Conjunto completo de colunas derivadas.
ALL_DERIVED_COLUMNS: Final[tuple[str, ...]] = (
    DERIVED_COLUMNS + tuple(COHERENCE_FLAGS) + DERIVED_SEMANTIC_COLUMNS + (ADJUSTMENT_COLUMN,)
)

AGE_BANDS: Final[tuple[tuple[int, int, str], ...]] = (
    (0, 4, "0-4"),
    (5, 11, "5-11"),
    (12, 17, "12-17"),
    (18, 29, "18-29"),
    (30, 39, "30-39"),
    (40, 49, "40-49"),
    (50, 59, "50-59"),
    (60, 69, "60-69"),
    (70, 79, "70-79"),
    (80, 200, "80+"),
)

UF_CODES: Final[frozenset[str]] = frozenset(
    {
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    }
)


def assert_no_denied_columns(columns: list[str]) -> None:
    """Falha se alguma coluna da denylist chegou a camada analitica.

    Args:
        columns: nomes das colunas presentes no dataframe ou tabela.

    Raises:
        ValueError: se ao menos uma coluna proibida estiver presente.
    """
    violations = sorted(set(columns) & set(DENIED_COLUMNS))
    if violations:
        details = "; ".join(f"{name} ({DENIED_COLUMNS[name]})" for name in violations)
        raise ValueError(f"Colunas com dados pessoais chegaram a camada analitica: {details}")
