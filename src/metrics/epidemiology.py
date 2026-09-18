"""Calculo dos indicadores epidemiologicos, em SQL deterministico.

Nenhum numero deste modulo passa por um modelo de linguagem. Todo indicador e
uma consulta parametrizada sobre a view analitica do DuckDB e retorna o envelope
:class:`MetricResult`, com numerador, denominador, periodo, filtros, fonte e
limitacoes -- o que torna cada valor do relatorio rastreavel ate a consulta que
o produziu.

Regra transversal: quando o denominador e zero, ausente ou insuficiente, o
indicador retorna `value=None` com `unavailable_reason` preenchido. Nunca se
devolve zero no lugar de "nao calculavel".
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from src.config import get_settings
from src.data.load_database import VIEW_ANALYTICS
from src.data.reference.icu_capacity import ICU_BED_TYPES_FOR_SRAG
from src.data.reference.tables import (
    TABLE_ICU_CAPACITY,
    TABLE_POPULATION,
    TABLE_VACCINATION,
)
from src.data.reference.vaccination import CAMPAIGNS
from src.metrics.definitions import (
    CASE_GROWTH_RATE,
    ICU_ADMISSION_RATE,
    ICU_BED_OCCUPANCY_RATE,
    INCIDENCE_RATE,
    MORTALITY_RATE,
    POPULATION_VACCINATION_COVERAGE,
    SEASONAL_BASELINE,
    VACCINATION_COVERAGE,
    MetricDefinition,
    MetricResult,
)
from src.metrics.filters import AnalyticFilters, analysis_cutoff, reference_date

_PERCENT_DECIMALS = 2


def _period(start: date, end: date, label: str) -> dict[str, Any]:
    return {"inicio": start.isoformat(), "fim": end.isoformat(), "descricao": label}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, _PERCENT_DECIMALS)


def _unavailable(
    definition: MetricDefinition,
    reason: str,
    *,
    period: dict[str, Any],
    filters: AnalyticFilters,
    numerator: int | None = None,
    denominator: int | None = None,
    components: dict[str, Any] | None = None,
) -> MetricResult:
    """Constroi um resultado explicitamente indisponivel (Guardrail 6)."""
    return MetricResult(
        metric=definition.key,
        value=None,
        numerator=numerator,
        denominator=denominator,
        period=period,
        filters=filters.to_dict(),
        definition=definition,
        components=components or {},
        unavailable_reason=reason,
    )


def _analysis_window(connection: Any, window_days: int | None) -> tuple[date, date, int]:
    """Janela analitica canonica: `(inicio, fim, dias)`.

    O fim e a data de corte (maior digitacao menos o atraso de notificacao) e o
    inicio recua `window_days - 1` dias. Todas as metricas usam esta funcao para
    que nao existam duas nocoes de "ultimos 30 dias" no projeto.
    """
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    return cutoff - timedelta(days=window - 1), cutoff, window


# =============================================================================
# Diagnostico de completude
# =============================================================================


def data_currency(connection: Any) -> dict[str, Any]:
    """As cinco datas que situam o relatorio no tempo, mais a da fonte.

    Existem porque elas **nao** coincidem, e confundi-las e o erro de leitura
    mais provavel do relatorio inteiro: alguem le "relatorio de setembro" e
    supoe que os dados vao ate setembro. Nao vao. Entre o dia de execucao e o
    ultimo dia analisavel ha duas defasagens empilhadas -- a da fonte, que
    publica com atraso, e a do atraso de notificacao, que o sistema desconta de
    proposito para nao ler digitacao pendente como queda de casos.

    Returns:
        Bloco com as datas, a defasagem em dias entre elas e o que cada uma
        significa.
    """
    settings = get_settings()
    today = datetime.now(tz=UTC).date()
    reference = reference_date(connection)
    cutoff = reference - timedelta(days=settings.reporting_lag_days)

    row = connection.execute(
        f"SELECT max(data_sintomas) FROM {VIEW_ANALYTICS} WHERE data_sintomas <= ?",
        [today],
    ).fetchone()
    latest_symptoms = row[0] if row else None

    return {
        "data_atual_do_sistema": today.isoformat(),
        "data_mais_recente_de_sintomas_na_base": (
            latest_symptoms.isoformat() if latest_symptoms else None
        ),
        "data_mais_recente_de_digitacao_na_base": reference.isoformat(),
        "data_de_corte_epidemiologica": cutoff.isoformat(),
        "atraso_de_notificacao_configurado_dias": settings.reporting_lag_days,
        "defasagem_ate_a_digitacao_dias": (today - reference).days,
        "defasagem_ate_o_corte_dias": (today - cutoff).days,
        "atualizacao_da_fonte": _source_update(),
        "significado": {
            "data_atual_do_sistema": (
                "dia em que o relatorio foi executado. NAO e a data ate a qual ha dado disponivel."
            ),
            "data_mais_recente_de_digitacao_na_base": (
                "ultima ficha digitada presente no arquivo publicado pelo "
                "DATASUS; e a ancora de todas as janelas, no lugar de hoje"
            ),
            "data_de_corte_epidemiologica": (
                "ultimo dia considerado confiavel: a data de digitacao menos o "
                "atraso de notificacao configurado. Todo indicador termina aqui"
            ),
            "data_mais_recente_de_sintomas_na_base": (
                "ha fichas com sintomas depois da data de corte; elas existem, "
                "mas a janela nao as usa porque a digitacao delas ainda esta "
                "incompleta"
            ),
        },
    }


def _source_update() -> dict[str, Any]:
    """Quando o arquivo bruto foi obtido, segundo o manifesto de proveniencia."""
    import json

    settings = get_settings()
    path = settings.raw_manifest_path
    if not path.exists():
        return {
            "disponivel": False,
            "motivo": (
                "manifesto de proveniencia nao encontrado; a base pode ter sido "
                "montada fora do fluxo de ingestao"
            ),
        }
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"disponivel": False, "motivo": "manifesto de proveniencia ilegivel"}

    entries = list(manifest.values()) if isinstance(manifest, dict) else list(manifest)
    obtained = [
        str(entry.get("downloaded_at"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("downloaded_at")
    ]
    if not obtained:
        return {"disponivel": False, "motivo": "manifesto sem data de obtencao"}
    return {
        "disponivel": True,
        "obtido_da_fonte_em": max(obtained),
        "anos_no_manifesto": sorted(
            str(entry.get("year")) for entry in entries if isinstance(entry, dict)
        ),
        "observacao": (
            "data em que o arquivo foi baixado do Open DATASUS, nao a data de "
            "publicacao da safra pela fonte"
        ),
    }


def notification_delay_profile(
    connection: Any, filters: AnalyticFilters | None = None
) -> dict[str, Any]:
    """Mede o atraso de notificacao observado na propria base.

    O atraso entre o inicio dos sintomas e a digitacao da ficha determina quanto
    da serie recente ainda esta incompleto. Medi-lo -- em vez de assumir um valor
    -- permite dizer no relatorio *quanto* a janela recente e confiavel e
    verificar se `REPORTING_LAG_DAYS` esta calibrado.

    Args:
        connection: conexao DuckDB somente leitura.
        filters: recorte por UF e classificacao final.

    Returns:
        Percentis do atraso em dias, o corte configurado e um alerta quando o
        corte adotado e menor que o percentil 75 observado.
    """
    filters = filters or AnalyticFilters()
    settings = get_settings()
    cutoff = analysis_cutoff(connection)
    horizon = cutoff - timedelta(days=365)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            quantile_cont(date_diff('day', data_sintomas, data_digitacao), 0.5),
            quantile_cont(date_diff('day', data_sintomas, data_digitacao), 0.75),
            quantile_cont(date_diff('day', data_sintomas, data_digitacao), 0.9),
            count(*)
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ?
          AND data_digitacao IS NOT NULL
          AND {clause}
        """,
        [horizon, cutoff, *parameters],
    ).fetchone()

    median, p75, p90, sample = row
    configured = settings.reporting_lag_days
    under_calibrated = p75 is not None and configured < float(p75)

    return {
        "atualidade_da_base": data_currency(connection),
        "atraso_mediano_dias": float(median) if median is not None else None,
        "atraso_p75_dias": float(p75) if p75 is not None else None,
        "atraso_p90_dias": float(p90) if p90 is not None else None,
        "registros_avaliados": int(sample),
        "corte_configurado_dias": configured,
        "corte_suficiente": not under_calibrated,
        "alerta": (
            f"O corte configurado ({configured} dias) e menor que o atraso do "
            f"percentil 75 observado ({p75} dias): a janela recente ainda esta "
            "incompleta e a variacao de casos tende a ser subestimada."
            if under_calibrated
            else None
        ),
    }


