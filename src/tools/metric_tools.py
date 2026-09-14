"""Tools dos quatro indicadores epidemiologicos exigidos.

Cada tool e pequena, deterministica e especializada: recebe parametros ja
validados, abre o banco em modo somente leitura, delega o calculo a
`src.metrics.epidemiology` e devolve o envelope padronizado com valor,
numerador, denominador, periodo, filtros, fonte e limitacoes.

Nao existe tool generica de consulta: o modelo escolhe entre operacoes nomeadas,
nunca formula a pergunta ao banco.
"""

from __future__ import annotations

from typing import Any

from src.config import DATASUS_SOURCE_LABEL
from src.data.load_database import connect
from src.guardrails.small_cells import annotate_rate_reliability, enforce_minimum_cell_size
from src.metrics import epidemiology
from src.metrics.filters import AnalyticFilters
from src.observability.audit import audited
from src.tools.schemas import MetricQuery


def _filters(query: MetricQuery) -> AnalyticFilters:
    return AnalyticFilters(uf=query.uf, classification=query.classification)


@audited("get_case_growth_rate", source=DATASUS_SOURCE_LABEL)
def get_case_growth_rate(**kwargs: Any) -> dict[str, Any]:
    """Taxa de aumento de casos entre duas janelas consecutivas.

    Compara o numero de casos da janela mais recente analisavel com o da janela
    imediatamente anterior, de mesmo tamanho, pela data dos primeiros sintomas.

    Returns:
        Envelope da metrica. `value` e `None` quando a janela anterior nao tem
        casos, caso em que `unavailable_reason` explica a indisponibilidade.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        result = epidemiology.case_growth_rate(
            connection, _filters(query), window_days=query.window_days
        )
    return annotate_rate_reliability(enforce_minimum_cell_size(result.to_dict()))


@audited("get_mortality_rate", source=DATASUS_SOURCE_LABEL)
def get_mortality_rate(**kwargs: Any) -> dict[str, Any]:
    """Taxa de mortalidade (letalidade) entre casos encerrados de SRAG.

    Numerador: obitos por SRAG (EVOLUCAO = 2). Denominador: casos encerrados
    elegiveis (EVOLUCAO em 1, 2 ou 3). Casos com evolucao ignorada ou em aberto
    ficam fora de ambos.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        result = epidemiology.mortality_rate(
            connection, _filters(query), window_days=query.window_days
        )
    return annotate_rate_reliability(enforce_minimum_cell_size(result.to_dict()))


@audited("get_icu_metrics", source=DATASUS_SOURCE_LABEL)
def get_icu_metrics(**kwargs: Any) -> dict[str, Any]:
    """Indicadores de UTI entre casos de SRAG.

    Retorna a taxa de **admissao** em UTI entre hospitalizados -- a unica
    proporcao calculavel com o SIVEP-Gripe. A taxa de **ocupacao de leitos** vem
    explicitamente nula em `components`, com o motivo: o dataset nao contem
    capacidade instalada nem leitos ocupados. O censo diario de pacientes de
    SRAG em UTI acompanha o resultado como aproximacao de pressao assistencial.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        result = epidemiology.icu_metrics(
            connection, _filters(query), window_days=query.window_days
        )
    return annotate_rate_reliability(enforce_minimum_cell_size(result.to_dict()))


@audited("get_vaccination_metrics", source=DATASUS_SOURCE_LABEL)
def get_vaccination_metrics(**kwargs: Any) -> dict[str, Any]:
    """Cobertura vacinal declarada entre casos notificados de SRAG.

    Cobre covid-19 (VACINA_COV) e influenza (VACINA). A **taxa de vacinacao da
    populacao** vem explicitamente nula em `components`: o dataset so contem
    informacao vacinal de pessoas notificadas com SRAG, o que nao representa a
    populacao geral. A completude da informacao acompanha cada percentual.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        result = epidemiology.vaccination_metrics(
            connection, _filters(query), window_days=query.window_days
        )
    return annotate_rate_reliability(enforce_minimum_cell_size(result.to_dict()))


@audited("get_incidence_rate", source=f"{DATASUS_SOURCE_LABEL} + IBGE")
def get_incidence_rate(**kwargs: Any) -> dict[str, Any]:
    """Casos notificados de SRAG por 100 mil habitantes na janela analisada.

    O denominador e a populacao residente estimada pelo IBGE (referencia
    externa versionada em `data/reference/`). Sem a referencia carregada, o
    indicador e declarado nao calculavel -- nunca aproximado.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        result = epidemiology.incidence_rate(
            connection, _filters(query), window_days=query.window_days
        )
    return enforce_minimum_cell_size(result.to_dict())


@audited("get_seasonal_baseline", source=DATASUS_SOURCE_LABEL)
def get_seasonal_baseline(**kwargs: Any) -> dict[str, Any]:
    """Excesso (ou deficit) de casos sobre o baseline sazonal.

    Compara a janela atual com a mesma janela de calendario nos anos de
    baseline configurados (mediana). Distingue surto de sazonalidade -- algo
    que a taxa de aumento entre janelas consecutivas nao consegue fazer.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        result = epidemiology.seasonal_baseline(
            connection, _filters(query), window_days=query.window_days
        )
    return enforce_minimum_cell_size(result.to_dict())


@audited("get_notification_completeness", source=DATASUS_SOURCE_LABEL)
def get_notification_completeness(**kwargs: Any) -> dict[str, Any]:
    """Perfil do atraso de notificacao observado na base.

    Serve para qualificar a confiabilidade da janela recente: informa os
    percentis do atraso entre inicio de sintomas e digitacao e se o corte
    analitico configurado e suficiente.
    """
    query = MetricQuery(**kwargs)
    with connect() as connection:
        return epidemiology.notification_delay_profile(connection, _filters(query))
