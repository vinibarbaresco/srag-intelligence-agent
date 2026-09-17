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
    name="Sem diagnóstico ou conduta clínica",
    description=(
        "O sistema produz análise epidemiológica agregada. Não emite diagnóstico, "
        "prescrição, recomendação terapêutica nem orientação de conduta clínica "
        "individual."
    ),
    enforced_at=(
        "validate_request (entrada) e generate_interpretation, passo "
        "apply_output_guardrails (saída)"
    ),
)

SENSITIVE_DATA = GuardrailPolicy(
    key="sensitive_data",
    name="Proteção de dados pessoais",
    description=(
        "Colunas identificáveis nunca são lidas do dataset; toda saída passa por "
        "varredura de identificadores (CPF, CNS, e-mail, telefone). O agente "
        "consulta apenas agregados por período, UF e classificação; não existe "
        "tool para recuperar registros individuais."
    ),
    enforced_at=(
        "schema de ingestão, regra de célula pequena nas tools, auditoria, "
        "generate_interpretation (varredura da saída) e generate_report (cabeçalho)"
    ),
)

EVIDENCE_BINDING = GuardrailPolicy(
    key="evidence_binding",
    name="Toda afirmação quantitativa precisa de evidência",
    description=(
        "Números presentes na interpretação são confrontados com os valores "
        "efetivamente retornados pelas tools. Valor sem lastro bloqueia a "
        "publicação do relatório."
    ),
    enforced_at="validate_evidence e generate_interpretation, passo apply_output_guardrails",
)

NO_ARBITRARY_SQL = GuardrailPolicy(
    key="no_arbitrary_sql",
    name="Sem SQL arbitrário gerado pelo modelo",
    description=(
        "Não existe tool que execute consulta livre. O modelo escolhe tools e "
        "preenche parâmetros tipados, validados contra domínios fechados; o SQL é "
        "literal no código e recebe valores por binding. O banco é aberto em modo "
        "somente leitura."
    ),
    enforced_at="camada de tools e conexão DuckDB",
)

NEWS_NEVER_OVERRIDES_DATA = GuardrailPolicy(
    key="news_never_overrides_data",
    name="Notícias não sobrescrevem dados oficiais",
    description=(
        "Notícias circulam em campo próprio do estado e entram no relatório "
        "apenas sob o rótulo CONTEXTO EXTERNO. Nenhum indicador é calculado, "
        "ajustado ou corrigido a partir de conteúdo jornalístico."
    ),
    enforced_at="estado do grafo e renderização do relatório",
)

UNCERTAINTY = GuardrailPolicy(
    key="uncertainty",
    name="Declarar indisponibilidade em vez de extrapolar",
    description=(
        "Quando uma métrica não pode ser calculada com segurança, o sistema "
        "declara a indisponibilidade e o motivo. Nunca substitui ausência por "
        "zero, média ou estimativa."
    ),
    enforced_at="camada de métricas e generate_report",
)

SEMANTIC_REVIEW = GuardrailPolicy(
    key="semantic_review",
    name="Revisão semântica independente da saída",
    description=(
        "Depois das verificações lexicais, o texto do modelo é entregue a um "
        "revisor independente (outra chamada de modelo, prompt próprio, sem acesso "
        "ao pedido original) que procura conduta clínica parafraseada e dado "
        "individual -- achados bloqueantes -- e, em caráter consultivo, obediência "
        "a instruções vindas de notícias e extrapolação de indicador indisponível, "
        "que viram aviso. Indisponibilidade do revisor é declarada no relatório e a "
        "camada lexical permanece (fail-open)."
    ),
    enforced_at=(
        "generate_interpretation, passo apply_output_guardrails, após as verificações "
        "lexicais; somente sobre texto produzido por modelo"
    ),
)

PROMPT_INJECTION = GuardrailPolicy(
    key="prompt_injection",
    name="Instrução do sistema não é reescrita pela solicitação",
    description=(
        "A solicitação é classificada em risco de prompt injection antes de "
        "qualquer consulta. Risco alto -- sobrescrever instruções, assumir outro "
        "papel, extrair o prompt ou credenciais, executar SQL ou código, arbitrar "
        "o valor de um indicador, suprimir limitações -- é recusado, e o motivo "
        "vai para a trilha de auditoria. Risco médio segue com aviso registrado. "
        "Conteúdo externo (títulos, fontes, URLs e mensagens de erro) é "
        "sanitizado antes de entrar no contexto do modelo, e a solicitação do "
        "usuário circula rotulada como dado, nunca como instrução."
    ),
    enforced_at=(
        "validate_request (classificação e recusa), select_optional_tools "
        "(allowlist e schemas) e generate_interpretation (sanitização do "
        "contexto externo)"
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
    PROMPT_INJECTION,
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
    "Este relatório apresenta análise epidemiológica agregada de dados públicos "
    "de vigilância. Não constitui diagnóstico, prescrição, recomendação "
    "terapêutica nem orientação de conduta clínica individual."
)

UNCERTAINTY_STATEMENT: Final[str] = (
    "Não é possível calcular esta métrica com segurança com os dados disponíveis."
)
