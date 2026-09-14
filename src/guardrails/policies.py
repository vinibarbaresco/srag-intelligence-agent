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
    enforced_at=(
        "validate_request (entrada) e generate_interpretation, passo "
        "apply_output_guardrails (saida)"
    ),
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
        "schema de ingestao, regra de celula pequena nas tools, auditoria, "
        "generate_interpretation (varredura da saida) e generate_report (cabecalho)"
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
    enforced_at="validate_evidence e generate_interpretation, passo apply_output_guardrails",
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

SEMANTIC_REVIEW = GuardrailPolicy(
    key="semantic_review",
    name="Revisao semantica independente da saida",
    description=(
        "Depois das verificacoes lexicais, o texto do modelo e entregue a um "
        "revisor independente (outra chamada de modelo, prompt proprio, sem acesso "
        "ao pedido original) que procura conduta clinica parafraseada e dado "
        "individual -- achados bloqueantes -- e, em carater consultivo, obediencia "
        "a instrucoes vindas de noticias e extrapolacao de indicador indisponivel, "
        "que viram aviso. Indisponibilidade do revisor e declarada no relatorio e a "
        "camada lexical permanece (fail-open)."
    ),
    enforced_at=(
        "generate_interpretation, passo apply_output_guardrails, apos as verificacoes "
        "lexicais; somente sobre texto produzido por modelo"
    ),
)

ALL_POLICIES: Final[tuple[GuardrailPolicy, ...]] = (
    MEDICAL_ADVICE,
    SENSITIVE_DATA,
    EVIDENCE_BINDING,
    NO_ARBITRARY_SQL,
    NEWS_NEVER_OVERRIDES_DATA,
    UNCERTAINTY,
    SEMANTIC_REVIEW,
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
    # Pedidos clinicos genericos, sem paciente explicito: "quais medicamentos
    # sao indicados", "sugira um tratamento", "protocolo de tratamento para".
    re.compile(
        r"\b(medicament\w*|remedi\w*|antivir\w*|antibiotic\w*|tratamento\w*|terapia\w*)"
        r"\s+(sao\s+|e\s+|é\s+|mais\s+)?(indicad\w*|recomendad\w*|adequad\w*|eficaz\w*)",
        re.I,
    ),
    re.compile(
        r"\b(sugira|sugere|indique|recomende)\s+(um\s+|o\s+)?(tratamento|medicament|remedi|terapia)",
        re.I,
    ),
    re.compile(r"\b(protocolo|esquema)\s+(de\s+)?(tratamento|medicacao|terapeutic\w*)\b", re.I),
    re.compile(r"\bcomo\s+(devo|posso)\s+(me\s+)?(tratar|medicar|cuidar)\b", re.I),
)

#: Pedidos de dado individual, recusados na entrada (Guardrail 2).
#:
#: Nao existe tool que devolva registros; a recusa aqui e uma camada a mais, e
#: deixa o motivo explicito em vez de devolver um relatorio agregado que nao
#: responde ao que foi pedido.
INDIVIDUAL_DATA_REQUEST_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\b(registro|dado|caso|lista|nome|prontuario)s?\s+(individua\w*|nominai\w*|pessoai\w*)",
        re.I,
    ),
    re.compile(
        r"\b(nome|cpf|cns|endereco|endereço|telefone|prontuario)s?\s+(d[eo]s?\s+)?(paciente|obito|óbito|caso)s?",
        re.I,
    ),
    re.compile(
        r"\b(mostre|liste|revele|exiba|retorne|traga)\s+(os\s+|as\s+)?(pacientes|obitos|óbitos|pessoas)\s+que\b",
        re.I,
    ),
    re.compile(r"\bquem\s+(morreu|faleceu|foi\s+internad\w*)\b", re.I),
    re.compile(r"\b(revele|mostre|exiba)\s+.*\b(registros?|dados?)\s+individua\w*", re.I),
)

#: Linguagem prescritiva, proibida na saida do modelo.
PRESCRIPTIVE_OUTPUT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # "recomenda-se administrar": o cliticio "-se" entre o verbo e o objeto
    # escapava do padrao original -- achado do golden set.
    re.compile(r"\brecomend\w*(?:-se)?\s+(o uso|administrar|prescrever|tomar|iniciar)\b", re.I),
    re.compile(
        r"\b(deve|devem|deveria)\s+(tomar|usar|administrar|receber)\s+\w*"
        r"(medicament|antivir|antibiotic|oseltamivir|tamiflu)\w*",
        re.I,
    ),
    re.compile(r"\b(prescrev|receit)\w*\b", re.I),
    re.compile(r"\bo\s+diagnostico\s+(e|é)\b", re.I),
    re.compile(r"\bdose\s+recomendada\b", re.I),
    # Orientacao de conduta sem verbo prescritivo explicito: "usar antivirais",
    # "devem procurar atendimento e tomar ...", "e indicado o uso de ...".
    re.compile(
        r"\b(usar|tomar|fazer\s+uso\s+de|iniciar)\s+(o\s+|os\s+|um\s+)?(antivir|antibiotic|oseltamivir|tamiflu|corticoid|medicament)\w*",
        re.I,
    ),
    re.compile(
        r"\b(e|é|esta|está)\s+(indicad|recomendad)\w*\s+(o\s+uso|a\s+administracao|iniciar|tratar)\b",
        re.I,
    ),
)

DISCLAIMER: Final[str] = (
    "Este relatorio apresenta analise epidemiologica agregada de dados publicos "
    "de vigilancia. Nao constitui diagnostico, prescricao, recomendacao "
    "terapeutica nem orientacao de conduta clinica individual."
)

UNCERTAINTY_STATEMENT: Final[str] = (
    "Nao e possivel calcular esta metrica com seguranca com os dados disponiveis."
)
