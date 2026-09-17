"""Analise de sensibilidade das linhas identicas.

O problema
----------
A ingestao **conta** linhas identicas nas colunas persistidas, mas nao as
remove. A contagem sozinha nao responde a pergunta que importa -- *isso muda
algum indicador?* --, e sem essa resposta a decisao de nao deduplicar fica sem
lastro: ninguem sabe se ela e prudente ou se esta escondendo um erro de 5 pontos
percentuais na letalidade.

Este modulo responde: recalcula os indicadores sobre a janela analisada com e
sem colapso das linhas identicas, e publica a diferenca. A contagem principal
continua **sem** deduplicacao; a versao deduplicada e um cenario de
sensibilidade, rotulado como tal.

Por que nao deduplicar
----------------------
O identificador da notificacao (`NU_NOTIFIC`) e negado por minimizacao: ele nao
e lido do arquivo bruto. Sem ele, duas fichas identicas em todas as colunas
persistidas podem ser:

* a mesma notificacao publicada duas vezes -- duplicata real; ou
* dois pacientes distintos com os mesmos atributos agregados. Com faixa etaria
  no lugar da idade exata, sexo, UF, datas e um punhado de codigos clinicos, a
  colisao e comum em UF populosa e semana de pico.

Nao ha como distinguir os dois casos, e os erros nao sao simetricos: deduplicar
remove **casos reais** de forma irreversivel e silenciosa; nao deduplicar
mantem, no maximo, um excesso pequeno e mensuravel -- que e exatamente o que
esta funcao mede.

Pseudonimizacao do identificador: avaliada e recusada
------------------------------------------------------
A alternativa considerada foi ler `NU_NOTIFIC`, aplicar um hash com sal e
descarta-lo, guardando so o pseudonimo para deduplicar. Foi recusada por dois
motivos independentes, e bastaria um:

1. **Minimizacao (LGPD, art. 6, III).** O pseudonimo continua sendo dado
   pessoal: ele e um identificador direto e estavel de uma notificacao
   individual, e um hash de um numero de notificacao e reversivel por forca
   bruta sobre um espaco pequeno. Ler um identificador que hoje nao entra no
   sistema, para ganhar precisao num numero de segunda ordem, inverte a
   proporcionalidade entre finalidade e dado coletado.
2. **Beneficio desproporcional.** Medido na safra de referencia, ha **zero**
   `NU_NOTIFIC` repetido e zero linhas identicas nas 194 colunas brutas. O
   excedente aparece so depois do descarte de colunas -- ou seja, ele e artefato
   da propria minimizacao, e nao duplicacao na fonte.

A decisao fica registrada aqui, e nao apenas na documentacao, porque e uma
decisao de tratamento de dado pessoal: quem mudar de ideia precisa passar por
este texto.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from src.config import get_settings
from src.data.load_database import VIEW_ANALYTICS
from src.data.schema import ADJUSTMENT_COLUMN
from src.metrics.filters import AnalyticFilters, analysis_cutoff

_PERCENT_DECIMALS = 2

#: Diferenca em pontos percentuais a partir da qual o impacto deixa de ser
#: desprezivel e vira ressalva no relatorio.
#:
#: Meio ponto percentual e menor que a variacao que o proprio atraso de
#: notificacao produz entre duas execucoes consecutivas da mesma janela. Abaixo
#: disso, apontar a duplicidade como fonte de erro seria dar a ela um peso que
#: ela nao tem diante das demais incertezas ja declaradas.
MATERIALITY_THRESHOLD_PP: float = 0.5

#: Decisao de tratamento, publicada junto do resultado.
DEDUPLICATION_DECISION: str = (
    "A contagem principal NAO e deduplicada. Sem o identificador da notificacao "
    "-- negado por minimizacao -- nao ha como distinguir duplicata real de dois "
    "pacientes distintos com os mesmos atributos agregados, e os erros nao sao "
    "simetricos: deduplicar remove casos reais de forma irreversivel, enquanto "
    "nao deduplicar mantem um excedente pequeno e mensuravel, quantificado aqui."
)

PSEUDONYMIZATION_DECISION: str = (
    "Pseudonimizacao irreversivel do identificador da notificacao foi avaliada e "
    "RECUSADA: o pseudonimo continua sendo dado pessoal (identificador direto e "
    "estavel de uma notificacao), reverter um hash sobre um espaco pequeno e "
    "viavel, e o ganho seria desproporcional -- a fonte tem zero NU_NOTIFIC "
    "repetido e zero linhas identicas nas 194 colunas brutas."
)


def _has_column(connection: Any, column: str) -> bool:
    """Indica se a view analitica tem a coluna informada."""
    rows = connection.execute(f"DESCRIBE {VIEW_ANALYTICS}").fetchall()
    return any(str(row[0]) == column for row in rows)


def _ratio(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator * 100, _PERCENT_DECIMALS)


def _delta(with_duplicates: float | None, without: float | None) -> float | None:
    if with_duplicates is None or without is None:
        return None
    return round(without - with_duplicates, _PERCENT_DECIMALS)


def duplicate_sensitivity(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    """Impacto potencial das linhas identicas sobre os indicadores da janela.

    Recalcula os indicadores proporcionais duas vezes sobre a mesma janela -- na
    base como esta e com as linhas identicas colapsadas -- e devolve as duas
    versoes com a diferenca em pontos percentuais.

    O escopo e a janela analisada, e nao a base inteira, de proposito: o numero
    que o relatorio publica vem da janela, e uma duplicidade concentrada fora
    dela nao afeta o que se esta lendo.

    Args:
        connection: conexao com o banco analitico.
        filters: recorte de UF/classificacao.
        window_days: tamanho da janela; padrao `GROWTH_WINDOW_DAYS`.

    Returns:
        Bloco com contagens, cenario deduplicado, diferencas e a decisao de
        tratamento adotada.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)
    clause, parameters = filters.where_clause()

    # Excluir a coluna de ajustes reproduz exatamente o criterio da ingestao: ela
    # descreve o que o pipeline fez com o registro, nao o conteudo dele, e duas
    # fichas iguais tratadas de formas diferentes nao sao registros diferentes.
    #
    # A exclusao e condicional porque a coluna e opcional: uma base montada fora
    # do pipeline de tratamento (como a sintetica dos testes) nao a tem, e um
    # `EXCLUDE` de coluna inexistente quebra a consulta inteira em vez de
    # degradar o criterio.
    projection = "*"
    if _has_column(connection, ADJUSTMENT_COLUMN):
        projection = f"* EXCLUDE ({ADJUSTMENT_COLUMN})"

    row = connection.execute(
        f"""
        WITH janela AS (
            SELECT {projection}
            FROM {VIEW_ANALYTICS}
            WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        ),
        unicos AS (SELECT DISTINCT * FROM janela)
        SELECT
            (SELECT count(*) FROM janela),
            (SELECT count(*) FROM unicos),
            (SELECT count(*) FILTER (WHERE caso_encerrado) FROM janela),
            (SELECT count(*) FILTER (WHERE caso_encerrado) FROM unicos),
            (SELECT count(*) FILTER (WHERE eh_obito_srag) FROM janela),
            (SELECT count(*) FILTER (WHERE eh_obito_srag) FROM unicos),
            (SELECT count(*) FILTER (WHERE uti_informado) FROM janela),
            (SELECT count(*) FILTER (WHERE uti_informado) FROM unicos),
            (SELECT count(*) FILTER (WHERE teve_admissao_uti) FROM janela),
            (SELECT count(*) FILTER (WHERE teve_admissao_uti) FROM unicos),
            (SELECT count(*) FILTER (WHERE vacina_covid_informada) FROM janela),
            (SELECT count(*) FILTER (WHERE vacina_covid_informada) FROM unicos),
            (SELECT count(*) FILTER (WHERE vacinado_covid) FROM janela),
            (SELECT count(*) FILTER (WHERE vacinado_covid) FROM unicos)
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    (
        cases,
        unique_cases,
        closed,
        unique_closed,
        deaths,
        unique_deaths,
        icu_known,
        unique_icu_known,
        icu_yes,
        unique_icu_yes,
        vaccine_known,
        unique_vaccine_known,
        vaccinated,
        unique_vaccinated,
    ) = (int(value) for value in row)

    excess = cases - unique_cases
    indicators = {
        "mortality_rate": {
            "nome": "letalidade entre casos encerrados",
            "valor_publicado_pct": _ratio(deaths, closed),
            "valor_se_deduplicado_pct": _ratio(unique_deaths, unique_closed),
        },
        "icu_admission_rate": {
            "nome": "taxa de admissao em UTI entre internados",
            "valor_publicado_pct": _ratio(icu_yes, icu_known),
            "valor_se_deduplicado_pct": _ratio(unique_icu_yes, unique_icu_known),
        },
        "vaccination_coverage_among_cases": {
            "nome": "cobertura vacinal declarada (covid-19) entre casos",
            "valor_publicado_pct": _ratio(vaccinated, vaccine_known),
            "valor_se_deduplicado_pct": _ratio(unique_vaccinated, unique_vaccine_known),
        },
    }
    for payload in indicators.values():
        payload["diferenca_pp"] = _delta(
            payload["valor_publicado_pct"], payload["valor_se_deduplicado_pct"]
        )

    deltas = [
        abs(payload["diferenca_pp"])
        for payload in indicators.values()
        if payload["diferenca_pp"] is not None
    ]
    material = bool(deltas) and max(deltas) >= MATERIALITY_THRESHOLD_PP

    return {
        "periodo": {
            "inicio": start.isoformat(),
            "fim": cutoff.isoformat(),
            "descricao": f"ultimos {window} dias ate a data de corte analitica",
        },
        "filtros": filters.to_dict(),
        "criterio": (
            "linhas identicas em todas as colunas persistidas, exceto "
            f"`{ADJUSTMENT_COLUMN}`, que descreve o tratamento e nao o conteudo"
            if projection != "*"
            else (
                "linhas identicas em todas as colunas da view analitica (a base "
                f"em uso nao tem a coluna `{ADJUSTMENT_COLUMN}`)"
            )
        ),
        "casos_na_janela": cases,
        "casos_se_deduplicado": unique_cases,
        "linhas_identicas_excedentes": excess,
        "percentual_excedente": _ratio(excess, cases),
        "indicadores": indicators,
        "impacto_material": material,
        "limiar_de_materialidade_pp": MATERIALITY_THRESHOLD_PP,
        "veredito": (
            "As linhas identicas deslocam pelo menos um indicador em "
            f"{max(deltas) if deltas else 0} ponto(s) percentual(is), acima do "
            f"limiar de {MATERIALITY_THRESHOLD_PP} pp. O cenario deduplicado "
            "acima deve ser lido junto do valor publicado."
            if material
            else (
                "Nenhum indicador se desloca mais que "
                f"{MATERIALITY_THRESHOLD_PP} ponto percentual quando as linhas "
                "identicas sao colapsadas: a duplicidade nao e fonte relevante "
                "de erro nesta janela, diante das incertezas ja declaradas."
            )
        ),
        "decisao_de_tratamento": DEDUPLICATION_DECISION,
        "pseudonimizacao": PSEUDONYMIZATION_DECISION,
        "nenhum_registro_removido": True,
    }