# =============================================================================
# Indicador 1 -- Taxa de aumento de casos
# =============================================================================


def case_growth_rate(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Compara duas janelas consecutivas de casos por data de primeiros sintomas.

    Args:
        connection: conexao DuckDB somente leitura.
        filters: recorte por UF e classificacao final.
        window_days: tamanho de cada janela; padrao `GROWTH_WINDOW_DAYS`.

    Returns:
        Variacao percentual entre a janela atual e a anterior.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)

    current_start = cutoff - timedelta(days=window - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=window - 1)

    # --- Maturidade simetrica -------------------------------------------------
    #
    # As duas janelas nao sao observadas com a mesma maturidade. A janela atual
    # termina no corte e teve `REPORTING_LAG_DAYS` dias para ser digitada; a
    # anterior termina uma janela antes e teve `lag + janela` dias. Comparar as
    # duas cruas subestima o crescimento de forma sistematica -- e o erro que
    # produz "queda de casos" durante uma subida real, justamente no indicador
    # que dispara o alerta.
    #
    # A correcao e censura a direita simetrica **por janela**: cada janela e
    # contada como era conhecida `L` dias apos o seu proprio fechamento. Para a
    # janela atual isso e todo o dado disponivel (cutoff + L = data de
    # referencia); para a anterior, apenas o que ja havia sido digitado ate
    # `fim da janela anterior + L`. As duas passam a ter exatamente o mesmo
    # prazo de observacao depois do ultimo dia de sintomas que contem.
    #
    # Preferida a censura por registro (`atraso <= L` em ambas): esta descarta
    # apenas as chegadas tardias da janela anterior, enquanto aquela descartaria
    # tambem os notificadores lentos da janela atual, jogando fora dado
    # legitimo dos dois lados sem ganho de comparabilidade.
    maturity_days = get_settings().reporting_lag_days
    previous_observed_until = previous_end + timedelta(days=maturity_days)
    # A janela atual fecha no corte e tem `maturity_days` de observacao depois
    # dele -- o que e, por construcao, a data de referencia da base.
    current_observed_until = cutoff + timedelta(days=maturity_days)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE data_sintomas BETWEEN ? AND ?) AS atual_bruto,
            count(*) FILTER (WHERE data_sintomas BETWEEN ? AND ?) AS anterior_bruto,
            -- A condicao de maturidade e a MESMA nos dois lados: a janela so
            -- difere no seu proprio prazo de observacao. Exigir digitacao
            -- apenas da janela anterior deixaria o registro sem DT_DIGITA
            -- entrar no numerador e nunca no denominador -- invisivel nesta
            -- safra (0% de ausencia), mas grave numa carga multi-ano: no
            -- INFLUD19 a coluna esta 34,37% vazia.
            count(*) FILTER (WHERE data_sintomas BETWEEN ? AND ?
                             AND data_digitacao IS NOT NULL
                             AND data_digitacao <= ?) AS atual,
            count(*) FILTER (WHERE data_sintomas BETWEEN ? AND ?
                             AND data_digitacao IS NOT NULL
                             AND data_digitacao <= ?) AS anterior
        FROM {VIEW_ANALYTICS}
        WHERE {clause}
        """,
        [
            current_start,
            cutoff,
            previous_start,
            previous_end,
            current_start,
            cutoff,
            current_observed_until,
            previous_start,
            previous_end,
            previous_observed_until,
            *parameters,
        ],
    ).fetchone()

    raw_current, raw_previous, current_cases, previous_cases = (int(value) for value in row)
    period = _period(
        previous_start,
        cutoff,
        f"janela atual {current_start.isoformat()} a {cutoff.isoformat()} "
        f"comparada a {previous_start.isoformat()} a {previous_end.isoformat()}",
    )
    components = {
        "casos_periodo_atual": current_cases,
        "casos_periodo_anterior": previous_cases,
        # Contagens sem censura, publicadas para que a diferenca entre o valor
        # corrigido e o ingenuo seja visivel em vez de afirmada.
        "casos_periodo_atual_sem_censura": raw_current,
        "casos_periodo_anterior_sem_censura": raw_previous,
        "maturidade_simetrica_dias": maturity_days,
        "janela_atual_observada_ate": current_observed_until.isoformat(),
        "janela_anterior_observada_ate": previous_observed_until.isoformat(),
        # Soma dos dois lados: registros sem data de digitacao e registros
        # digitados depois do prazo de observacao da propria janela.
        "casos_excluidos_por_imaturidade": (raw_current - current_cases)
        + (raw_previous - previous_cases),
        "janela_dias": window,
        "periodo_atual": _period(current_start, cutoff, "janela atual"),
        "periodo_anterior": _period(previous_start, previous_end, "janela anterior"),
        "data_corte_analitica": cutoff.isoformat(),
        "completude_da_notificacao": notification_delay_profile(connection, filters),
    }

    if previous_cases == 0:
        return _unavailable(
            CASE_GROWTH_RATE,
            "A janela anterior nao possui casos digitados ate "
            f"{previous_observed_until.isoformat()} (fechamento da janela mais "
            f"{maturity_days} dias de observacao); a variacao percentual e "
            "matematicamente indefinida (divisao por zero).",
            period=period,
            filters=filters,
            numerator=current_cases - previous_cases,
            denominator=0,
            components=components,
        )

    value = round((current_cases - previous_cases) / previous_cases * 100, _PERCENT_DECIMALS)
    return MetricResult(
        metric=CASE_GROWTH_RATE.key,
        value=value,
        numerator=current_cases - previous_cases,
        denominator=previous_cases,
        period=period,
        filters=filters.to_dict(),
        definition=CASE_GROWTH_RATE,
        components=components,
        records_used=current_cases + previous_cases,
    )


# =============================================================================
# Indicador 2 -- Letalidade entre casos encerrados
# =============================================================================


#: Teto do deslocamento da coorte madura, em dias.
#:
#: Sem teto, uma base com poucos encerramentos antigos produziria um percentil
#: enorme e a coorte madura cairia fora da serie -- trocando um vies por uma
#: janela vazia.
_MAX_OUTCOME_MATURITY_DAYS: Final[int] = 120

#: Percentil do tempo ate o encerramento usado como deslocamento.
_OUTCOME_MATURITY_PERCENTILE: Final[float] = 0.90


def _outcome_maturity_days(connection: Any, filters: AnalyticFilters | None = None) -> int:
    """Tempo tipico entre o inicio dos sintomas e o encerramento do caso.

    Medido na propria base -- o percentil configurado da diferenca entre
    `DT_SIN_PRI` e `DT_ENCERRA` nos casos ja encerrados -- e nao arbitrado. E o
    deslocamento aplicado a coorte madura da letalidade.

    Args:
        connection: conexao com o banco analitico.
        filters: recorte de UF/classificacao.

    Returns:
        Dias de deslocamento, limitado a :data:`_MAX_OUTCOME_MATURITY_DAYS`.
    """
    filters = filters or AnalyticFilters()
    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT quantile_cont(date_diff('day', data_sintomas, data_encerramento), ?)
        FROM {VIEW_ANALYTICS}
        WHERE caso_encerrado
          AND data_encerramento IS NOT NULL
          AND data_encerramento >= data_sintomas
          AND {clause}
        """,
        [_OUTCOME_MATURITY_PERCENTILE, *parameters],
    ).fetchone()

    if row is None or row[0] is None:
        return _MAX_OUTCOME_MATURITY_DAYS
    return max(1, min(int(round(float(row[0]))), _MAX_OUTCOME_MATURITY_DAYS))


