"""Gera a documentacao derivada do codigo.

Tres artefatos sao produzidos a partir das estruturas reais da aplicacao, e nao
redigidos a mao:

* `docs/dicionario_metricas.md` -- contrato metrica <-> campo <-> regra <-> limitacao;
* `docs/regras_transformacao.md` -- contrato de colunas e regras de limpeza;
* `docs/catalogo_tools.md` -- catalogo de tools e guardrails.

Documentacao gerada do codigo nao envelhece em silencio: se uma definicao de
metrica mudar e a documentacao nao for regerada, a diferenca aparece no diff.

Uso::

    python docs/gerar_documentacao.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (  # noqa: E402
    DATASUS_DATASET_URL,
    DATASUS_DICTIONARY_URL,
    DATASUS_SOURCE_LABEL,
    DELIVERY_LABEL,
    get_settings,
)
from src.data.cleaning import CLEANING_PIPELINE  # noqa: E402
from src.data.schema import (  # noqa: E402
    ADJUSTMENT_CODES,
    ADJUSTMENT_COLUMN,
    AGE_BANDS,
    ALLOWED_COLUMNS,
    CODE_LABELS,
    COHERENCE_FLAGS,
    DENIED_COLUMNS,
    DERIVED_SEMANTIC_COLUMNS,
    MISSING_CODES,
)
from src.guardrails.policies import ALL_POLICIES  # noqa: E402
from src.metrics.definitions import ALL_DEFINITIONS  # noqa: E402
from src.tools.registry import TOOLS  # noqa: E402


def _delivery_note(filename: str) -> str:
    """Identificacao da entrega, no topo de cada documento.

    Traz tambem o caminho do proprio arquivo: o avaliador recebe varios
    documentos separados, e um `.md` aberto isolado (ou impresso) precisa
    dizer, sem depender do repositorio ao redor, qual dos artefatos entregues
    ele e.
    """
    return f"> **{DELIVERY_LABEL}** -- Arquivo entregue: `{filename}`"


_GENERATED_NOTE = (
    "> Documento gerado por `python docs/gerar_documentacao.py` a partir das "
    "definicoes do codigo. Nao edite a mao: altere a fonte e regere."
)

#: Nota sobre a ausencia de data de geracao.
#:
#: O documento nao carrega a data em que foi gerado de proposito: isso o
#: tornaria irreprodutivel -- regerar amanha produziria um diff sem que nada
#: tivesse mudado, e o CI que verifica se a documentacao esta em dia falharia
#: todo dia. Quando cada documento mudou ja esta no historico do git.
_REPRODUCIBILITY_NOTE = (
    "> Reproduzivel: o conteudo depende apenas do codigo, nao da data de "
    "geracao. O CI falha se este arquivo divergir do que o codigo produz."
)


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build_metrics_doc() -> str:
    """Contrato formal entre dados brutos e indicadores publicados."""
    lines = [
        "# Dicionario de metricas",
        "",
        _delivery_note("docs/dicionario_metricas.md"),
        "",
        _GENERATED_NOTE,
        _REPRODUCIBILITY_NOTE,
        "",
        f"- **Fonte:** {DATASUS_SOURCE_LABEL}",
        f"- **Dataset:** {DATASUS_DATASET_URL}",
        f"- **Dicionario oficial de dados:** {DATASUS_DICTIONARY_URL}",
        "",
        "## Visao geral",
        "",
        "| Metrica | Campo(s) | Numerador | Denominador | Calculavel |",
        "|---------|----------|-----------|-------------|------------|",
    ]

    for definition in ALL_DEFINITIONS:
        calculavel = "nao" if definition.not_computable_reason else "sim"
        lines.append(
            f"| `{definition.key}` | {', '.join(definition.fields) or '-'} | "
            f"{_escape(definition.numerator)} | {_escape(definition.denominator)} | "
            f"{calculavel} |"
        )

    lines += ["", "## Detalhamento", ""]

    for definition in ALL_DEFINITIONS:
        lines += [
            f"### `{definition.key}` - {definition.name}",
            "",
            f"- **Definicao:** {definition.definition}",
            f"- **Numerador:** {definition.numerator}",
            f"- **Denominador:** {definition.denominator}",
            f"- **Campos utilizados:** "
            f"{', '.join(f'`{field}`' for field in definition.fields) or 'nenhum'}",
            f"- **Periodo:** {definition.period}",
            f"- **Unidade:** {definition.unit}",
            f"- **Tratamento de dados ausentes:** {definition.missing_data_handling}",
        ]

        if definition.not_computable_reason:
            lines += [
                "",
                f"> **NAO CALCULAVEL COM ESTE DATASET.** {definition.not_computable_reason}",
            ]

        lines += ["", "**Limitacoes:**", ""]
        lines += [f"- {limitation}" for limitation in definition.limitations]
        lines.append("")

    return "\n".join(lines)


def build_transformation_doc() -> str:
    """Contrato de colunas e regras de limpeza aplicadas na ingestao."""
    lines = [
        "# Regras de transformacao dos dados",
        "",
        _delivery_note("docs/regras_transformacao.md"),
        "",
        _GENERATED_NOTE,
        _REPRODUCIBILITY_NOTE,
        "",
        f"- **Fonte:** {DATASUS_SOURCE_LABEL}",
        f"- **Dicionario oficial:** {DATASUS_DICTIONARY_URL}",
        "",
        "> A semantica dos campos foi conferida em **duas versoes independentes** "
        "do dicionario oficial (a publicada com o dataset 2019-2026 e a versao "
        "`Dicionario_de_Dados_SRAG_Hospitalizado`). A numeracao dos campos na "
        "ficha difere entre elas -- `CLASSI_FIN` e o campo 78 numa e 80 na outra "
        "-- mas os dominios dos codigos sao identicos, inclusive o de `UTI` "
        "(`Internado em UTI?`, 1-Sim/2-Nao/9-Ignorado), que sustenta a decisao "
        "de nao chamar aquele indicador de taxa de ocupacao.",
        "",
        "## Principio",
        "",
        "Nenhum registro e removido ou alterado silenciosamente. Toda regra "
        "aplicada e contabilizada em `data/processed/quality_report.json`, e "
        "registros inconsistentes sao **marcados** (`flag_data_invalida`), nao "
        "excluidos da camada processada.",
        "",
        "## Minimizacao de dados",
        "",
        f"O arquivo bruto possui 194 colunas. Apenas **{len(ALLOWED_COLUMNS)}** sao "
        "lidas do disco; as demais nunca entram em memoria.",
        "",
        "### Colunas lidas",
        "",
        "| Coluna | Dominio |",
        "|--------|---------|",
    ]

    for column in ALLOWED_COLUMNS:
        labels = CODE_LABELS.get(column)
        if labels:
            domain = ", ".join(f"{code}={label}" for code, label in labels.items())
        elif column.startswith("DT_") or column.startswith("DOSE_"):
            domain = "data"
        else:
            domain = "texto ou numero"
        lines.append(f"| `{column}` | {_escape(domain) or 'texto'} |")

    lines += [
        "",
        "### Colunas proibidas (nunca lidas)",
        "",
        "Enumeradas explicitamente para que a decisao de nao processa-las fique "
        "auditavel. Um teste de regressao falha caso alguma delas alcance a "
        "camada analitica.",
        "",
        "| Coluna | Motivo da exclusao |",
        "|--------|--------------------|",
    ]
    for column, reason in sorted(DENIED_COLUMNS.items()):
        lines.append(f"| `{column}` | {reason} |")

    lines += [
        "",
        "## Pipeline de tratamento",
        "",
        "O tratamento e um pipeline declarado de regras nomeadas "
        "(`src/data/cleaning/`), nao um procedimento. Cada regra e uma classe "
        "com nome, descricao e teste proprio; a tabela abaixo e gerada da "
        "mesma estrutura que o codigo executa, entao nao pode divergir dele.",
        "",
        "A ordem importa: datas antes da coerencia (que compara datas) e "
        "coerencia antes da semantica (porque a usabilidade de uma estadia em "
        "UTI depende da flag de coerencia).",
        "",
        "| # | Regra | O que faz |",
        "|---|-------|-----------|",
        *(
            f"| {position} | `{rule.name}` | {_escape(rule.description)} |"
            for position, rule in enumerate(CLEANING_PIPELINE, start=1)
        ),
        "",
        "### Semantica derivada em Python",
        "",
        "A traducao dos codigos do dicionario em conceitos epidemiologicos "
        "acontece na camada de tratamento, ao lado de `CODE_LABELS`, e nao no "
        "SQL da view analitica. Manter a traducao em outra linguagem e outro "
        "arquivo permitiria que uma mudanca no dicionario nao alcancasse o "
        "calculo sem que nada falhasse. A view faz apenas projecao de tipo.",
        "",
        f"Colunas derivadas: {', '.join(f'`{name}`' for name in DERIVED_SEMANTIC_COLUMNS)}.",
        "",
        "## Registro no relatorio de qualidade",
        "",
        "| # | Regra | Comportamento | Registro no relatorio de qualidade |",
        "|---|-------|---------------|-------------------------------------|",
        "| 1 | Parse de datas | Tres formatos ja publicados pela fonte para o "
        "mesmo campo: `2026-04-30T00:00:00.000Z`, `2024-12-29` e `30/04/2026` "
        "(este ultimo apenas como fallback, para nao criar ambiguidade "
        "dia/mes) | `datas_nao_parseaveis_por_coluna` |",
        f"| 2 | Codigos de ausencia | O codigo {sorted(MISSING_CODES)} (Ignorado) e "
        "preservado e excluido de numeradores e denominadores; nunca vira `Nao` "
        "nem zero | `codigo_9_ignorado_por_coluna` |",
        "| 3 | Normalizacao de idade | `NU_IDADE_N` + `TP_IDADE` convertidos para "
        "anos; valores fora de [0, 120] anulados | "
        "`idade_fora_do_intervalo_plausivel` |",
        f"| 4 | Agregacao de idade | Faixas: {', '.join(label for _, _, label in AGE_BANDS)} | - |",
        "| 5 | Validacao de UF | Valor fora das 27 siglas e anulado | `uf_fora_do_dominio` |",
        "| 6 | Coerencia por dimensao | Quatro flags independentes (detalhadas "
        "abaixo) em vez de um unico veredito de validade | `coherence_flags` |",
        "| 7 | Recorte analitico | A view `srag_analytics` exclui apenas os "
        "registros com `flag_data_invalida`; a tabela `srag_cases` preserva "
        "todos | diferenca entre as duas contagens |",
        "",
        "## Flags de coerencia",
        "",
        "Um unico booleano `registro invalido` seria grosseiro demais: uma data "
        "de internacao impossivel nao deveria excluir o registro da contagem de "
        "casos, que depende apenas de `DT_SIN_PRI`. Na base de referencia isso "
        "descartaria 4.452 registros por um defeito irrelevante para a maior "
        "parte das metricas.",
        "",
        "Por isso a coerencia e avaliada por dimensao. Cada metrica exclui "
        "somente o que compromete o seu proprio calculo, e o volume de cada "
        "problema aparece no relatorio em vez de sumir num descarte agregado.",
        "",
        "| Flag | Exclui da view analitica | Significado |",
        "|------|--------------------------|-------------|",
        *(
            f"| `{name}` | {'sim' if name == 'flag_data_invalida' else 'nao'} | "
            f"{_escape(description)} |"
            for name, description in COHERENCE_FLAGS.items()
        ),
        "",
        "## Rastreamento dos ajustes",
        "",
        "Flags de coerencia descrevem o que o dado tem de errado. Esta secao "
        "trata do que o pipeline **fez** com ele -- sao coisas distintas: um "
        "registro pode estar incoerente sem ter sido tocado, e pode ter sido "
        "alterado sem estar incoerente.",
        "",
        f"Toda alteracao de valor e gravada no proprio registro, na coluna "
        f"`{ADJUSTMENT_COLUMN}` (codigos separados por virgula; vazia quando o "
        "registro chegou intacto). Isso torna cada alteracao localizavel:",
        "",
        "```sql",
        f"SELECT ano_referencia, {ADJUSTMENT_COLUMN}, count(*)",
        f"FROM srag_cases WHERE {ADJUSTMENT_COLUMN} <> '' GROUP BY 1, 2;",
        "```",
        "",
        "Sem isso, um campo anulado pelo pipeline seria indistinguivel de um "
        "que ja veio vazio da fonte -- e a alteracao seria, na pratica, "
        "silenciosa.",
        "",
        "| Codigo | Significado |",
        "|--------|-------------|",
        *(
            f"| `{code}` | {_escape(description)} |"
            for code, description in ADJUSTMENT_CODES.items()
        ),
        "",
        "### Rastreabilidade da carga",
        "",
        "| Artefato | Conteudo |",
        "|----------|----------|",
        "| `data/raw/manifest.json` | proveniencia do arquivo-fonte: origem, "
        "URL ou caminho, tamanho, sha256 e data |",
        "| `data/processed/quality_report.json` | `run_id` da carga, contagens "
        "por regra, flags, ajustes e proveniencia |",
        "| `data/processed/ingestion_history.jsonl` | uma linha por carga, para "
        "comparar versoes da base e detectar degradacao na fonte |",
        "| coluna `ajustes_aplicados` | alteracoes aplicadas a cada registro |",
        "| tabela `audit_events` | eventos da ingestao e das execucoes do "
        "agente, consultaveis por `run_id` |",
        "",
        "## Janela de analise",
        "",
        "Toda janela temporal e ancorada na **maior data de digitacao da base** "
        "(`max(DT_DIGITA)`), nunca em `today()`: a base publicada tem defasagem em "
        "relacao ao dia corrente. Da data de referencia sao descontados "
        "`REPORTING_LAG_DAYS` dias, para nao confundir atraso de notificacao com "
        "queda real de casos. A tool `get_notification_completeness` mede os "
        "percentis do atraso observado e sinaliza quando o corte configurado e "
        "menor que o percentil 75.",
        "",
    ]
    return "\n".join(lines)


def build_tools_doc() -> str:
    """Catalogo de tools e politicas de guardrail."""
    lines = [
        "# Catalogo de tools e guardrails",
        "",
        _delivery_note("docs/catalogo_tools.md"),
        "",
        _GENERATED_NOTE,
        _REPRODUCIBILITY_NOTE,
        "",
        "",
        "## Tools",
        "",
        "Todas as tools sao deterministicas, com schema de entrada fechado "
        "(`extra=forbid`) e envelope de saida padronizado. Nao existe tool de "
        "consulta livre ao banco.",
        "",
        "| Tool | Categoria | Parametros | Descricao |",
        "|------|-----------|------------|-----------|",
    ]

    for tool in TOOLS:
        schema = tool.input_model.model_json_schema()
        params = ", ".join(f"`{name}`" for name in schema.get("properties", {})) or "-"
        lines.append(
            f"| `{tool.name}` | {tool.category} | {params} | {_escape(tool.description)} |"
        )

    lines += [
        "",
        "## Guardrails",
        "",
        "| # | Politica | Verificada em | Descricao |",
        "|---|----------|---------------|-----------|",
    ]
    for index, policy in enumerate(ALL_POLICIES, start=1):
        lines.append(
            f"| {index} | {policy.name} | {policy.enforced_at} | {_escape(policy.description)} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    settings = get_settings()
    docs = settings.docs_dir
    docs.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "dicionario_metricas.md": build_metrics_doc(),
        "regras_transformacao.md": build_transformation_doc(),
        "catalogo_tools.md": build_tools_doc(),
    }

    for name, content in artifacts.items():
        path = docs / name
        path.write_text(content, encoding="utf-8")
        print(f"Gerado: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
