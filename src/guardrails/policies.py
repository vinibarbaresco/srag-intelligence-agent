"""Politicas de guardrail declaradas em um unico lugar.

Cada politica descreve o que e proibido, por que, e em que ponto do fluxo e
verificada. Declara-las como dados (e nao como `if` espalhados) permite que o
relatorio final liste exatamente quais protecoes estavam ativas na execucao e
que os testes percorram todas elas sem duplicar a lista.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    """Descricao auditavel de uma protecao do sistema."""

    key: str
    name: str
    description: str
    enforced_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "enforced_at": self.enforced_at,
        }


MEDICAL_ADVICE = GuardrailPolicy(
    key="medical_advice",
    name="Sem diagnostico ou conduta clinica",
    description=(
        "O sistema produz analise epidemiologica agregada. Nao emite diagnostico, "
        "prescricao, recomendacao terapeutica nem orientacao de conduta clinica "
        "individual."
    ),
    enforced_at="validate_request (entrada) e generate_report (saida)",
)

SENSITIVE_DATA = GuardrailPolicy(
    key="sensitive_data",
    name="Protecao de dados pessoais",
    description=(
        "Colunas identificaveis nunca sao lidas do dataset; toda saida passa por "
        "varredura de identificadores (CPF, CNS, e-mail, telefone). O agente "
        "consulta apenas agregados por periodo, UF e classificacao; nao existe "
        "tool para recuperar registros individuais."
    ),
    enforced_at=(
        "schema de ingestao, regra de celula pequena nas tools, auditoria e generate_report"
    ),
)

EVIDENCE_BINDING = GuardrailPolicy(
    key="evidence_binding",
    name="Toda afirmacao quantitativa precisa de evidencia",
    description=(
        "Numeros presentes na interpretacao sao confrontados com os valores "
        "efetivamente retornados pelas tools. Valor sem lastro bloqueia a "
        "publicacao do relatorio."
    ),
    enforced_at="validate_evidence e generate_report",
)

NO_ARBITRARY_SQL = GuardrailPolicy(
    key="no_arbitrary_sql",
    name="Sem SQL arbitrario gerado pelo modelo",
    description=(
        "Nao existe tool que execute consulta livre. O modelo escolhe tools e "
        "preenche parametros tipados, validados contra dominios fechados; o SQL e "
        "literal no codigo e recebe valores por binding. O banco e aberto em modo "
        "somente leitura."
    ),
    enforced_at="camada de tools e conexao DuckDB",
)

NEWS_NEVER_OVERRIDES_DATA = GuardrailPolicy(
    key="news_never_overrides_data",
    name="Noticias nao sobrescrevem dados oficiais",
    description=(
        "Noticias circulam em campo proprio do estado e entram no relatorio "
        "apenas sob o rotulo CONTEXTO EXTERNO. Nenhum indicador e calculado, "
        "ajustado ou corrigido a partir de conteudo jornalistico."
    ),
    enforced_at="estado do grafo e renderizacao do relatorio",
)

UNCERTAINTY = GuardrailPolicy(
    key="uncertainty",
    name="Declarar indisponibilidade em vez de extrapolar",
    description=(
        "Quando uma metrica nao pode ser calculada com seguranca, o sistema "
        "declara a indisponibilidade e o motivo. Nunca substitui ausencia por "
        "zero, media ou estimativa."
    ),
    enforced_at="camada de metricas e generate_report",
)

ALL_POLICIES: Final[tuple[GuardrailPolicy, ...]] = (
    MEDICAL_ADVICE,
    SENSITIVE_DATA,
    EVIDENCE_BINDING,
    NO_ARBITRARY_SQL,
    NEWS_NEVER_OVERRIDES_DATA,
    UNCERTAINTY,
)


# =============================================================================
# Padroes lexicais
# =============================================================================

#: Pedidos de conduta clinica individual, recusados na entrada.
CLINICAL_REQUEST_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(qual|que)\s+(remedio|medicamento|antibiotico|antiviral)\b", re.I),
    re.compile(r"\bcomo\s+(tratar|curar|medicar)\b", re.I),
    re.compile(r"\b(prescrev|receit)\w*\b", re.I),
    re.compile(r"\b(dose|dosagem|posologia)\s+(de|para|recomendad)\w*", re.I),
    re.compile(r"\b(diagnostic\w+|diagnóstic\w+)\s+(d[eo]|para)\s+(mim|paciente|meu|minha)", re.I),
    re.compile(r"\b(devo|posso)\s+(tomar|usar|aplicar|interromper)\b", re.I),
    re.compile(r"\bestou\s+com\b.*\b(sintoma|febre|tosse|falta de ar)\b", re.I),
    re.compile(r"\bmeu\s+(filho|pai|mae|marido|paciente)\b", re.I),
)

#: Linguagem prescritiva, proibida na saida do modelo.
PRESCRIPTIVE_OUTPUT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\brecomend\w*\s+(o uso|administrar|prescrever|tomar)\b", re.I),
    re.compile(
        r"\b(deve|devem|deveria)\s+(tomar|usar|administrar|receber)\s+\w*"
        r"(medicament|antivir|antibiotic|oseltamivir|tamiflu)\w*",
        re.I,
    ),
    re.compile(r"\b(prescrev|receit)\w*\b", re.I),
    re.compile(r"\bo\s+diagnostico\s+(e|é)\b", re.I),
    re.compile(r"\bdose\s+recomendada\b", re.I),
)

DISCLAIMER: Final[str] = (
    "Este relatorio apresenta analise epidemiologica agregada de dados publicos "
    "de vigilancia. Nao constitui diagnostico, prescricao, recomendacao "
    "terapeutica nem orientacao de conduta clinica individual."
)

UNCERTAINTY_STATEMENT: Final[str] = (
    "Nao e possivel calcular esta metrica com seguranca com os dados disponiveis."
)