def mortality_rate(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Letalidade entre casos encerrados de SRAG na janela analisada."""
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*)                                        AS casos,
            count(*) FILTER (WHERE caso_encerrado)           AS encerrados,
            count(*) FILTER (WHERE eh_obito_srag)            AS obitos_srag,
            count(*) FILTER (WHERE EVOLUCAO = 3)             AS obitos_outras_causas,
            count(*) FILTER (WHERE EVOLUCAO = 9)             AS evolucao_ignorada,
            count(*) FILTER (WHERE EVOLUCAO IS NULL)         AS evolucao_ausente
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    total, closed, deaths, other_deaths, ignored, absent = (int(value) for value in row)
    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")

    # --- Coorte madura --------------------------------------------------------
    #
    # A letalidade sobre a janela recente e censurada a direita de forma
    # desigual: o obito encerra depressa, a cura encerra devagar. Uma janela com
    # 69% de encerramento nao e comparavel com outra que tem 90%, e a diferenca
    # entre elas pode ser inteiramente artefato de maturacao -- nao mudanca real
    # de gravidade.
    #
    # Publicamos ao lado a mesma taxa sobre uma coorte deslocada, com tempo
    # suficiente para encerrar, e o percentual de encerramento das duas. O valor
    # principal segue sendo o da janela recente, que e o que responde "como esta
    # agora"; a coorte madura e que permite dizer se a variacao e real.
    maturity = _outcome_maturity_days(connection, filters)
    mature_end = cutoff - timedelta(days=maturity)
    mature_start = mature_end - timedelta(days=window - 1)
    mature = connection.execute(
        f"""
        SELECT
            count(*)                               AS casos,
            count(*) FILTER (WHERE caso_encerrado)  AS encerrados,
            count(*) FILTER (WHERE eh_obito_srag)   AS obitos
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        """,
        [mature_start, mature_end, *parameters],
    ).fetchone()
    mature_total, mature_closed, mature_deaths = (int(value) for value in mature)

    components = {
        "casos_no_periodo": total,
        "casos_encerrados": closed,
        "obitos_por_srag": deaths,
        "obitos_por_outras_causas": other_deaths,
        "evolucao_ignorada": ignored,
        "evolucao_nao_informada": absent,
        # Em aberto = sem evolucao informada. Um codigo fora do dominio nao e
        # caso em aberto; ele e contado no relatorio de qualidade.
        "casos_em_aberto": absent,
        "percentual_em_aberto": _ratio(absent, total) if total else None,
        "letalidade_bruta_sobre_todos_os_casos": (_ratio(deaths, total) if total else None),
        "percentual_encerrado": _ratio(closed, total) if total else None,
        "coorte_madura": {
            "definicao": (
                "mesma janela deslocada para tras o tempo tipico ate o "
                "encerramento, de modo que os desfechos ja tenham sido "
                "registrados; serve para distinguir variacao real de artefato "
                "de maturacao"
            ),
            "dias_de_deslocamento": maturity,
            "periodo": _period(mature_start, mature_end, "coorte madura"),
            "casos": mature_total,
            "encerrados": mature_closed,
            "obitos_por_srag": mature_deaths,
            "percentual_encerrado": (_ratio(mature_closed, mature_total) if mature_total else None),
            "letalidade": _ratio(mature_deaths, mature_closed) if mature_closed else None,
        },
        "data_corte_analitica": cutoff.isoformat(),
    }

    if closed == 0:
        return _unavailable(
            MORTALITY_RATE,
            "Nao ha casos encerrados no periodo analisado; sem denominador "
            "elegivel, a letalidade entre casos encerrados nao pode ser calculada.",
            period=period,
            filters=filters,
            numerator=deaths,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=MORTALITY_RATE.key,
        value=_ratio(deaths, closed),
        numerator=deaths,
        denominator=closed,
        period=period,
        filters=filters.to_dict(),
        definition=MORTALITY_RATE,
        components=components,
        records_used=total,
    )


# =============================================================================
# Indicador 3 -- UTI
# =============================================================================


def icu_metrics(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Indicadores de UTI derivados **apenas** do SIVEP-Gripe.

    Devolve a taxa de **admissao** em UTI entre hospitalizados -- severidade dos
    casos notificados -- e o censo diario de pacientes de SRAG em UTI,
    acompanhado da qualidade da permanencia que o sustenta.

    Nenhum dos dois e ocupacao de leitos. A ocupacao e um indicador separado
    (:func:`icu_bed_occupancy_rate`), porque exige um denominador de capacidade
    que este dataset nao tem; `components` traz um ponteiro explicito para ele,
    justamente para que a taxa acima nao seja lida como se fosse ocupacao.
    """
    filters = filters or AnalyticFilters()
    start, cutoff, window = _analysis_window(connection, window_days)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE eh_internado)                     AS hospitalizados,
            count(*) FILTER (WHERE eh_internado AND uti_informado)   AS uti_informado,
            count(*) FILTER (WHERE eh_internado AND teve_admissao_uti) AS admitidos_uti,
            count(*) FILTER (WHERE eh_internado AND UTI = 9)         AS uti_ignorado,
            count(*) FILTER (WHERE eh_internado AND UTI IS NULL)     AS uti_ausente,
            count(*) FILTER (WHERE uti_informado AND HOSPITAL IS NULL) AS uti_hospital_ausente,
            count(*) FILTER (WHERE uti_informado AND HOSPITAL = 9)      AS uti_hospital_ignorado,
            -- Impossibilidade logica: admissao em UTI com internacao negada.
            count(*) FILTER (WHERE teve_admissao_uti AND HOSPITAL = 2)  AS uti_hospital_negado
        FROM (
            SELECT *,
                   -- Denominador da taxa de admissao em UTI. `HOSPITAL`
                   -- ausente ou ignorado nao pode ser lido como "nao
                   -- internado": esta base e de SRAG **hospitalizada**, e
                   -- tratar a ausencia como "Nao" era o unico ponto do
                   -- pipeline onde missing virava negativa.
                   --
                   -- A condicao NAO olha para `UTI`. Resgatar o registro de
                   -- `HOSPITAL` desconhecido apenas quando ha admissao em UTI
                   -- declarada condicionaria a entrada no denominador ao valor
                   -- do proprio numerador -- vies de selecao classico, que
                   -- inflava a taxa. So sai quem declarou `HOSPITAL = 2`.
                   foi_hospitalizado OR NOT hospitalizacao_informada AS eh_internado
            FROM {VIEW_ANALYTICS}
            WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        )
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    (
        hospitalized,
        icu_known,
        icu_yes,
        icu_ignored,
        icu_absent,
        icu_hospital_missing,
        icu_hospital_ignored,
        icu_hospital_denied,
    ) = (int(value) for value in row)
    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")

    census = icu_patient_census(connection, filters, window_days=window)

    # --- Pico so na parte madura da serie ------------------------------------
    #
    # O censo da cauda direita e contaminado por construcao: quanto mais recente
    # o dia, menos `DT_SAIDUTI` e `DT_EVOLUCA` ja foram digitados, e mais
    # estadias sao contadas como ainda em curso. O efeito e monotono, entao o
    # maximo bruto cai **sempre** no ultimo dia da janela e nao descreve um pico
    # epidemiologico -- descreve a borda da observacao. Na base de referencia, o
    # censo reportado sobe 38% no fim da janela enquanto o censo das estadias
    # com saida registrada cai.
    #
    # O pico publicado e o da parte da serie ja madura: dias anteriores ao corte
    # menos o teto de permanencia, alem do qual nenhuma estadia pode continuar
    # imputada. O maximo bruto segue publicado ao lado, rotulado.
    cap_days = icu_stay_cap(connection, filters)["dias"]
    mature_until = cutoff - timedelta(days=cap_days)
    mature_census = [point for point in census if date.fromisoformat(point["data"]) <= mature_until]

    peak = max(mature_census, key=lambda point: point["pacientes_em_uti"], default=None)
    raw_peak = max(census, key=lambda point: point["pacientes_em_uti"], default=None)

    components = {
        "internados_no_periodo": hospitalized,
        "com_informacao_de_uti": icu_known,
        "admitidos_em_uti": icu_yes,
        "uti_ignorado": icu_ignored,
        "uti_nao_informado": icu_absent,
        # Registros recuperados para o denominador por terem `UTI` informado
        # apesar de `HOSPITAL` nao dizer "Sim". Publicados nos dois bracos --
        # nao so nos admitidos -- para que a simetria da regra seja auditavel.
        "com_uti_informado_e_hospital_ausente": icu_hospital_missing,
        "com_uti_informado_e_hospital_ignorado": icu_hospital_ignored,
        # Contradicao no registro, publicada mas nao resgatada.
        "admitidos_em_uti_com_internacao_negada": icu_hospital_denied,
        "censo_diario_pico_pacientes_em_uti": peak["pacientes_em_uti"] if peak else 0,
        "censo_diario_pico_data": peak["data"] if peak else None,
        "censo_diario_pico_percentual_imputado": (peak["percentual_imputado"] if peak else None),
        "censo_diario_pico_apurado_ate": mature_until.isoformat(),
        "censo_diario_pico_criterio": (
            "maximo da parte madura da serie (ate o corte menos o teto de "
            f"permanencia de {cap_days} dias). A cauda direita e contaminada "
            "pela digitacao pendente de saida e sobe de forma artificial"
        ),
        "censo_diario_maximo_bruto": {
            "pacientes_em_uti": raw_peak["pacientes_em_uti"] if raw_peak else 0,
            "data": raw_peak["data"] if raw_peak else None,
            "percentual_imputado": (raw_peak["percentual_imputado"] if raw_peak else None),
            "advertencia": (
                "inclui a cauda contaminada; tende a cair no ultimo dia da "
                "janela por artefato de observacao, nao por pico real"
            ),
        },
        "completude_da_permanencia_em_uti": icu_stay_completeness(
            connection, filters, window_days=window
        ),
        "censo_diario": census,
        # A ocupacao de leitos e um indicador SEPARADO, com denominador vindo do
        # CNES (`icu_bed_occupancy_rate`). O ponteiro fica aqui para que ninguem
        # leia a taxa de admissao acima como se fosse ocupacao -- foi essa
        # confusao que o indicador proprio veio desfazer.
        "taxa_de_ocupacao_de_leitos_de_uti": {
            "indicador": ICU_BED_OCCUPANCY_RATE.key,
            "nota": (
                "Indicador distinto, calculado a parte com a capacidade "
                "instalada do CNES. A taxa de admissao acima NAO e ocupacao: "
                "ela mede severidade dos casos notificados, nao pressao sobre "
                "a capacidade instalada."
            ),
        },
        "data_corte_analitica": cutoff.isoformat(),
    }

    if icu_known == 0:
        return _unavailable(
            ICU_ADMISSION_RATE,
            "Nenhum caso hospitalizado no periodo possui a informacao de "
            "internacao em UTI preenchida (1-Sim ou 2-Nao).",
            period=period,
            filters=filters,
            numerator=icu_yes,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=ICU_ADMISSION_RATE.key,
        value=_ratio(icu_yes, icu_known),
        numerator=icu_yes,
        denominator=icu_known,
        period=period,
        filters=filters.to_dict(),
        definition=ICU_ADMISSION_RATE,
        components=components,
        records_used=hospitalized,
    )


