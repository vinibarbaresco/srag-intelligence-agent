"""Definicoes formais dos indicadores epidemiologicos.

Este modulo e o contrato entre o dado bruto e o numero publicado. Cada
indicador declara explicitamente numerador, denominador, campo de origem,
periodo, tratamento de ausencia e limitacoes -- conforme exigido pela
especificacao do desafio. As definicoes sao dados, nao texto solto: o relatorio
final e a documentacao sao gerados a partir delas, o que impede que a descricao
publicada divirja do calculo executado.

Nenhuma metrica e inventada. Quando o dataset nao permite calcular o indicador
pedido, isso e declarado em :attr:`MetricDefinition.not_computable_reason` e a
aproximacao adotada e nomeada de forma honesta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from src.config import DATASUS_SOURCE_LABEL


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """Especificacao formal e auditavel de um indicador."""

    key: str
    name: str
    definition: str
    numerator: str
    denominator: str
    fields: tuple[str, ...]
    period: str
    missing_data_handling: str
    limitations: tuple[str, ...]
    unit: str = "%"
    source: str = DATASUS_SOURCE_LABEL
    not_computable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "definition": self.definition,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "fields": list(self.fields),
            "period": self.period,
            "missing_data_handling": self.missing_data_handling,
            "limitations": list(self.limitations),
            "unit": self.unit,
            "source": self.source,
            "not_computable_reason": self.not_computable_reason,
        }


@dataclass(slots=True)
class MetricResult:
    """Envelope de retorno de toda tool de metrica.

    O campo `value` e `None` sempre que o indicador nao puder ser calculado com
    seguranca -- e nesse caso `unavailable_reason` explica o motivo. Nenhuma
    camada superior substitui `None` por zero ou por estimativa (Guardrail 6).
    """

    metric: str
    value: float | None
    numerator: int | None
    denominator: int | None
    period: dict[str, Any]
    filters: dict[str, Any]
    definition: MetricDefinition
    components: dict[str, Any] = field(default_factory=dict)
    unavailable_reason: str | None = None
    #: Registros da base que entraram no calculo antes de qualquer exclusao
    #: por codigo de ausencia -- a base de referencia do indicador.
    records_used: int | None = None

    @property
    def available(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "unit": self.definition.unit,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "records_used": self.records_used,
            "period": self.period,
            "filters": self.filters,
            "components": self.components,
            "source": self.definition.source,
            "definition": self.definition.definition,
            "limitations": list(self.definition.limitations),
            "unavailable_reason": self.unavailable_reason,
        }


# =============================================================================
# Nota transversal sobre atraso de notificacao
# =============================================================================

_REPORTING_LAG_NOTE: Final[str] = (
    "A serie recente e incompleta por atraso de notificacao: casos com sintomas "
    "nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais "
    "recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de "
    "digitacao da base, nunca a data de hoje."
)

_ANALYTIC_SCOPE_NOTE: Final[str] = (
    "O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente "
    "hospitalizados. Nenhum indicador aqui representa a populacao geral."
)


# =============================================================================
# Indicador 1 -- Taxa de aumento de casos
# =============================================================================

CASE_GROWTH_RATE = MetricDefinition(
    key="case_growth_rate",
    name="Taxa de aumento de casos",
    definition=(
        "Variacao percentual do numero de casos de SRAG entre duas janelas "
        "consecutivas de mesmo tamanho, medidas pela data dos primeiros sintomas: "
        "(casos_periodo_atual - casos_periodo_anterior) / casos_periodo_anterior x 100."
    ),
    numerator="casos com DT_SIN_PRI na janela atual menos casos na janela anterior",
    denominator="casos com DT_SIN_PRI na janela anterior",
    fields=("DT_SIN_PRI", "DT_DIGITA"),
    period="duas janelas consecutivas de GROWTH_WINDOW_DAYS dias (padrao: 30)",
    missing_data_handling=(
        "Registros sem DT_SIN_PRI ou com linha do tempo inconsistente ficam fora "
        "da view analitica e sao contabilizados no relatorio de qualidade."
    ),
    limitations=(
        _REPORTING_LAG_NOTE,
        "Mede variacao de casos notificados, nao incidencia populacional: nao ha "
        "denominador populacional no dataset.",
        "Quando a janela anterior tem zero casos, a variacao percentual e "
        "indefinida e o indicador retorna valor nulo.",
        _ANALYTIC_SCOPE_NOTE,
    ),
)


# =============================================================================
# Indicador 2 -- Taxa de mortalidade
# =============================================================================

MORTALITY_RATE = MetricDefinition(
    key="mortality_rate",
    name="Taxa de mortalidade por SRAG (letalidade entre casos encerrados)",
    definition=(
        "Proporcao de obitos por SRAG entre os casos encerrados elegiveis: "
        "obitos (EVOLUCAO = 2) / casos encerrados (EVOLUCAO em 1, 2 ou 3) x 100."
    ),
    numerator="casos com EVOLUCAO = 2 (Obito por SRAG)",
    denominator="casos com EVOLUCAO em (1-Cura, 2-Obito, 3-Obito por outras causas)",
    fields=("EVOLUCAO", "DT_SIN_PRI"),
    period="casos com primeiros sintomas na janela analisada",
    missing_data_handling=(
        "EVOLUCAO = 9 (Ignorado) e EVOLUCAO nulo ficam fora do numerador e do "
        "denominador. Casos ainda em aberto tambem nao entram no denominador, "
        "para nao subestimar a letalidade."
    ),
    limitations=(
        "Trata-se de letalidade (case fatality ratio) entre casos notificados de "
        "SRAG, nao de mortalidade populacional por SRAG.",
        "Casos recentes ainda sem encerramento ficam fora do denominador. Como "
        "obitos costumam ser encerrados antes das curas, a letalidade da janela "
        "recente tende a ser SUPERESTIMADA; o percentual de casos em aberto e "
        "publicado junto do indicador para dimensionar esse vies.",
        "EVOLUCAO = 3 (obito por outras causas) entra no denominador como caso "
        "encerrado, mas nao no numerador.",
        _REPORTING_LAG_NOTE,
        _ANALYTIC_SCOPE_NOTE,
    ),
)


# =============================================================================
# Indicador 3 -- UTI
# =============================================================================

ICU_ADMISSION_RATE = MetricDefinition(
    key="icu_admission_rate",
    name="Taxa de admissao em UTI entre hospitalizados por SRAG",
    definition=(
        "Proporcao de pacientes hospitalizados por SRAG que foram internados em "
        "UTI: UTI = 1 / (UTI em 1 ou 2), restrito a HOSPITAL = 1."
    ),
    numerator="hospitalizados com UTI = 1 (Sim)",
    denominator="hospitalizados com UTI informado (1-Sim ou 2-Nao)",
    fields=("UTI", "HOSPITAL", "DT_SIN_PRI"),
    period="casos com primeiros sintomas na janela analisada",
    missing_data_handling=(
        "UTI = 9 (Ignorado) e UTI nulo sao excluidos do numerador e do "
        "denominador, e o volume de ignorados e reportado junto do resultado."
    ),
    limitations=(
        "ATENCAO: este indicador NAO e taxa de ocupacao de leitos de UTI. O campo "
        "53 do SIVEP-Gripe ('Internado em UTI?') registra se houve admissao em "
        "UTI, nao a ocupacao da capacidade instalada.",
        "Mede severidade clinica dos casos notificados, nao pressao sobre a rede hospitalar.",
        _REPORTING_LAG_NOTE,
        _ANALYTIC_SCOPE_NOTE,
    ),
)

ICU_BED_OCCUPANCY_RATE = MetricDefinition(
    key="icu_bed_occupancy_rate",
    name="Taxa de ocupacao de leitos de UTI",
    definition=(
        "Proporcao de leitos de UTI ocupados em relacao a capacidade instalada: "
        "leitos_ocupados / leitos_totais x 100."
    ),
    numerator="leitos de UTI ocupados",
    denominator="leitos de UTI disponiveis (capacidade instalada)",
    fields=(),
    period="nao aplicavel",
    missing_data_handling="nao aplicavel",
    limitations=(
        "O dataset SRAG do SIVEP-Gripe nao contem capacidade instalada de leitos "
        "nem contagem de leitos ocupados por unidade de saude.",
        "Um denominador de capacidade exigiria fonte externa (CNES / leitos "
        "habilitados), fora do escopo desta PoC.",
    ),
    not_computable_reason=(
        "Nao e possivel calcular taxa de ocupacao de UTI com os dados disponiveis: "
        "o SIVEP-Gripe nao registra capacidade instalada nem leitos ocupados. "
        "Como aproximacao, sao reportados a taxa de admissao em UTI entre "
        "hospitalizados e o censo diario de pacientes de SRAG em UTI."
    ),
)

ICU_PATIENT_CENSUS = MetricDefinition(
    key="icu_patient_census",
    name="Censo diario de pacientes de SRAG em UTI",
    definition=(
        "Numero de pacientes de SRAG presentes em UTI em cada dia, contado como "
        "DT_ENTUTI <= dia <= coalesce(DT_SAIDUTI, DT_EVOLUCA, data de referencia)."
    ),
    numerator="pacientes de SRAG com permanencia em UTI cobrindo o dia",
    denominator="nao aplicavel (contagem absoluta, nao proporcao)",
    fields=("DT_ENTUTI", "DT_SAIDUTI", "DT_EVOLUCA"),
    period="serie diaria na janela analisada",
    missing_data_handling=(
        "Registros com UTI = 1 mas sem DT_ENTUTI nao entram no censo e sao "
        "contabilizados a parte, pois a permanencia e desconhecida."
    ),
    limitations=(
        "E um censo de pacientes, nao uma taxa de ocupacao: nao ha denominador de leitos.",
        "Pacientes sem data de saida registrada tem a permanencia imputada ate a "
        "data de evolucao ou ate a data de referencia, o que superestima o censo "
        "nos dias mais recentes.",
        "Cobre apenas pacientes de SRAG notificados, nao a ocupacao total da UTI.",
    ),
    unit="pacientes",
)


# =============================================================================
# Indicador 4 -- Vacinacao
# =============================================================================

VACCINATION_COVERAGE = MetricDefinition(
    key="vaccination_coverage_among_cases",
    name="Cobertura vacinal declarada entre casos notificados de SRAG",
    definition=(
        "Proporcao de casos notificados de SRAG com vacinacao declarada: "
        "VACINA_COV = 1 / (VACINA_COV em 1 ou 2) para covid-19, e VACINA = 1 / "
        "(VACINA em 1 ou 2) para influenza."
    ),
    numerator="casos com vacinacao declarada como 1-Sim",
    denominator="casos com a informacao vacinal preenchida (1-Sim ou 2-Nao)",
    fields=("VACINA_COV", "VACINA", "DT_SIN_PRI"),
    period="casos com primeiros sintomas na janela analisada",
    missing_data_handling=(
        "Codigo 9 (Ignorado) e valores nulos sao excluidos de numerador e "
        "denominador; a proporcao de nao informados e devolvida junto ao "
        "resultado porque ela condiciona a leitura do indicador."
    ),
    limitations=(
        "ATENCAO: este indicador NAO e taxa de vacinacao da populacao. O "
        "denominador sao casos notificados de SRAG (majoritariamente "
        "hospitalizados), um grupo com perfil de risco distinto da populacao "
        "geral -- ha vies de selecao por definicao.",
        "A informacao e declarada no momento da notificacao e depende da "
        "apresentacao da caderneta; a subnotificacao de doses e conhecida.",
        "A cobertura vacinal populacional exigiria fonte externa (SI-PNI / "
        "localizaSUS) e denominador demografico (IBGE), fora do escopo da PoC.",
        _ANALYTIC_SCOPE_NOTE,
    ),
)

POPULATION_VACCINATION_COVERAGE = MetricDefinition(
    key="population_vaccination_coverage",
    name="Taxa de vacinacao da populacao",
    definition=(
        "Doses aplicadas na campanha (referencia externa SI-PNI, por UF e ano) "
        "sobre a populacao-alvo da campanha -- ou, na ausencia dela, sobre a "
        "populacao residente estimada pelo IBGE -- x 100."
    ),
    numerator="doses aplicadas na campanha, na UF e no ano de referencia (SI-PNI)",
    denominator="populacao-alvo da campanha ou populacao residente (IBGE)",
    fields=(),
    period="ano de referencia da campanha mais proximo da data de corte analitica",
    missing_data_handling=(
        "Sem o arquivo de referencia de doses aplicadas, o indicador e declarado "
        "nao calculavel com o motivo. Nunca e estimado a partir dos casos."
    ),
    limitations=(
        "O SIVEP-Gripe nao contem este dado: numerador e denominador vem de "
        "fontes externas (SI-PNI e IBGE), com sua propria defasagem.",
        "Doses aplicadas sao um proxy de pessoas vacinadas; em campanhas de dose "
        "unica (influenza) a aproximacao e boa, em esquemas de multiplas doses "
        "(covid-19) ela superestima a cobertura.",
        "Quando nao ha populacao-alvo informada, o denominador e a populacao "
        "total, o que subestima a cobertura do publico-alvo.",
    ),
    source="SI-PNI (doses aplicadas) e IBGE (populacao), via data/reference/",
)


# =============================================================================
# Indicadores complementares -- incidencia e baseline sazonal
# =============================================================================

INCIDENCE_RATE = MetricDefinition(
    key="incidence_rate",
    name="Incidencia de SRAG notificada por 100 mil habitantes",
    definition=(
        "Casos de SRAG com primeiros sintomas na janela analisada, por 100 mil "
        "habitantes: casos / populacao residente estimada (IBGE) x 100.000."
    ),
    numerator="casos com DT_SIN_PRI na janela analisada",
    denominator="populacao residente estimada (IBGE) da UF ou do Brasil, no ano mais proximo",
    fields=("DT_SIN_PRI", "SG_UF_NOT"),
    period="ultimos GROWTH_WINDOW_DAYS dias ate a data de corte analitica",
    missing_data_handling=(
        "Sem a referencia populacional carregada, o indicador e declarado nao "
        "calculavel; nunca se usa um denominador aproximado."
    ),
    limitations=(
        "Incidencia de casos NOTIFICADOS de SRAG (majoritariamente hospitalizados), "
        "nao de infeccao respiratoria na populacao.",
        "A populacao e a estimativa anual do IBGE mais proxima da data de corte; "
        "o ano usado e publicado junto do indicador.",
        "Permite comparar UFs de tamanhos diferentes, o que a contagem absoluta nao permite.",
        _REPORTING_LAG_NOTE,
    ),
    unit="por 100 mil hab.",
    source=f"{DATASUS_SOURCE_LABEL} (casos) e IBGE (populacao)",
)

SEASONAL_BASELINE = MetricDefinition(
    key="seasonal_excess",
    name="Excesso de casos sobre o baseline sazonal",
    definition=(
        "Variacao percentual dos casos da janela atual em relacao a MEDIANA dos "
        "casos observados na mesma janela de calendario nos anos de baseline: "
        "(casos_atuais - mediana_baseline) / mediana_baseline x 100."
    ),
    numerator="casos na janela atual menos a mediana dos anos de baseline na mesma janela",
    denominator="mediana dos casos na mesma janela de calendario nos anos de baseline",
    fields=("DT_SIN_PRI",),
    period="janela atual e a mesma janela (mes/dia) em cada ano de BASELINE_YEARS",
    missing_data_handling=(
        "Anos de baseline sem nenhum caso na base sao considerados ausentes e "
        "excluidos; com menos de BASELINE_MIN_YEARS anos presentes o indicador e "
        "declarado nao calculavel."
    ),
    limitations=(
        "2020 e 2021 ficam fora do baseline por padrao: a pandemia de covid-19 "
        "multiplicou as notificacoes de SRAG e um baseline que os incluisse "
        "rotularia qualquer ano normal como 'abaixo do esperado'.",
        "Mede se a janela atual esta acima ou abaixo do padrao historico da mesma "
        "epoca do ano -- e o que distingue surto de sazonalidade. A taxa de aumento "
        "de casos, que compara janelas consecutivas, nao faz essa distincao.",
        "Mudancas de criterio de notificacao e de cobertura da vigilancia entre os "
        "anos afetam a comparacao; os anos efetivamente usados sao publicados.",
        _REPORTING_LAG_NOTE,
    ),
)


# =============================================================================
# Series temporais
# =============================================================================

DAILY_CASES = MetricDefinition(
    key="daily_cases",
    name="Numero diario de casos de SRAG",
    definition="Contagem de casos de SRAG por data dos primeiros sintomas.",
    numerator="casos com DT_SIN_PRI igual ao dia",
    denominator="nao aplicavel (contagem absoluta)",
    fields=("DT_SIN_PRI",),
    period="ultimos 30 dias da janela analisavel",
    missing_data_handling="Dias sem nenhum caso aparecem com valor zero, nao omitidos.",
    limitations=(
        _REPORTING_LAG_NOTE,
        "A serie e por data de inicio de sintomas, nao por data de notificacao: "
        "muda a forma da curva em relacao a series publicadas por data de registro.",
        _ANALYTIC_SCOPE_NOTE,
    ),
    unit="casos",
)

MONTHLY_CASES = MetricDefinition(
    key="monthly_cases",
    name="Numero mensal de casos de SRAG",
    definition="Contagem de casos de SRAG por mes da data dos primeiros sintomas.",
    numerator="casos com DT_SIN_PRI no mes",
    denominator="nao aplicavel (contagem absoluta)",
    fields=("DT_SIN_PRI",),
    period="ultimos 12 meses da janela analisavel",
    missing_data_handling="Meses sem casos aparecem com valor zero, nao omitidos.",
    limitations=(
        "O mes mais recente costuma estar incompleto, tanto por atraso de "
        "notificacao quanto por ser um mes parcial.",
        _ANALYTIC_SCOPE_NOTE,
    ),
    unit="casos",
)


ALL_DEFINITIONS: Final[tuple[MetricDefinition, ...]] = (
    CASE_GROWTH_RATE,
    MORTALITY_RATE,
    ICU_ADMISSION_RATE,
    ICU_BED_OCCUPANCY_RATE,
    ICU_PATIENT_CENSUS,
    VACCINATION_COVERAGE,
    POPULATION_VACCINATION_COVERAGE,
    INCIDENCE_RATE,
    SEASONAL_BASELINE,
    DAILY_CASES,
    MONTHLY_CASES,
)

DEFINITIONS_BY_KEY: Final[dict[str, MetricDefinition]] = {
    definition.key: definition for definition in ALL_DEFINITIONS
}
