"""Regra de celula pequena (Guardrail 2 -- dados sensiveis).

Uma proporcao calculada sobre pouquissimos casos e problematica por dois
motivos distintos, que aqui convergem na mesma decisao:

* **estatistico** -- uma taxa sobre 3 casos nao mede nada; um unico obito move
  o indicador em 33 pontos percentuais;
* **privacidade** -- quanto menor o denominador, mais um numero publicado se
  aproxima de descrever pessoas especificas em vez de uma populacao.

O escopo desta regra e deliberadamente estreito. A camada analitica ja aplica
minimizacao forte: nao ha municipio, a idade e agregada em faixa e a
granularidade geografica maxima e a UF. Com isso, a contagem de casos por dia
em um estado nao identifica ninguem -- e suprimi-la quebraria as series e os
graficos sem ganho de protecao. O que se suprime e a **proporcao** calculada
sobre denominador insuficiente, que e onde o risco e a inutilidade coincidem.
"""

from __future__ import annotations

from typing import Any

from src.config import get_settings
from src.observability.logging_config import get_logger

logger = get_logger(__name__)


def suppression_reason(denominator: int, threshold: int) -> str:
    """Texto publicado quando um indicador e suprimido."""
    return (
        f"Denominador insuficiente ({denominator} casos, minimo {threshold}). "
        "A proporcao nao e publicada: sobre tao poucos casos ela nao mede a "
        "populacao e se aproxima de descrever individuos. Reduza o recorte ou "
        "amplie a janela de analise."
    )


def enforce_minimum_cell_size(
    result: dict[str, Any], threshold: int | None = None
) -> dict[str, Any]:
    """Suprime o valor de um indicador calculado sobre denominador pequeno.

    O envelope e preservado -- definicao, periodo, filtros, fonte e limitacoes
    continuam publicados -- mas `value` vira `None` e `unavailable_reason`
    explica a supressao. O comportamento e o mesmo de qualquer outra
    indisponibilidade (Guardrail 6): declarar, nunca estimar.

    O numerador tambem e omitido, porque publicar "3 obitos" com o denominador
    suprimido nao protegeria nada.

    Args:
        result: envelope devolvido por uma tool de metrica.
        threshold: piso de contagem; padrao `MIN_CELL_SIZE`.

    Returns:
        O envelope, suprimido quando aplicavel. Indicadores ja indisponiveis e
        retornos sem denominador passam intactos.
    """
    threshold = threshold if threshold is not None else get_settings().min_cell_size
    denominator = result.get("denominator")

    if threshold <= 0 or denominator is None or result.get("value") is None:
        return result
    if denominator >= threshold:
        return result

    logger.info(
        "indicador suprimido pela regra de celula pequena",
        extra={
            "metric": result.get("metric"),
            "denominador": denominator,
            "minimo": threshold,
        },
    )

    suppressed = dict(result)
    suppressed["value"] = None
    suppressed["numerator"] = None
    suppressed["unavailable_reason"] = suppression_reason(denominator, threshold)
    suppressed["suppressed_by"] = "min_cell_size"
    return suppressed


#: Numero de eventos abaixo do qual uma taxa e considerada instavel.
#:
#: Convencao consolidada em estatistica de saude publica (NCHS e congeneres):
#: taxas baseadas em menos de 20 eventos tem erro padrao relativo alto demais
#: para sustentar comparacao. Nao e questao de privacidade -- e de precisao.
MIN_EVENTS_FOR_STABLE_RATE = 20


def annotate_rate_reliability(
    result: dict[str, Any], minimum_events: int = MIN_EVENTS_FOR_STABLE_RATE
) -> dict[str, Any]:
    """Declara instabilidade quando a taxa repousa sobre poucos eventos.

    Diferente da supressao por celula pequena, aqui o valor **e** publicado: o
    dado existe e e legitimo. O que se acrescenta e o aviso de que ele nao
    sustenta comparacao -- uma taxa de 12,5% construida sobre um unico obito
    varia 12 pontos percentuais com o proximo caso.

    Suprimir seria esconder informacao valida; publicar sem a ressalva seria
    sugerir uma precisao que o numero nao tem.

    Args:
        result: envelope devolvido por uma tool de metrica.
        minimum_events: piso de eventos para considerar a taxa estavel.

    Returns:
        O envelope, com `reliability_warning` quando aplicavel.
    """
    numerator = result.get("numerator")
    if result.get("value") is None or numerator is None:
        return result

    events = abs(numerator)
    if events >= minimum_events:
        return result

    annotated = dict(result)
    annotated["reliability_warning"] = (
        f"Taxa baseada em apenas {events} evento(s), abaixo do minimo de "
        f"{minimum_events} usualmente exigido para uma taxa estavel. O valor e "
        "real, mas nao sustenta comparacao entre periodos ou recortes: um unico "
        "caso a mais o desloca de forma relevante."
    )
    return annotated