def icu_stay_cap(connection: Any, filters: AnalyticFilters | None = None) -> dict[str, Any]:
    """Teto de permanencia em UTI usado para limitar a imputacao.

    Uma estadia sem data de saida nem de evolucao nao pode ser tratada como
    "ainda em curso" indefinidamente: na base de referencia isso contava
    pacientes admitidos em 2025 como internados em agosto de 2026, e 93% do
    censo no dia de corte era imputado, com permanencia mediana de 163 dias
    contra 5 dias nas estadias com saida registrada.

    O teto vem da propria base -- o percentil configurado da permanencia das
    estadias que **tem** saida registrada -- ou de um valor fixo em
    `ICU_STAY_CAP_DAYS`. A origem do teto e publicada junto do censo.
    """
    settings = get_settings()
    if settings.icu_stay_cap_days is not None:
        return {"dias": settings.icu_stay_cap_days, "origem": "configurado (ICU_STAY_CAP_DAYS)"}

    filters = filters or AnalyticFilters()
    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT quantile_cont(date_diff('day', data_entrada_uti, data_saida_uti), ?),
               count(*)
        FROM {VIEW_ANALYTICS}
        WHERE estadia_uti_utilizavel
          AND data_saida_uti IS NOT NULL
          AND data_saida_uti >= data_entrada_uti
          AND {clause}
        """,
        [settings.icu_stay_cap_percentile, *parameters],
    ).fetchone()

    percentile, sample = row
    if percentile is None or int(sample) == 0:
        # Sem estadias completas no recorte nao ha base empirica: usa o teto
        # da base inteira, para nao deixar a imputacao sem limite.
        if filters.uf is None and filters.classification is None:
            return {"dias": 0, "origem": "sem estadias completas na base"}
        return icu_stay_cap(connection, AnalyticFilters())

    return {
        "dias": max(1, int(round(float(percentile)))),
        "origem": (
            f"percentil {settings.icu_stay_cap_percentile:.0%} da permanencia das "
            f"{int(sample)} estadias com saida registrada"
        ),
    }


#: Fragmento SQL que determina o fim de cada estadia em UTI.
#:
#: Ordem de preferencia: saida registrada; data de evolucao (alta ou obito e um
#: evento real, e a saida da UTI e anterior a ele); e, so entao, imputacao ate o
#: teto de permanencia.
#:
#: O teto limita **todos** os ramos imputados, nao apenas o ultimo. A versao
#: anterior aplicava `entrada + teto` so dentro do `coalesce`, de modo que uma
#: estadia sem `DT_SAIDUTI` mas com `DT_EVOLUCA` distante escapava do teto e era
#: contada ate a data de corte -- reintroduzindo, por outro caminho, exatamente
#: o defeito que o teto existe para corrigir. A saida registrada continua
#: soberana: ela e o dado, nao uma imputacao, e por isso nao e truncada.
_ICU_STAY_END_SQL = """
    least(
        coalesce(
            data_saida_uti,
            least(
                data_evolucao,
                data_entrada_uti + to_days(CAST(? AS INTEGER))
            ),
            data_entrada_uti + to_days(CAST(? AS INTEGER))
        ),
        CAST(? AS DATE)
    )
"""

#: Numero de parametros consumidos por :data:`_ICU_STAY_END_SQL`, na ordem:
#: teto (ramo da evolucao), teto (ramo em aberto), data de corte.
_ICU_STAY_END_PARAMS = 3


def icu_stay_completeness(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    """Mede como a permanencia em UTI foi determinada nas estadias do censo.

    A populacao avaliada e **exatamente** a que alimenta o censo: estadias que
    intersectam a janela, independentemente da data de sintomas. Medir outra
    populacao -- como se fazia antes -- publicava uma completude que nao
    descrevia o numero ao lado dela.
    """
    filters = filters or AnalyticFilters()
    start, cutoff, _ = _analysis_window(connection, window_days)
    cap = icu_stay_cap(connection, filters)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        WITH estadias AS (
            SELECT
                data_entrada_uti AS entrada,
                {_ICU_STAY_END_SQL} AS saida,
                data_saida_uti IS NOT NULL AS saida_real,
                data_saida_uti IS NULL AND data_evolucao IS NOT NULL AS por_evolucao,
                data_saida_uti IS NULL AND data_evolucao IS NULL AS em_aberto,
                -- Truncamento pelo teto, em qualquer ramo imputado: sem saida
                -- registrada e com o fim candidato (evolucao, ou o proprio
                -- corte quando nao ha evolucao) alem de `entrada + teto`.
                data_saida_uti IS NULL
                    AND coalesce(data_evolucao, CAST(? AS DATE))
                        > data_entrada_uti + to_days(CAST(? AS INTEGER))
                    AS truncada
            FROM {VIEW_ANALYTICS}
            WHERE estadia_uti_utilizavel AND {clause}
        )
        SELECT
            count(*),
            count(*) FILTER (WHERE saida_real),
            count(*) FILTER (WHERE por_evolucao),
            count(*) FILTER (WHERE em_aberto),
            count(*) FILTER (WHERE truncada)
        FROM estadias
        WHERE entrada <= ? AND saida >= ?
        """,
        [cap["dias"], cap["dias"], cutoff, cutoff, cap["dias"], *parameters, cutoff, start],
    ).fetchone()

    total, com_saida, por_evolucao, em_aberto, truncadas = (int(value) for value in row)
    return {
        "populacao": "estadias em UTI que intersectam a janela do censo",
        "estadias_no_censo": total,
        "saida_registrada": com_saida,
        "permanencia_imputada_pela_data_de_evolucao": por_evolucao,
        "permanencia_imputada_em_aberto": em_aberto,
        "imputadas_truncadas_pelo_teto": truncadas,
        "teto_de_permanencia_dias": cap["dias"],
        "origem_do_teto": cap["origem"],
        "percentual_com_saida_registrada": _ratio(com_saida, total) if total else None,
        "percentual_imputado_em_aberto": _ratio(em_aberto, total) if total else None,
        "efeito_da_imputacao": (
            "Estadias sem data de saida nem de evolucao sao tratadas como em "
            "curso ate o teto de permanencia. Sem o teto, pacientes admitidos "
            "meses antes contavam como internados ate a data de corte e "
            "inflavam o censo em ordem de grandeza."
        ),
    }


def icu_patient_census(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> list[dict[str, Any]]:
    """Serie diaria de pacientes de SRAG presentes em UTI.

    Cada ponto traz a contagem e a fracao dela que depende de imputacao
    (estadia sem saida registrada), para que o leitor saiba quanto do numero e
    observado e quanto e inferido -- inclusive no dia do pico.
    """
    filters = filters or AnalyticFilters()
    start, cutoff, _ = _analysis_window(connection, window_days)
    cap = icu_stay_cap(connection, filters)

    clause, parameters = filters.where_clause()
    rows = connection.execute(
        f"""
        WITH calendario AS (
            SELECT CAST(dia AS DATE) AS dia
            FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY) AS t(dia)
        ),
        estadias AS (
            SELECT
                data_entrada_uti AS entrada,
                {_ICU_STAY_END_SQL} AS saida,
                data_saida_uti IS NULL AS imputada
            FROM {VIEW_ANALYTICS}
            WHERE estadia_uti_utilizavel AND {clause}
        )
        SELECT calendario.dia,
               count(estadias.entrada),
               count(estadias.entrada) FILTER (WHERE estadias.imputada)
        FROM calendario
        LEFT JOIN estadias
          ON calendario.dia BETWEEN estadias.entrada AND estadias.saida
        GROUP BY calendario.dia
        ORDER BY calendario.dia
        """,
        [start, cutoff, cap["dias"], cap["dias"], cutoff, *parameters],
    ).fetchall()

    return [
        {
            "data": day.isoformat(),
            "pacientes_em_uti": int(count),
            "imputados": int(imputed),
            "percentual_imputado": _ratio(int(imputed), int(count)) if count else None,
        }
        for day, count, imputed in rows
    ]


# =============================================================================
# Indicador 3b -- Ocupacao de leitos de UTI (capacidade instalada externa)
# =============================================================================

#: Numero de UFs que a referencia de capacidade precisa cobrir para que o
#: indicador nacional seja publicado.
#:
#: A soma nacional de leitos so e comparavel com o censo nacional de pacientes
#: se o denominador cobrir o mesmo territorio do numerador. Com capacidade de
#: apenas parte das UFs a razao sairia inflada -- pacientes do Brasil inteiro
#: sobre os leitos de algumas UFs --, e essa e exatamente a ocupacao falsamente
#: alta que o indicador precisa nao produzir.
_REQUIRED_UF_COVERAGE: Final[int] = 27


def _month_distance(competence: str, reference: date) -> int:
    """Distancia em meses entre uma competencia `AAAA-MM` e uma data."""
    year, month = (int(part) for part in competence.split("-"))
    return abs((reference.year - year) * 12 + (reference.month - month))


def _icu_capacity(
    connection: Any, filters: AnalyticFilters, cutoff: date
) -> tuple[dict[str, Any] | None, str | None]:
    """Capacidade de UTI compativel com a janela, ou o motivo da indisponibilidade.

    A compatibilidade e verificada em tres dimensoes, nesta ordem, e cada falha
    tem motivo proprio: existencia da referencia, cobertura geografica e
    distancia temporal.

    Returns:
        Par `(capacidade, motivo)`. Exatamente um dos dois e `None`.
    """
    settings = get_settings()
    types = list(ICU_BED_TYPES_FOR_SRAG)
    placeholders = ", ".join("?" for _ in types)

    uf_clause, uf_parameters = ("uf = ?", [filters.uf]) if filters.uf else ("TRUE", [])
    rows = connection.execute(
        f"""
        SELECT competencia,
               sum(leitos_existentes)  AS existentes,
               sum(leitos_sus)         AS sus,
               count(DISTINCT uf)      AS ufs,
               any_value(fonte)        AS fonte,
               any_value(url)          AS url,
               max(data_extracao)      AS data_extracao
        FROM {TABLE_ICU_CAPACITY}
        WHERE tipo_leito IN ({placeholders}) AND {uf_clause}
        GROUP BY competencia
        ORDER BY competencia
        """,
        [*types, *uf_parameters],
    ).fetchall()

    scope = filters.uf or "Brasil"
    if not rows:
        return None, (
            "Referencia de capacidade instalada de UTI (CNES) nao carregada"
            + (f" para a UF {filters.uf}" if filters.uf else "")
            + ". Execute `python -m src.data.reference.icu_capacity` e recarregue "
            "o banco analitico. Sem denominador de leitos a ocupacao nao e "
            "calculavel -- a taxa de admissao em UTI e o censo diario continuam "
            "publicados e NAO sao ocupacao."
        )

    required_ufs = 1 if filters.uf else _REQUIRED_UF_COVERAGE
    covered = [row for row in rows if int(row[3]) >= required_ufs]
    if not covered:
        best = max(int(row[3]) for row in rows)
        return None, (
            f"Cobertura geografica insuficiente na referencia de capacidade: o "
            f"recorte {scope} exige {required_ufs} UF(s) e a melhor competencia "
            f"disponivel tem {best}. Um denominador parcial sobre um numerador "
            "nacional produziria ocupacao superestimada, entao o indicador "
            "permanece indisponivel."
        )

    chosen = min(covered, key=lambda row: _month_distance(str(row[0]), cutoff))
    competence = str(chosen[0])
    lag = _month_distance(competence, cutoff)
    if lag > settings.icu_capacity_max_lag_months:
        return None, (
            f"Periodo incompativel: a competencia de capacidade mais proxima "
            f"({competence}) esta a {lag} meses da data de corte analitica "
            f"({cutoff.isoformat()}), acima do limite de "
            f"{settings.icu_capacity_max_lag_months} meses "
            "(ICU_CAPACITY_MAX_LAG_MONTHS). Casar um censo com uma capacidade "
            "de outro periodo produziria um numero sem significado."
        )

    beds = int(chosen[1] or 0)
    if beds <= 0:
        return None, (
            f"A referencia de capacidade registra {beds} leito(s) de UTI para o "
            f"recorte {scope} na competencia {competence}. Sem leitos no "
            "denominador a ocupacao nao e definida (divisao por zero), e zero "
            "leitos com pacientes em UTI indica erro de cadastro na fonte."
        )

    return {
        "leitos_existentes": beds,
        "leitos_sus": int(chosen[2] or 0),
        "competencia": competence,
        "defasagem_meses": lag,
        "ufs_cobertas": int(chosen[3]),
        "ufs_exigidas": required_ufs,
        "tipos_de_leito": types,
        "fonte": chosen[4],
        "url": chosen[5],
        "data_extracao": chosen[6],
        "competencias_disponiveis": sorted(str(row[0]) for row in rows),
    }, None


def icu_bed_occupancy_rate(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Ocupacao de leitos de UTI por pacientes de SRAG.

    ``ocupacao_uti_pct = pacientes_srag_em_uti_no_dia / leitos_uti_disponiveis_no_dia * 100``

    O numerador e o censo diario calculado sobre o SIVEP-Gripe; o denominador e
    a capacidade instalada do CNES para a UF e a competencia compativel com a
    janela. O valor publicado e o do **dia de pico do censo maduro** -- o mesmo
    dia ja auditado em :func:`icu_metrics` --, e a serie diaria completa
    acompanha o resultado.

    Este indicador e distinto da taxa de admissao em UTI (severidade dos casos)
    e do censo (contagem absoluta de pacientes). Os tres convivem e nenhum e
    renomeado como o outro.
    """
    filters = filters or AnalyticFilters()
    _, cutoff, window = _analysis_window(connection, window_days)

    # --- Janela madura deslocada, e nao apenas truncada ----------------------
    #
    # A cauda recente do censo e contaminada por construcao: quanto mais recente
    # o dia, menos saidas foram digitadas e mais estadias seguem imputadas. So a
    # parte anterior ao corte menos o teto de permanencia e apuravel.
    #
    # Truncar a janela de analise nessa fronteira nao funciona: na base real o
    # teto medido e de 31 dias e a janela tem 30, de modo que NENHUM dia
    # sobreviveria e a ocupacao ficaria permanentemente indisponivel -- um
    # indicador que nunca sai nao e um indicador. A janela e entao **deslocada**
    # para tras, preservando o mesmo tamanho: `window` dias madilhos encerrados
    # em `cutoff - teto`. O periodo publicado e esse, e nao o dos demais
    # indicadores, e a diferenca vem declarada no resultado.
    cap_days = icu_stay_cap(connection, filters)["dias"]
    mature_until = cutoff - timedelta(days=cap_days)
    mature_start = mature_until - timedelta(days=window - 1)
    period = _period(
        mature_start,
        mature_until,
        f"{window} dias encerrados {cap_days} dias antes da data de corte "
        "analitica, para que as saidas de UTI ja estejam digitadas",
    )

    # O censo cobre da janela madura ate o corte: a parte recente nao entra no
    # valor publicado, mas fica na serie, rotulada, para comparacao.
    census = icu_patient_census(connection, filters, window_days=window + cap_days)
    mature = [
        point
        for point in census
        if mature_start <= date.fromisoformat(point["data"]) <= mature_until
    ]

    capacity, reason = _icu_capacity(connection, filters, cutoff)
    components: dict[str, Any] = {
        "formula": ("pacientes_srag_em_uti_no_dia / leitos_uti_disponiveis_no_dia * 100"),
        "capacidade_instalada": capacity,
        "janela_madura": {
            "inicio": mature_start.isoformat(),
            "fim": mature_until.isoformat(),
            "deslocamento_dias": cap_days,
            "motivo_do_deslocamento": (
                "a cauda recente do censo depende de saidas ainda nao digitadas; "
                f"o teto de permanencia medido ({cap_days} dias) e o tempo "
                "necessario para que a estadia ja esteja encerrada no registro"
            ),
            "nota": (
                "este periodo NAO coincide com o dos demais indicadores, que vao "
                f"ate {cutoff.isoformat()}. Comparar a ocupacao com eles exige "
                "levar o deslocamento em conta"
            ),
        },
        "criterio_do_dia_publicado": ("dia de pico do censo dentro da janela madura deslocada"),
        "data_corte_analitica": cutoff.isoformat(),
    }

    if capacity is None:
        return _unavailable(
            ICU_BED_OCCUPANCY_RATE,
            reason or "Capacidade instalada de UTI indisponivel.",
            period=period,
            filters=filters,
            numerator=None,
            denominator=None,
            components=components,
        )

    beds = capacity["leitos_existentes"]
    peak = max(mature, key=lambda point: point["pacientes_em_uti"], default=None)
    if peak is None:
        return _unavailable(
            ICU_BED_OCCUPANCY_RATE,
            "Nao ha nenhum dia com censo apuravel na janela madura "
            f"({mature_start.isoformat()} a {mature_until.isoformat()}): a base "
            "nao cobre esse periodo. Sem numerador nao ha ocupacao.",
            period=period,
            filters=filters,
            numerator=None,
            denominator=beds,
            components=components,
        )

    daily = [
        {
            "data": point["data"],
            "pacientes_em_uti": point["pacientes_em_uti"],
            "ocupacao_pct": _ratio(point["pacientes_em_uti"], beds),
            "percentual_imputado": point["percentual_imputado"],
        }
        for point in mature
    ]
    occupancies = [point["ocupacao_pct"] for point in daily]

    components.update(
        {
            "data_do_valor_publicado": peak["data"],
            "pacientes_em_uti_no_dia": peak["pacientes_em_uti"],
            "leitos_disponiveis_no_dia": beds,
            "percentual_do_numerador_imputado": peak["percentual_imputado"],
            "ocupacao_media_na_janela_madura_pct": (
                round(sum(occupancies) / len(occupancies), _PERCENT_DECIMALS)
                if occupancies
                else None
            ),
            "ocupacao_minima_na_janela_madura_pct": min(occupancies) if occupancies else None,
            "dias_apurados": len(daily),
            "serie_diaria_de_ocupacao": daily,
            "leitos_sus_no_denominador": capacity["leitos_sus"],
            "alcance_do_indicador": (
                "parcela da capacidade de UTI ocupada por pacientes de SRAG "
                "notificados; e um piso da ocupacao total, que inclui pacientes "
                "sem SRAG"
            ),
        }
    )

    return MetricResult(
        metric=ICU_BED_OCCUPANCY_RATE.key,
        value=_ratio(peak["pacientes_em_uti"], beds),
        numerator=peak["pacientes_em_uti"],
        denominator=beds,
        period=period,
        filters=filters.to_dict(),
        definition=ICU_BED_OCCUPANCY_RATE,
        components=components,
        records_used=sum(point["pacientes_em_uti"] for point in mature),
    )


# =============================================================================
# Indicador 4 -- Vacinacao
# =============================================================================


def vaccination_metrics(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Cobertura vacinal declarada entre casos notificados de SRAG.

    Mede a cobertura **entre quem adoeceu e foi notificado** -- um grupo com
    vies de selecao por definicao, e nao a populacao. A cobertura populacional e
    calculada a parte, a partir da referencia externa do SI-PNI, e viaja em
    `components` sob nome proprio: a aproximacao nunca e apresentada sob o nome
    do indicador original, mesmo quando a referencia externa falta.
    """
    filters = filters or AnalyticFilters()
    window = window_days or get_settings().growth_window_days
    cutoff = analysis_cutoff(connection)
    start = cutoff - timedelta(days=window - 1)

    clause, parameters = filters.where_clause()
    row = connection.execute(
        f"""
        SELECT
            count(*)                                                   AS casos,
            count(*) FILTER (WHERE vacina_covid_informada)             AS covid_informado,
            count(*) FILTER (WHERE vacinado_covid)                     AS covid_sim,
            count(*) FILTER (WHERE VACINA_COV = 9)                     AS covid_ignorado,
            count(*) FILTER (WHERE VACINA_COV IS NULL)                 AS covid_ausente,
            count(*) FILTER (WHERE vacina_influenza_informada)         AS influenza_informado,
            count(*) FILTER (WHERE vacinado_influenza)                 AS influenza_sim,
            count(*) FILTER (WHERE VACINA = 9)                         AS influenza_ignorado,
            count(*) FILTER (WHERE VACINA IS NULL)                     AS influenza_ausente
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ? AND {clause}
        """,
        [start, cutoff, *parameters],
    ).fetchone()

    (
        total,
        covid_known,
        covid_yes,
        covid_ignored,
        covid_absent,
        flu_known,
        flu_yes,
        flu_ignored,
        flu_absent,
    ) = (int(value) for value in row)

    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")
    components = {
        "casos_no_periodo": total,
        "covid19": {
            "com_informacao": covid_known,
            "vacinados": covid_yes,
            "ignorado": covid_ignored,
            "nao_informado": covid_absent,
            "cobertura_declarada_pct": _ratio(covid_yes, covid_known) if covid_known else None,
            "completude_da_informacao_pct": _ratio(covid_known, total) if total else None,
        },
        "influenza": {
            "com_informacao": flu_known,
            "vacinados": flu_yes,
            "ignorado": flu_ignored,
            "nao_informado": flu_absent,
            "cobertura_declarada_pct": _ratio(flu_yes, flu_known) if flu_known else None,
            "completude_da_informacao_pct": _ratio(flu_known, total) if total else None,
        },
        "taxa_de_vacinacao_da_populacao": population_vaccination_coverage(
            connection, filters, cutoff.year
        ),
        "data_corte_analitica": cutoff.isoformat(),
    }

    if covid_known == 0:
        return _unavailable(
            VACCINATION_COVERAGE,
            "Nenhum caso do periodo possui a informacao de vacinacao contra "
            "covid-19 preenchida (1-Sim ou 2-Nao).",
            period=period,
            filters=filters,
            numerator=covid_yes,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=VACCINATION_COVERAGE.key,
        value=_ratio(covid_yes, covid_known),
        numerator=covid_yes,
        denominator=covid_known,
        period=period,
        filters=filters.to_dict(),
        definition=VACCINATION_COVERAGE,
        components=components,
        records_used=total,
    )


def population_vaccination_coverage(
    connection: Any, filters: AnalyticFilters, year: int
) -> dict[str, Any]:
    """Cobertura vacinal da populacao, a partir da referencia externa do SI-PNI.

    Devolve um bloco por campanha (`influenza`, `covid19`) com valor, numerador,
    denominador, ano e fonte -- ou `value: None` com o motivo quando a
    referencia nao foi fornecida. A classificacao final nao recorta este bloco:
    cobertura vacinal e atributo da populacao, nao dos casos.
    """
    base = {
        "formula": "doses_aplicadas / populacao_alvo * 100",
        "definition": POPULATION_VACCINATION_COVERAGE.definition,
        "limitations": list(POPULATION_VACCINATION_COVERAGE.limitations),
        "source": POPULATION_VACCINATION_COVERAGE.source,
        # As duas campanhas nunca sao somadas: publico-alvo, esquema de doses e
        # sazonalidade sao diferentes, e uma cobertura agregada das duas nao
        # descreveria nenhuma populacao real.
        "campanhas_distintas": sorted(CAMPAIGNS),
    }
    unavailable = {
        **base,
        "value": None,
        "unavailable_reason": (
            "Referencia de doses aplicadas (SI-PNI) nao fornecida em "
            "data/reference/cobertura_vacinal_uf.csv. O SIVEP-Gripe so contem a "
            "informacao vacinal de pessoas notificadas com SRAG, que nao representa "
            "a populacao; sem a referencia externa o indicador nao e calculavel. "
            "Gere-a com `python -m src.data.reference.vaccination --from-pni "
            "<extratos> --year <ano>`."
        ),
    }

    row = connection.execute(
        f"SELECT ano FROM {TABLE_VACCINATION} ORDER BY abs(ano - ?) LIMIT 1", [year]
    ).fetchone()
    if row is None:
        return unavailable
    reference_year = int(row[0])

    uf_clause, uf_parameters = ("uf = ?", [filters.uf]) if filters.uf else ("TRUE", [])
    rows = connection.execute(
        f"""
        SELECT campanha, sum(doses_aplicadas), sum(populacao_alvo),
               count(*) FILTER (WHERE populacao_alvo IS NULL),
               string_agg(DISTINCT fonte, '; '),
               string_agg(DISTINCT url, '; '),
               max(data_extracao),
               count(DISTINCT uf),
               bool_and(coalesce(periodo_completo, FALSE))
        FROM {TABLE_VACCINATION}
        WHERE ano = ? AND {uf_clause}
        GROUP BY campanha
        """,
        [reference_year, *uf_parameters],
    ).fetchall()
    if not rows:
        return {
            **unavailable,
            "ano_da_referencia": reference_year,
            "unavailable_reason": (
                f"A referencia de doses aplicadas nao cobre o recorte "
                f"{filters.uf or 'nacional'} no ano {reference_year}."
            ),
        }

    population, population_year = _reference_population(connection, filters, reference_year)
    campaigns: dict[str, Any] = {}
    for row in rows:
        campaign, doses, target, without_target, source, url, extracted, ufs, period_complete = row
        doses = int(doses or 0)
        # --- Completude temporal, verificada ANTES do denominador ---------------
        #
        # Um extrato mensal isolado do SI-PNI nao e cobertura anual nem
        # populacional: dividir doses de um unico mes pela populacao do ano
        # inteiro produz um percentual sem lastro epidemiologico, nao uma
        # aproximacao dela. A declaracao de completude e do operador que gerou
        # a referencia (`--periodo-completo`), nunca inferida aqui a partir da
        # contagem de linhas -- ver contrato do arquivo em
        # `src/data/reference/vaccination.py`.
        if not bool(period_complete):
            campaigns[campaign] = {
                "value": None,
                "numerator": doses,
                "unavailable_reason": (
                    "A referencia de doses aplicadas para esta campanha nao declara "
                    "cobertura de periodo completo (`periodo_completo=true`); os "
                    f"registros disponiveis somam {doses} doses, mas podem ser um "
                    "extrato mensal isolado do SI-PNI. Um recorte parcial dividido "
                    "pela populacao do ano nao e cobertura vacinal anual nem "
                    "populacional -- e um numero sem significado epidemiologico. "
                    "Agregue todos os extratos mensais da campanha com "
                    "`python -m src.data.reference.vaccination --from-pni ... "
                    "--periodo-completo` para habilitar este indicador."
                ),
            }
            continue
        if target is not None and int(without_target) == 0:
            denominator = int(target)
            denominator_label = "populacao-alvo da campanha (SI-PNI)"
            denominator_source = "SI-PNI"
        elif population:
            # Substituicao do denominador: legitima quando a populacao-alvo nao
            # e publicada, mas nunca silenciosa -- o rotulo, a justificativa e o
            # sentido do vies acompanham o numero.
            denominator = population
            denominator_label = f"populacao residente total (IBGE {population_year})"
            denominator_source = "IBGE"
        else:
            campaigns[campaign] = {
                "value": None,
                "unavailable_reason": (
                    "Sem populacao-alvo na referencia e sem populacao do IBGE "
                    "carregada; nao ha denominador possivel para esta campanha."
                ),
            }
            continue
        campaigns[campaign] = {
            "value": _ratio(doses, denominator) if denominator else None,
            "numerator": doses,
            "denominator": denominator,
            "denominador_descricao": denominator_label,
            "denominador_fonte": denominator_source,
            "justificativa_do_denominador": (
                "publico-alvo publicado pela campanha"
                if denominator_source == "SI-PNI"
                else (
                    "populacao-alvo nao publicada para esta campanha; usada a "
                    "populacao residente do IBGE, o que SUBESTIMA a cobertura do "
                    "publico-alvo por ampliar o denominador"
                )
            ),
            "ufs_na_referencia": int(ufs),
            "fonte": source,
            "url": url,
            "data_extracao": extracted,
            "periodo_completo": True,
        }

    return {
        **base,
        "ano_da_referencia": reference_year,
        "campanhas": campaigns,
    }


# =============================================================================
# Indicador complementar -- Incidencia por 100 mil habitantes
# =============================================================================


def _reference_population(
    connection: Any, filters: AnalyticFilters, year: int
) -> tuple[int | None, int | None]:
    """Populacao de referencia (IBGE) para o recorte, no ano mais proximo.

    Returns:
        Par `(populacao, ano_usado)`; `(None, None)` quando a tabela de referencia
        esta vazia -- a referencia nao foi carregada.
    """
    row = connection.execute(
        f"SELECT ano FROM {TABLE_POPULATION} ORDER BY abs(ano - ?) LIMIT 1", [year]
    ).fetchone()
    if row is None:
        return None, None
    reference_year = int(row[0])

    if filters.uf is not None:
        population = connection.execute(
            f"SELECT sum(populacao) FROM {TABLE_POPULATION} WHERE ano = ? AND uf = ?",
            [reference_year, filters.uf],
        ).fetchone()[0]
    else:
        population = connection.execute(
            f"SELECT sum(populacao) FROM {TABLE_POPULATION} WHERE ano = ?", [reference_year]
        ).fetchone()[0]
    return (int(population) if population else None), reference_year


def incidence_rate(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Casos por 100 mil habitantes residentes na janela analisada.

    O denominador vem da tabela `populacao_uf` (estimativa do IBGE carregada em
    `data/reference/`). A classificacao final recorta o numerador, nao o
    denominador: a incidencia de covid-19 continua sendo por habitante.

    O recorte geografico aqui e a UF de **residencia** (`SG_UF`), e nao a de
    notificacao, porque o denominador do IBGE e populacao **residente**. Usar a
    UF de notificacao compara universos diferentes: um paciente que mora em GO e
    interna no DF entra no numerador do DF e no denominador de GO. Foi medido
    que 2.807 registros (1,70%) tem residencia e notificacao em UFs distintas, e
    o erro nao e uniforme -- concentra-se nas UFs com fluxo assistencial
    liquido, com vies da ordem de 20 a 25% nelas.

    Os registros sem UF de residencia ficam fora do numerador quando ha recorte
    por UF, e o volume e publicado nos componentes.
    """
    filters = filters or AnalyticFilters()
    start, cutoff, window = _analysis_window(connection, window_days)

    clause, parameters = filters.where_clause(uf_dimension="SG_UF")
    row = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE {clause}),
            count(*) FILTER (WHERE uf_residencia IS NULL)
        FROM {VIEW_ANALYTICS}
        WHERE data_sintomas BETWEEN ? AND ?
        """,
        [*parameters, start, cutoff],
    ).fetchone()
    cases, without_residence = int(row[0]), int(row[1])
    population, reference_year = _reference_population(connection, filters, cutoff.year)

    period = _period(start, cutoff, f"ultimos {window} dias ate a data de corte analitica")
    components = {
        "casos_na_janela": cases,
        # A escala e parte do resultado: sem ela, "por 100 mil habitantes" na
        # redacao seria um numero sem lastro para o guardrail de evidencia.
        "escala": {"por_habitantes": 100_000, "em_milhares": 100},
        "dimensao_geografica": "UF de residencia (SG_UF)",
        "casos_sem_uf_de_residencia_na_janela": without_residence,
        "populacao_de_referencia": population,
        "ano_da_estimativa_populacional": reference_year,
        "fonte_da_populacao": "IBGE - populacao residente estimada (tabela 6579)",
        "data_corte_analitica": cutoff.isoformat(),
    }

    if population is None:
        return _unavailable(
            INCIDENCE_RATE,
            "Referencia populacional do IBGE nao carregada no banco analitico. "
            "Execute `python -m src.data.reference.population` e recarregue a base.",
            period=period,
            filters=filters,
            numerator=cases,
            denominator=None,
            components=components,
        )

    return MetricResult(
        metric=INCIDENCE_RATE.key,
        value=round(cases / population * 100_000, _PERCENT_DECIMALS),
        numerator=cases,
        denominator=population,
        period=period,
        filters=filters.to_dict(),
        definition=INCIDENCE_RATE,
        components=components,
        records_used=cases,
    )


# =============================================================================
# Indicador complementar -- Baseline sazonal
# =============================================================================

#: Anos excluidos do baseline por definicao, com o motivo publicado.
PANDEMIC_YEARS: tuple[int, ...] = (2020, 2021)


def _shift_year(day: date, delta_years: int) -> date:
    """Move a data `delta_years` anos, preservando mes e dia (29/02 vira 28/02)."""
    target_year = day.year + delta_years
    try:
        return day.replace(year=target_year)
    except ValueError:
        return day.replace(year=target_year, day=28)


def _years_present(
    connection: Any,
    years: list[int],
    filters: AnalyticFilters | None = None,
    window: tuple[date, date] | None = None,
) -> list[int]:
    """Anos de baseline com ao menos um caso na **janela comparada** do recorte.

    O recorte importa. Sem ele, um ano com casos no Brasil mas nenhum no estado
    filtrado seria considerado "presente" e entraria na mediana do baseline com
    valor zero -- derrubando a mediana e inflando o excesso sazonal, ou tornando
    o indicador nao calculavel por "mediana zero" quando o correto seria
    declarar o ano ausente. O efeito e maior justamente nas UFs pequenas, onde o
    alerta mais importa.

    A **janela** importa tanto quanto o recorte. Testar apenas "tem caso no ano
    civil" deixa passar o ano que existe na base por um punhado de registros
    fora da janela comparada: na base de referencia, 2024 aparece somente com
    registros de 29 a 31 de dezembro, e ainda assim era considerado presente e
    entrava na mediana com **zero** casos na janela de maio-junho. Uma mediana
    puxada para zero infla o excesso sazonal ou torna o indicador nao
    calculavel, quando o correto e declarar o ano ausente.

    Args:
        connection: conexao com o banco analitico.
        years: anos candidatos a baseline.
        filters: recorte de UF/classificacao; sem ele, considera a base inteira.
        window: par `(inicio, fim)` da janela atual, deslocado para cada ano
            candidato. Sem ele, basta ter caso no ano civil.

    Returns:
        Anos com ao menos um caso na janela comparada do recorte, em ordem
        crescente.
    """
    if not years:
        return []
    filters = filters or AnalyticFilters()
    clause, parameters = filters.where_clause()

    if window is None:
        placeholders = ", ".join("?" for _ in years)
        rows = connection.execute(
            f"""
            SELECT DISTINCT year(data_sintomas)
            FROM {VIEW_ANALYTICS}
            WHERE year(data_sintomas) IN ({placeholders}) AND {clause}
            """,
            [*years, *parameters],
        ).fetchall()
        return sorted(int(row[0]) for row in rows)

    start, end = window
    present: list[int] = []
    for year in sorted(years):
        delta = year - end.year
        count = connection.execute(
            f"""
            SELECT count(*) FROM {VIEW_ANALYTICS}
            WHERE data_sintomas BETWEEN ? AND ? AND {clause}
            """,
            [_shift_year(start, delta), _shift_year(end, delta), *parameters],
        ).fetchone()[0]
        if int(count) > 0:
            present.append(year)
    return present


def seasonal_baseline(
    connection: Any,
    filters: AnalyticFilters | None = None,
    window_days: int | None = None,
) -> MetricResult:
    """Compara a janela atual com a mesma janela de calendario em anos anteriores.

    A referencia e a **mediana** dos anos de baseline presentes na base, menos
    sensivel a um ano atipico do que a media. Os anos configurados, os presentes,
    os ausentes e os excluidos por definicao sao todos publicados nos
    componentes: o leitor sabe exatamente contra o que a janela foi comparada.
    """
    filters = filters or AnalyticFilters()
    settings = get_settings()
    start, cutoff, window = _analysis_window(connection, window_days)

    configured = sorted(set(settings.baseline_years) - set(PANDEMIC_YEARS))
    excluded_by_config = sorted(set(settings.baseline_years) & set(PANDEMIC_YEARS))
    present = [
        year
        for year in _years_present(connection, configured, filters, window=(start, cutoff))
        if year < cutoff.year
    ]
    absent = sorted(set(configured) - set(present))

    clause, parameters = filters.where_clause()

    def count_between(first: date, last: date) -> int:
        return int(
            connection.execute(
                f"""
                SELECT count(*) FROM {VIEW_ANALYTICS}
                WHERE data_sintomas BETWEEN ? AND ? AND {clause}
                """,
                [first, last, *parameters],
            ).fetchone()[0]
        )

    current = count_between(start, cutoff)
    by_year: dict[str, dict[str, Any]] = {}
    for year in present:
        delta = year - cutoff.year
        first, last = _shift_year(start, delta), _shift_year(cutoff, delta)
        by_year[str(year)] = {
            "inicio": first.isoformat(),
            "fim": last.isoformat(),
            "casos": count_between(first, last),
        }

    counts = sorted(item["casos"] for item in by_year.values())
    median = statistics.median(counts) if counts else None
    mean = round(statistics.fmean(counts), 1) if counts else None

    period = _period(
        start,
        cutoff,
        f"janela atual de {window} dias comparada a mesma janela de calendario em "
        f"{len(present)} ano(s) de baseline",
    )
    components = {
        "casos_na_janela_atual": current,
        "mediana_do_baseline": median,
        "media_do_baseline": mean,
        "casos_por_ano_de_baseline": by_year,
        "anos_configurados": sorted(settings.baseline_years),
        "anos_considerados": present,
        "anos_ausentes_na_base": absent,
        "anos_excluidos_por_definicao": {
            "anos": list(PANDEMIC_YEARS),
            "motivo": (
                "anos pandemicos de covid-19: volume de SRAG fora de qualquer padrao "
                "sazonal, incompativel com um baseline"
            ),
            "estavam_na_configuracao": excluded_by_config,
        },
        "minimo_de_anos_exigido": settings.baseline_min_years,
        "razao_atual_sobre_mediana": (
            round(current / median, 3) if median not in (None, 0) else None
        ),
        "data_corte_analitica": cutoff.isoformat(),
    }

    if len(present) < settings.baseline_min_years:
        return _unavailable(
            SEASONAL_BASELINE,
            f"Apenas {len(present)} ano(s) de baseline presente(s) na base "
            f"({present or 'nenhum'}); o minimo configurado e "
            f"{settings.baseline_min_years}. Carregue mais anos com "
            "`python main.py --setup --years ...` para habilitar a comparacao sazonal.",
            period=period,
            filters=filters,
            numerator=None,
            denominator=None,
            components=components,
        )

    if not median:
        return _unavailable(
            SEASONAL_BASELINE,
            "A mediana do baseline e zero: nao ha casos na mesma janela dos anos "
            "de referencia, e a variacao percentual e indefinida.",
            period=period,
            filters=filters,
            numerator=current,
            denominator=0,
            components=components,
        )

    return MetricResult(
        metric=SEASONAL_BASELINE.key,
        value=round((current - median) / median * 100, _PERCENT_DECIMALS),
        numerator=int(round(current - median)),
        denominator=int(round(median)),
        period=period,
        filters=filters.to_dict(),
        definition=SEASONAL_BASELINE,
        components=components,
        records_used=current + sum(counts),
    )
