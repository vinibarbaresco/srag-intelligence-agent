# Indicium HealthCare — SRAG Intelligence Agent

[![CI](https://github.com/vinibarbaresco/srag-intelligence-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/vinibarbaresco/srag-intelligence-agent/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://docs.astral.sh/ruff/)

Agente de monitoramento epidemiológico de **Síndrome Respiratória Aguda Grave (SRAG)** sobre dados
reais do Open DATASUS. Prova de conceito com orquestração em LangGraph, cálculo determinístico em
SQL, busca semântica de notícias, guardrails explícitos e trilha de auditoria por execução.

```bash
python main.py --setup    # primeira execução: baixa dados e monta os bancos
python main.py            # atualiza notícias e gera o relatório
```

---

## Entrega para avaliação

Este repositório contém todos os artefatos solicitados para a PoC:

- documentação técnica, instruções de execução, decisões e limitações neste README;
- diagrama conceitual em PDF: [`docs/arquitetura.pdf`](docs/arquitetura.pdf);
- código-fonte do agente, ferramentas, tratamento de dados, testes e documentação complementar
  em [`docs/`](docs/README.md).

Os CSVs do DATASUS, bancos locais, chaves e relatórios gerados não são versionados por serem
reproduzíveis, volumosos ou sensíveis. A seção [Como executar](#12-como-executar) explica como
reconstruir esses artefatos a partir da fonte oficial.

---

## 1. Visão geral

O sistema recebe uma solicitação em linguagem natural, valida-a, aciona ferramentas determinísticas
que consultam um banco analítico, recupera contexto jornalístico de um Vector DB, verifica a
evidência de cada número, produz uma interpretação e publica um relatório rastreável — registrando
cada passo em uma trilha de auditoria identificada por `run_id`.

O princípio que organiza todas as decisões:

> **Python/SQL para calcular. Tools para acessar. LangGraph para orquestrar. LLM para interpretar e comunicar.**

Nenhum número do relatório passa por um modelo de linguagem.

## 2. Problema

Profissionais de saúde precisam acompanhar a severidade e a evolução de surtos de SRAG. Os dados
existem — o SIVEP-Gripe publica todas as notificações — mas em formato hostil: 194 colunas,
codificação `latin-1`, variáveis categóricas codificadas, campos ignorados, atraso de notificação e
dados potencialmente sensíveis. Extrair deles um panorama confiável exige uma cadeia auditável, não
um modelo de linguagem lendo CSV.

## 3. Arquitetura

Diagrama completo: **[`docs/arquitetura.pdf`](docs/arquitetura.pdf)**.

```
                                    ┌─────────────────────────────────────┐
  Open DATASUS ──► data/raw ──►     │  TOOLS DETERMINÍSTICAS              │
  (CSV, 194 cols)  download.py      │  ├─ indicadores (4 + 2 complementares)│
        │                           │  ├─ diagnóstico de completude       │
        ▼                           │  ├─ séries temporais (2)            │
  data/processed ◄── preprocess.py  │  ├─ gráficos (2)                    │
  (Parquet, 31 cols)                │  └─ busca de notícias (1)           │
        │                           └──────────────┬──────────────────────┘
        ▼                                          │ envelope com fonte
  data/analytics/srag.duckdb ──────────────────────┤
  (tabela + view analítica + referências           │
   IBGE / SI-PNI de data/reference/)               │
                                                   ▼
  Google News RSS ──► ingest.py ──►        ┌───────────────────────────┐
  (atualização por relatório; cache        │  AGENTE (LangGraph)       │
   persistido em falha de rede)            │                           │
        │                                  │  1. validate_request      │
        ▼                                  │  2. collect_metrics       │
  data/analytics/news_vectors.duckdb ─────►│  3. collect_time_series   │◄── LLM (OpenAI)
  (Vector DB, busca por cosseno)           │  4. search_external_news  │    planeja, seleciona
                                           │  5. evaluate_alerts       │    tools, interpreta;
                                           │  6. validate_evidence     │    revisor semântico
                                           │  7. generate_interpretation│   independente (gpt-4o)
                                           │  8. generate_report       │    revisa a saída
                                           └───────────┬───────────────┘
                                                       ▼
                                        outputs/reports/*.md + *.html
                                        outputs/charts/*.png
                                        outputs/audit/<run_id>.jsonl
                                        outputs/history/runs.jsonl   (variação entre execuções)

  Também: API HTTP (python -m src.api) sobre o mesmo grafo · execução agendada (GitHub Actions)

  ══ TRANSVERSAL ══ guardrails (7) · trilha de auditoria · logging estruturado · minimização de dados
```

**Decisões que sustentam a arquitetura:**

| Decisão | Motivo |
|---|---|
| Grafo linear, sem ciclos | O relatório tem entregas fixas; um agente que decide sozinho quantas vezes repetir uma etapa é mais difícil de auditar, sem ganho aqui |
| Tools nomeadas, sem SQL livre | O LLM escolhe operações e preenche parâmetros tipados; o SQL é literal no código |
| Estado compartimentado | `metrics` / `external_context` / `interpretation` são campos distintos — separar DADO de CONTEXTO de INFERÊNCIA é estrutural, não uma instrução de prompt |
| Definições de métrica como dados | O relatório e a documentação são gerados das mesmas `MetricDefinition` que descrevem o cálculo, então descrição e execução não divergem |
| DuckDB para os dois bancos | Colunar para as agregações, `list_cosine_similarity` para o Vector DB; um arquivo, zero servidor |

## 4. Tecnologias

Python 3.12 · LangGraph · LangChain/OpenAI · DuckDB · pandas + PyArrow · Pydantic Settings ·
matplotlib · PyMuPDF · pytest

## 5. Dataset

[SRAG 2019–2026 — Open DATASUS](https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026)
([dicionário de dados](https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/dicionario-de-dados-2019-a-2025.pdf)).

Os arquivos **não são versionados** — são grandes e reproduzíveis. `src/data/download.py` os obtém
sob demanda e registra proveniência (URL, tamanho, `sha256`, data) em `data/raw/manifest.json`.

Quem já tiver o CSV em disco — é o caso de quem recebeu o arquivo junto com o enunciado — pode
registrá-lo em vez de baixar. O arquivo não é copiado: o manifesto guarda o caminho original e o
mesmo `sha256`, de modo que a proveniência de um relatório seja verificável seja qual for a origem
do dado.

```bash
python main.py --setup --csv "INFLUD25_DATASUS-Versao26-06-2025.csv"
```

O parser de datas cobre os **três formatos** que a fonte já publicou para o mesmo campo:
`2026-04-30T00:00:00.000Z` (publicações atuais), `2024-12-29` (versão de 26/06/2025) e `30/04/2026`
(safras antigas).

O portal não expõe API CKAN (`/api/3/action/*` retorna 404), e o nome do arquivo embute a data de
republicação (`INFLUD26-24-08-2026.csv`). Por isso a URL **nunca é fixada no código**: é resolvida
a cada execução na página do dataset, que é renderizada no servidor.

| Ano | Registros | CSV bruto | Papel |
|---|---|---|---|
| 2019 | 48.941 | 52 MB | regime pré-pandêmico; fora do baseline padrão |
| 2022 | 560.577 | 623 MB | baseline sazonal |
| 2023 | 279.453 | 316 MB | baseline sazonal |
| 2024 | 267.986 | 302 MB | baseline sazonal |
| 2025 | 336.391 | 382 MB | série corrente |
| 2026 | 212.278 | 237 MB | série corrente |
| **Total** | **1.705.626** | **1,9 GB** | **Parquet de ~18 MB (16 colunas lidas → 31 após derivações)** |

2020 e 2021 (1,3 GB e 1,8 GB) não são baixados por padrão: são anos pandêmicos, excluídos do
baseline por definição, e a série corrente não precisa deles. `SRAG_YEARS` controla o conjunto;
`--setup --years 2025 2026` reproduz a configuração mínima (603 MB).

## 6. Tratamento dos dados

Documentação completa: [`docs/regras_transformacao.md`](docs/regras_transformacao.md).

**O tratamento é um pipeline declarado de regras nomeadas**, não um procedimento. Cada regra é uma
classe com nome, descrição e teste próprio; a ordem está declarada em `CLEANING_PIPELINE`:

```
src/data/
  cleaning/
    base.py          CleaningRule (contrato) + CleaningContext
    dates.py         parse_datas ........ 3 formatos publicados pela fonte
    codes.py         normaliza_codigos_categoricos · normaliza_sexo
                     normaliza_numericos · valida_uf
    demographics.py  deriva_idade · marca_ano_de_origem
    coherence.py     avalia_coerencia ... 4 flags por dimensão
    derived.py       deriva_semantica ... códigos → conceitos epidemiológicos
    pipeline.py      a ordem, declarada
  quality.py         QualityReport (agregado) + AdjustmentLog (por registro)
  preprocess.py      orquestração: lê, aplica o pipeline, grava
```

Isso muda o que dá para auditar: o tratamento se lê como uma **lista de regras**, cada uma
exercitável num teste sem executar a carga. A tabela de regras em `docs/regras_transformacao.md`
é gerada dessa mesma estrutura, então não pode divergir do que roda.

**A semântica vive em Python, não em SQL.** A tradução dos códigos do dicionário em conceitos
(`eh_obito_srag`, `caso_encerrado`, `teve_admissao_uti`…) fica em `derived.py`, ao lado de
`CODE_LABELS`. Manter essa tradução no SQL da view permitiria que uma mudança no dicionário não
alcançasse o cálculo sem que nada falhasse — e definições em SQL só são testáveis com um banco
montado. A view analítica faz apenas projeção de tipo.

**Minimização na origem.** Das 194 colunas, **16** são lidas — as que alguma métrica, regra de
coerência ou série efetivamente consome. As demais estão em duas listas explícitas:

- **`DENIED_COLUMNS`** (33) — identificáveis ou sensíveis: `NU_NOTIFIC`, `DT_NASC`, `NM_UN_INTE`,
  município, ocupação, textos livres, lotes de imunizante, raça/cor, idade gestacional, datas de
  dose vacinal.
- **`NOT_SELECTED_COLUMNS`** (7) — avaliadas no dicionário e descartadas por não serem usadas:
  `DT_NOTIFIC`, `DT_ENCERRA`, `CRITERIO`, `SEM_PRI`, `SG_UF`, `FATOR_RISC`, `SUPORT_VEN`.

Minimizar não é só excluir o que identifica — é não ler o que nenhuma métrica consome. Um teste de
regressão falha se alguma coluna lida deixar de ser usada, o que força a escolha entre usá-la de
fato ou declará-la como não selecionada, com o motivo. A idade é agregada em faixa etária; a
granularidade geográfica máxima é a UF de notificação.

**Nada é removido silenciosamente.** Registros inconsistentes são **marcados**, não excluídos.
Toda regra vira contagem em `data/processed/quality_report.json`, e o relatório traz uma seção de
qualidade dos dados. Na execução de referência: 1.705.626 linhas lidas, **0 descartadas** (573 sem eixo temporal utilizável ficam fora da view analítica, marcadas, não removidas).

**Coerência por dimensão, não um veredito único.** Quatro flags independentes, porque uma data de
internação impossível não deve excluir o registro da contagem de casos, que depende apenas de
`DT_SIN_PRI` — um booleano único descartaria 4.452 registros por um defeito irrelevante para a
maioria das métricas. Só a flag do eixo temporal exclui da camada analítica:

| Flag | Registros | % | Exclui da análise |
|---|---|---|---|
| `flag_data_invalida` | 89 | 0,02% | **sim** |
| `flag_internacao_inconsistente` | 11.700 | 2,19% | não |
| `flag_uti_inconsistente` | 4.194 | 0,79% | não |
| `flag_evolucao_inconsistente` | 39.174 | 7,33% | não |

**Rastreamento dos ajustes.** Flags de coerência dizem o que o dado tem de errado; a coluna
`ajustes_aplicados` diz o que o pipeline **fez** com ele. Sem ela, um campo anulado pela limpeza
seria indistinguível de um que já veio vazio da fonte — a alteração seria, na prática, silenciosa.
Cada carga recebe um `run_id`, grava sua proveniência no relatório de qualidade e acrescenta uma
linha a `ingestion_history.jsonl`, o que permite comparar versões da base.

```sql
SELECT ajustes_aplicados, count(*) FROM srag_cases
WHERE ajustes_aplicados <> '' GROUP BY 1;
-- idade_anulada | 2
```

**Imputação limitada e declarada.** O censo de UTI precisa de uma data de saída; quando ela falta,
a permanência é imputada pela data de evolução e, na ausência dela, **até um teto empírico** — o
percentil 95 da permanência das estadias com saída registrada (28 dias na base de referência).
Sem o teto, pacientes admitidos meses antes contavam como internados até a data de corte: 93% do
censo no dia de corte era imputado e o pico saía inflado em ordem de grandeza (14.954 contra
3.821). A revisão final encontrou e corrigiu esse defeito. O relatório publica, junto do pico, a
fração dele que depende de imputação e a completude medida **sobre a mesma população** que
alimenta o censo.

**Ausência é ausência.** O código `9-Ignorado` é preservado e excluído de numeradores e
denominadores — nunca convertido em `Não` ou zero. O volume de ignorados é reportado junto de cada
indicador, porque ele condiciona a leitura do resultado.

**Datas.** Parse ISO-8601 com sufixo `Z` (formato atual da fonte) e `dd/mm/aaaa` como fallback.

**Atraso de notificação.** Toda janela é ancorada em `max(DT_DIGITA)` da base — **nunca em
`today()`** — e desconta `REPORTING_LAG_DAYS`. O padrão de 21 dias foi calibrado medindo o atraso
real da base (mediana 7 dias, p75 13, p90 26), não arbitrado. A tool
`get_notification_completeness` republica essa medição a cada execução e alerta se o corte ficar
abaixo do p75.

## 7. Métricas

Documentação completa: [`docs/dicionario_metricas.md`](docs/dicionario_metricas.md).

| # | Indicador | Numerador | Denominador | Status |
|---|---|---|---|---|
| 1 | Taxa de aumento de casos | casos na janela atual − anterior | casos na janela anterior | calculável |
| 2 | Taxa de mortalidade | `EVOLUCAO = 2` | `EVOLUCAO ∈ (1,2,3)` | calculável |
| 3 | Taxa de admissão em UTI | `UTI = 1` | `UTI ∈ (1,2)` entre `HOSPITAL = 1` | calculável |
| 3b | **Taxa de ocupação de leitos de UTI** | leitos ocupados | capacidade instalada | **não calculável** |
| 4 | Cobertura vacinal entre casos notificados | `VACINA_COV = 1` | `VACINA_COV ∈ (1,2)` | calculável |
| 4b | **Taxa de vacinação da população** | doses aplicadas (SI-PNI) | população-alvo ou IBGE | **calculável só com referência externa** fornecida em `data/reference/`; sem ela, declarada indisponível |
| 5 | Incidência por 100 mil habitantes *(complementar)* | casos na janela | população residente (IBGE) | calculável — referência versionada no repositório |
| 6 | Excesso sobre o baseline sazonal *(complementar)* | casos atuais − mediana da mesma janela nos anos de baseline | mediana do baseline | calculável com ≥ 2 anos de baseline carregados |

Os dois indicadores complementares respondem à pergunta que os quatro exigidos não respondem
sozinhos. A **incidência** (denominador IBGE, tabela 6579, obtida por
`python -m src.data.reference.population` e versionada com proveniência) torna UFs de tamanhos
diferentes comparáveis. O **baseline sazonal** compara a janela atual com a mesma janela de
calendário em 2022–2024 (mediana) — é o que distingue surto de sazonalidade, coisa que a taxa de
aumento entre janelas consecutivas não faz. **2020 e 2021 nunca entram** no baseline: a pandemia
multiplicou as notificações de SRAG e qualquer ano normal pareceria "abaixo do esperado". 2019 fica
fora do padrão por outro motivo — regime de vigilância pré-pandêmico, 48 mil casos/ano contra 270 mil
ou mais a partir de 2022 — e pode ser incluído via `BASELINE_YEARS`. Os anos efetivamente usados,
ausentes e excluídos são publicados junto do indicador.

### Os dois indicadores que o dataset não permite calcular sozinho

O desafio pede "taxa de ocupação de UTI" e "taxa de vacinação da população". **Nenhum dos dois é
calculável com o SIVEP-Gripe**, e o sistema diz isso em vez de renomear uma aproximação:

**Ocupação de UTI.** O campo 53 (`UTI`) registra *"Internado em UTI?"* — uma **admissão**, não a
ocupação de uma capacidade. O dataset não contém leitos instalados nem leitos ocupados. O sistema
entrega a taxa de admissão em UTI entre hospitalizados (o que de fato mede) e o censo diário de
pacientes de SRAG em UTI, derivado de `DT_ENTUTI`/`DT_SAIDUTI` — um censo, não uma taxa. O campo
`icu_bed_occupancy_rate` volta `null` com o motivo.

**Vacinação da população.** `VACINA_COV` só existe para pessoas que adoeceram e foram notificadas —
um grupo com viés de seleção por definição. O sistema entrega a cobertura declarada entre casos
notificados, acompanhada da **completude da informação**. A taxa populacional passa a ser calculada
quando a equipe fornece a extração oficial de doses aplicadas (SI-PNI / LocalizaSUS) no contrato
[`data/reference/cobertura_vacinal_uf.template.csv`](data/reference/cobertura_vacinal_uf.template.csv)
— o Ministério da Saúde não expõe API estável para esse dado, então ele entra por arquivo, com
fonte e URL publicados junto do indicador. O denominador é a população-alvo da campanha ou, na
ausência dela, a população residente do IBGE (rotulado). Sem o arquivo, o indicador permanece
**explicitamente nulo com o motivo** — nunca estimado a partir dos casos.

## 8. Agent Architecture

Grafo `StateGraph` linear, oito nós, sem ciclos:

```
START → validate_request → ┬→ END (solicitação recusada, nenhuma consulta ao banco)
                           └→ collect_epidemiological_metrics
                              → collect_time_series
                              → search_external_news
                              → evaluate_alerts          (limiares + variação vs. execução anterior)
                              → validate_evidence
                              → generate_interpretation  (LLM → guardrails lexicais → revisor semântico)
                              → generate_report → END
```

O nó `evaluate_alerts` é determinístico: aplica os limiares de `ALERT_*_THRESHOLD_PCT` aos
indicadores já calculados, consulta `outputs/history/runs.jsonl` pela execução anterior do mesmo
recorte e publica a variação de cada indicador. O veredito (`normal` / `atencao` / `alerta`) entra
no relatório como DADO e, com `--fail-on-alert`, no código de saída do processo (2) — é o sinal que
a execução agendada usa.

O LLM atua em dois pontos: **planejamento** (escolhe tools, no `validate_request`) e
**interpretação** (`generate_interpretation`). O plano do modelo é **registrado para auditoria** e comparado ao conjunto obrigatório
do relatório — o modelo pode acrescentar tools, nunca suprimir uma exigida pela entrega.

Antes de consultar o Vector DB, o nó `search_external_news` tenta atualizar os feeds. Se a rede ou
algum feed falhar, a execução continua sobre o acervo persistido e registra a degradação. Cada item
mantém título, fonte, data de publicação, URL e instante de recuperação. Conteúdo externo é tratado
como dado não confiável e nunca como instrução para o modelo.

Sem `OPENAI_API_KEY`, ou com `--no-llm`, um `DeterministicNarrator` redige o relatório por template
a partir dos mesmos resultados de tools. A via efetivamente usada é registrada no relatório e na
auditoria.

**Consumo do modelo.** Cada chamada registra `usage_metadata`; o relatório publica tokens de
entrada/saída por papel (redator, revisor) e o **custo estimado** com os preços de lista
configurados (`OPENAI_*_PRICE_PER_1M_TOKENS`) — rotulado como estimativa, não fatura. Ordem de
grandeza: ~US$ 0,007 por relatório com `gpt-4o-mini` redigindo e `gpt-4o` revisando. Tracing
opcional via LangSmith (`LANGCHAIN_TRACING_V2`), lido automaticamente pelo LangChain.

## 9. Tools

Catálogo completo: [`docs/catalogo_tools.md`](docs/catalogo_tools.md). Doze tools pequenas,
determinísticas e testadas, com schema de entrada fechado (`extra=forbid`) e envelope de saída
padronizado:

```python
{
    "metric": "mortality_rate",
    "value": 5.25,
    "unit": "%",
    "numerator": 921,
    "denominator": 17535,
    "period": {...},
    "filters": {...},
    "components": {...},
    "source": "Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)",
    "definition": "...",
    "limitations": [...],
    "unavailable_reason": null,
}
```

## 10. Guardrails

| # | Política | Onde é aplicada | O que impede |
|---|---|---|---|
| 1 | Sem conduta clínica | entrada e saída | Pedidos de diagnóstico/prescrição são recusados; linguagem prescritiva na saída é bloqueada |
| 2 | Proteção de dados pessoais | ingestão, tools, auditoria, saída | Colunas identificáveis nunca lidas; idade em faixa e geografia só até UF; CPF/CNS/e-mail/telefone varridos de logs e do relatório; indicador com denominador abaixo de `MIN_CELL_SIZE` é suprimido |
| 3 | Evidência obrigatória | `validate_evidence` + saída | Todo número da interpretação é confrontado com os retornos das tools |
| 4 | Sem SQL arbitrário | camada de tools | Não existe tool de consulta livre; parâmetros tipados, SQL literal, banco somente leitura |
| 5 | Notícias não sobrescrevem dados | estado do grafo e relatório | Contexto externo em campo próprio, publicado só sob o rótulo CONTEXTO EXTERNO |
| 6 | Declarar incerteza | métricas e relatório | Métrica sem denominador retorna `null` + motivo, nunca zero ou estimativa |
| 7 | Revisão semântica independente | saída, após as verificações lexicais | Outra chamada de modelo (`gpt-4o`, prompt próprio, sem acesso ao pedido) procura **conduta clínica parafraseada** e **dado individual** — bloqueantes — e, em caráter consultivo, obediência a instrução vinda de notícia e extrapolação de indicador indisponível, que viram aviso |

O **guardrail 7** cobre o que regex não alcança: *"quem tiver falta de ar deveria considerar ir ao
pronto atendimento"* não tem verbo prescritivo, mas é conduta clínica. Decisões de projeto:
**fail-closed** em achado bloqueante (o texto cai na redação determinística), **fail-open** em
indisponibilidade do revisor (a camada lexical permanece e o relatório declara que a revisão não
ocorreu), e só texto de modelo é revisado. Números **não** são objeto da revisão: a camada lexical
já os confronta de forma determinística, e pedir ao revisor que refizesse essa conta produziu
falsos positivos por formatação. A calibração mostrou também que `gpt-4o-mini` como revisor
apontava "conduta clínica" em frases sobre viés estatístico — por isso o revisor padrão é `gpt-4o`,
que lê ~2 mil tokens por execução.

O **guardrail 3** é o mais consequente. O conjunto de valores citáveis é montado a partir dos
retornos das tools; todo número do texto é extraído (notação pt-BR inclusa) e confrontado com ele.
Se o modelo publicar um valor sem lastro, o texto é **descartado** e substituído pela redação
determinística — e o bloqueio aparece no relatório.

## 11. Governança e auditoria

Cada execução tem um `run_id` (UUID4). Cada nó e cada tool gravam um evento com `timestamp`, `seq`,
`node`, `tool`, `parameters`, `status`, `duration_ms`, `result_summary`, `source` e `error`, em
`outputs/audit/<run_id>.jsonl` — fonte primária, escrita evento a evento, que sobrevive a uma
interrupção no meio da execução — e replicados ao final na tabela `audit_events` do DuckDB, o que
permite comparar execuções com SQL. Parâmetros passam pelo mascarador de dados pessoais antes de
serem gravados.

```sql
SELECT tool, count(*), round(avg(duration_ms), 1) AS ms_medio
FROM audit_events WHERE tool IS NOT NULL GROUP BY tool ORDER BY ms_medio DESC;
```

**Não se registra chain-of-thought do modelo** — apenas eventos operacionais e decisões observáveis.

```bash
python main.py --audit <run_id>
```

```
 seq status           ms  no / tool                              resumo
   1 ok              0.1  validate_request                       solicitacao aceita
   2 ok              3.8  plan_analysis                          10 tools no plano efetivo
   3 ok            547.8  get_case_growth_rate                   case_growth_rate=-32.39 %
   4 ok             30.9  get_mortality_rate                     mortality_rate=5.25 %
   5 ok             60.5  get_icu_metrics                        icu_admission_rate=27.07 %
  ...
  16 ok              0.7  validate_evidence                      130 valores lastreados
  18 ok              0.7  apply_output_guardrails                saida aprovada
  19 ok              4.0  generate_report                        relatorio gravado em ...
```

## 12. Como executar

```bash
git clone <repo> && cd Certificacao
python -m pip install -r requirements.txt
cp .env.example .env          # preencha OPENAI_API_KEY (opcional: veja --no-llm)
python main.py --setup        # ~5 min: baixa 603 MB, processa, monta os dois bancos
python main.py
```

As dependencias possuem limites de versao principal para evitar atualizacoes
incompativeis. Para executar tambem as verificacoes de qualidade do codigo, use
`python -m pip install -r requirements-dev.txt`.

**Antes de rodar a suite, confira a versao do interpretador.** O CI instala
exatamente o que `requirements.txt` fixa; um Python de sistema fora dessa faixa
executa os testes contra uma configuracao que o projeto nao suporta, e o defeito
so aparece no CI. Para reproduzir o ambiente do CI:

```bash
make venv                                  # cria .venv-ci nas versoes fixadas
make check PYTHON=.venv-ci/bin/python      # Windows: .venv-ci/Scripts/python.exe
```

O diretorio e ignorado pelo git e pode ser apagado a qualquer momento.

`--setup` é idempotente: o download reaproveita o cache local.

Sem `make`, no Windows: `.\run_demo.ps1` (mesmos passos; `-Csv arquivo.csv`, `-Uf SP`, `-Llm`).
Com Docker, nada é instalado na máquina:

```bash
docker build -t srag-agent .
docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" srag-agent --setup --no-llm
docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" --env-file .env srag-agent
```

A chave entra por `--env-file`; nunca é copiada para a imagem (`.dockerignore`). Um relatório de
exemplo já gerado está em [`docs/exemplo_relatorio.md`](docs/exemplo_relatorio.md).

| Comando | Efeito |
|---|---|
| `python main.py` | Relatório nacional completo |
| `python main.py --uf SP` | Recorte por unidade federativa |
| `python main.py --classification 5` | Apenas SRAG por covid-19 |
| `python main.py --no-llm` | Interpretação determinística, sem credencial |
| `python main.py --audit <run_id>` | Trilha de auditoria de uma execução |
| `python main.py --setup --years 2026` | Prepara apenas um ano |
| `python main.py --setup --csv arquivo.csv` | Usa um CSV já em disco, sem baixar nada |
| `python main.py --fail-on-alert` | Código de saída 2 se alguma regra de alerta disparar |
| `python -m src.api` | API HTTP (`/health`, `/indicadores/{tool}`, `/series/{tool}`, `POST /relatorios`, `/relatorios/{run_id}`, `/auditoria/{run_id}`) |
| `python -m src.data.reference.population` | Atualiza a referência populacional do IBGE em `data/reference/` |
| `make demo` · `make test` · `make check` | Atalhos: demonstração, testes, o mesmo gate do CI |
| `make venv` | Ambiente isolado nas versões fixadas em `requirements.txt`, para reproduzir o CI |

Etapas isoladas: `python -m src.data.download`, `src.data.preprocess`, `src.data.load_database`,
`src.news.ingest`. Documentação e diagrama: `python docs/gerar_documentacao.py`,
`python docs/gerar_diagrama_pdf.py`.

### Variáveis de ambiente

| Variável | Padrão | Função |
|---|---|---|
| `OPENAI_API_KEY` | — | Credencial do LLM; sem ela, via determinística |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo de planejamento e interpretação |
| `REPORTING_LAG_DAYS` | `21` | Dias descontados por atraso de notificação |
| `GROWTH_WINDOW_DAYS` | `30` | Tamanho das janelas comparadas |
| `SRAG_YEARS` | `2025,2026` | Anos processados (o `.env.example` sugere `2022,2023,2024,2025,2026` para habilitar o baseline) |
| `BASELINE_YEARS` | `2022,2023,2024` | Anos do baseline sazonal (2020–2021 nunca entram) |
| `BASELINE_MIN_YEARS` | `2` | Mínimo de anos presentes na base para publicar o baseline |
| `ALERT_GROWTH_THRESHOLD_PCT` | `20` | Limiar da regra de crescimento de casos |
| `ALERT_MORTALITY_THRESHOLD_PCT` | `10` | Limiar da regra de letalidade |
| `ALERT_BASELINE_EXCESS_THRESHOLD_PCT` | `50` | Limiar da regra de excesso sazonal |
| `SEMANTIC_JUDGE_ENABLED` | `true` | Liga o revisor semântico (exige credencial) |
| `OPENAI_JUDGE_MODEL` | `gpt-4o` | Modelo do revisor; mais forte que o redator por calibração |
| `OPENAI_*_PRICE_PER_1M_TOKENS` | gpt-4o-mini / gpt-4o | Preços de lista para a estimativa de custo (redator e revisor) |
| `POPULATION_REFERENCE_PATH` | `data/reference/populacao_uf.csv` | Referência populacional (IBGE) |
| `VACCINATION_REFERENCE_PATH` | `data/reference/cobertura_vacinal_uf.csv` | Doses aplicadas por UF (SI-PNI), se fornecidas |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Endereço da API HTTP |
| `NEWS_MAX_AGE_DAYS` | `45` | Janela de notícias |
| `NEWS_MAX_RESULTS` | `12` | Limite máximo de notícias recuperadas |
| `MIN_CELL_SIZE` | `5` | Piso de denominador; abaixo dele a proporção é suprimida |
| `OPENAI_TEMPERATURE` | `0.0` | Temperatura do modelo (zero: interpretação reproduzível) |
| `ICU_STAY_CAP_PERCENTILE` | `0.95` | Percentil da permanência real que limita a imputação no censo de UTI |
| `ICU_STAY_CAP_DAYS` | — | Teto fixo de permanência em UTI; se definido, ignora o percentil |
| `NEWS_REFRESH_ON_RUN` | `true` | Atualiza os feeds antes de cada relatório; usa cache em falha |
| `LOG_LEVEL` | `INFO` | Nível mínimo do log estruturado |
| `DATA_ROOT` | — | Redireciona `data/` e `outputs/` |

## 13. Exemplos

O projeto roda sobre duas bases diferentes, e os números **devem** divergir entre elas. Isso não é
inconsistência — é a âncora temporal funcionando: toda janela é ancorada em `max(DT_DIGITA)` da
base carregada, nunca em `today()`.

### Base A — download do DATASUS (padrão)

`python main.py --setup --years 2019 2022 2023 2024 2025 2026 && python main.py` · 1.705.626
registros · arquivos republicados em 14/09/2026 · corte analítico **2026-08-23**

| Indicador | Valor | Detalhe |
|---|---|---|
| Taxa de aumento de casos | **−35,38 %** | 19.336 casos (jul/25–ago/23) contra 29.922 na janela anterior |
| Taxa de mortalidade | **5,33 %** | 715 óbitos em 13.404 casos encerrados; 5.437 ainda em aberto |
| Taxa de admissão em UTI | **28,47 %** | 4.845 de 17.016 hospitalizados com UTI informado |
| Cobertura vacinal (covid-19) | **39,93 %** | 7.636 de 19.124 |
| Incidência por 100 mil hab. | **9,03** | 19.336 casos sobre 214.211.951 habitantes (IBGE 2026) |
| Excesso sobre o baseline sazonal | **−12,08 %** | 19.336 contra mediana de 21.993 na mesma janela de 2022–2024 — compatível com a sazonalidade |
| Ocupação de leitos de UTI | **não calculável** | o dataset não registra capacidade instalada |
| Vacinação da população | **não calculável** | referência SI-PNI não fornecida em `data/reference/` |
| Alertas | **normal** | nenhuma regra disparada; variação zero frente à execução anterior de mesmo corte |

Relatório completo desta execução: [`docs/exemplo_relatorio.md`](docs/exemplo_relatorio.md).

### Base B — CSV distribuído com o enunciado

`python main.py --setup --csv INFLUD25_DATASUS-Versao26-06-2025.csv && python main.py`
· 165.397 registros · corte analítico **2025-06-05**

| Indicador | Valor | Detalhe |
|---|---|---|
| Taxa de aumento de casos | **+46,97 %** | 52.558 casos contra 35.760 na janela anterior |
| Taxa de mortalidade | **7,86 %** | 2.845 óbitos em 36.182 casos encerrados |
| Taxa de admissão em UTI | **26,70 %** | 11.827 de 44.289 hospitalizados com UTI informado |
| Cobertura vacinal (covid-19) | **66,16 %** | 34.425 de 52.036 |

### Por que o crescimento inverte de sinal

Não é divergência de cálculo: são fases opostas da mesma sazonalidade. A base do enunciado termina
em **junho/2025**, no meio da subida do inverno; a base baixada alcança **setembro/2026**, depois do
pico. O mesmo código, ancorado na data de cada base, descreve corretamente os dois momentos — e o
baseline sazonal existe justamente para dizer se a fase atual está dentro do padrão da época
(−12 % frente à mediana de 2022–2024: está).

Em ambas: 0 registros descartados, os dois indicadores impossíveis declarados como tal, e a
trilha de auditoria completa.

### Recortes menores

```bash
python main.py --uf AC --classification 1
```

Com poucos casos, dois guardrails entram em ação. Indicadores com denominador abaixo de
`MIN_CELL_SIZE` são **suprimidos**; taxas construídas sobre menos de 20 eventos são publicadas com
a instabilidade **declarada**:

> **Atenção:** Taxa baseada em apenas 1 evento(s), abaixo do mínimo de 20 usualmente exigido para
> uma taxa estável. O valor é real, mas não sustenta comparação entre períodos ou recortes.

Suprimir esconderia informação válida; publicar sem ressalva sugeriria uma precisão que o número
não tem.

## 14. Testes

```bash
python -m pytest -q          # 391 testes, ~50 s
python -m ruff check .
```

A suíte roda sobre uma **base sintética de valores conhecidos**, montada em diretório temporário:
não exige os 603 MB nem acesso à rede, e permite afirmar resultados exatos — inclusive os casos de
borda que raramente aparecem em volume suficiente na base real.

### Integração contínua

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) roda a cada push e em toda pull request
para `main`:

| Verificação | O que impede |
|---|---|
| `ruff check` | Código fora do padrão do projeto |
| `ruff format --check` | Formatação divergente |
| `pytest` | Regressão em qualquer dos 391 testes (inclui red team e golden set) |
| Documentação regenerada | Que uma definição de métrica, regra de limpeza ou guardrail mude sem que a documentação acompanhe |

A última é a menos óbvia e a mais útil: os documentos em `docs/` são gerados do código, então o CI
os regenera e falha se o resultado divergir do que está commitado. Por isso eles **não carregam
data de geração** — seriam irreprodutíveis, e a verificação falharia todo dia sem que nada tivesse
mudado.

Como a suíte é hermética, o CI não precisa de segredo nem do dataset, e não fica sujeito à
instabilidade do DATASUS ou dos feeds de notícias. Execução completa em cerca de 1 minuto.

| Arquivo | Cobre |
|---|---|
| `test_cleaning_pipeline.py` | Pipeline declarado: ordem das regras, cada regra isolada, semântica derivada dos códigos do dicionário |
| `test_red_team.py` | Ataques por camada: pedido de dado individual, pedido clínico, SQL via tool/parâmetro, número inventado, injeção via notícia, vazamento de chave, prescrição na saída |
| `test_preprocessing.py` | Contrato de colunas, parse de datas nos três formatos, código 9 e códigos fora do domínio, idade implausível, marcação sem exclusão, carga ponta a ponta |
| `test_metrics.py` | Os 4 indicadores com valores exatos, denominador zero, filtro sem resultado, mês parcial |
| `test_database.py` | Coerência das flags derivadas com o dicionário, conexão somente leitura, binding de parâmetros |
| `test_tools.py` | Envelope completo, parâmetros inválidos, falha de tool, auditoria e mascaramento |
| `test_guardrails.py` | As 7 políticas, com caso bloqueado **e** caso legítimo (falso positivo importa) |
| `test_agent.py` | Grafo completo, planejamento, recusa na entrada, substituição de saída reprovada, degradação de notícias |
| `test_reference.py` | Referências IBGE e SI-PNI: validação do contrato, carga em tabela, cobertura populacional calculada e declarada indisponível |
| `test_monitoring.py` | Regras de alerta (limiar, indisponível vira atenção), histórico entre execuções, `--fail-on-alert` |
| `test_semantic_judge.py` | Revisor semântico: categorias bloqueantes x consultivas, fail-open, parse da resposta, integração no grafo com revisor falso |
| `test_golden_set.py` | 60 casos fixos (30 pedidos, 30 saídas) com veredito esperado; taxas de bloqueio e aprovação indevidos têm de ser zero |
| `test_api.py` | Rotas HTTP: catálogo, envelope idêntico ao da tool, 404/422, `run_id` validado como UUID, relatório e auditoria |

### Golden set

[`tests/golden/`](tests/golden/) congela 60 vereditos inequívocos. Diferente dos testes unitários,
mede o comportamento **agregado** dos guardrails — bloqueio indevido (falso positivo) e aprovação
indevida (falso negativo) — e qualquer mudança em regex, prompt ou política que altere um veredito
aparece caso a caso. Já pagou o investimento: encontrou *"recomenda-se administrar antivirais"*
escapando do padrão prescritivo (o clítico `-se` quebrava o casamento).

### Monitoramento agendado

[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml) roda toda segunda-feira (e sob
demanda) sobre a base atual do DATASUS, publica relatório, gráficos, auditoria e histórico como
artefato e **fica vermelho quando uma regra de alerta dispara** (`--fail-on-alert` → código 2). Com
`OPENAI_API_KEY` nos segredos do repositório, a interpretação e a revisão semântica entram
automaticamente; sem ela, a via determinística.

## 15. Estrutura do projeto

```
main.py                      entrypoint (CLI)
Dockerfile · Makefile · run_demo.ps1   execução reproduzível
src/
  config.py                  configuração centralizada (.env)
  data/        schema.py · download.py · preprocess.py · load_database.py · cleaning/
               reference/  population.py (IBGE) · vaccination.py (SI-PNI) · tables.py
  metrics/     definitions.py · filters.py · epidemiology.py · timeseries.py
  monitoring/  alerts.py (limiares) · history.py (variação entre execuções)
  api/         app.py · schemas.py (FastAPI sobre o mesmo grafo)
  tools/       schemas.py · registry.py · metric_tools.py · series_tools.py
               chart_tools.py · news_tools.py
  news/        rss_client.py · embeddings.py · vector_store.py · ingest.py
  guardrails/  policies.py · pii.py · input_guard.py · output_guard.py · small_cells.py
               semantic_judge.py (revisor independente)
  observability/ logging_config.py · audit.py
  agent/       state.py · llm.py · nodes.py · graph.py · orchestrator.py · report.py
  visualization/ charts.py
data/          raw/ · processed/ · analytics/          (não versionado)
               reference/  populacao_uf.csv + proveniência · cobertura_vacinal_uf.template.csv
outputs/       reports/ · charts/ · audit/ · history/  (não versionado)
docs/          arquitetura.pdf · dicionario_metricas.md · regras_transformacao.md
               catalogo_tools.md · exemplo_relatorio.md · gerar_*.py
.github/       ci.yml (lint, testes, docs) · monitor.yml (execução agendada com alerta)
tests/         14 arquivos · suíte hermética com fixture sintética · red team · golden set
```

## 16. Limitações

**Dos dados.** O SIVEP-Gripe cobre casos de SRAG **notificados**, majoritariamente hospitalizados —
nenhum indicador representa infecção respiratória na população geral; a incidência por 100 mil é de
casos notificados. A série recente é incompleta por atraso de notificação. A mortalidade é letalidade
entre casos encerrados, e a janela recente é instável enquanto muitos casos seguem em aberto.
Ocupação de leitos de UTI continua não calculável; a cobertura vacinal populacional depende de uma
extração do SI-PNI fornecida por arquivo (§7). O baseline sazonal compara regimes de vigilância que
mudaram entre os anos; os anos usados são publicados.

**Da implementação.** O embedding local (`hashing-ngram-local`) agrupa por vocabulário compartilhado,
não por sinonímia — a busca de notícias é notavelmente melhor com `OPENAI_API_KEY`. O acervo de
notícias depende do que os feeds do Google News expõem no momento da ingestão. O censo diário de UTI
imputa a permanência de quem não tem data de saída até a data de evolução ou de corte, o que
superestima os dias mais recentes. O guardrail de evidência isenta inteiros de 0 a 31 e anos de 2019
a 2030, para não bloquear frases legítimas como "os 4 indicadores". O revisor semântico é um
modelo de linguagem: bloqueia só nas categorias de dano direto e, mesmo assim, um falso positivo
derruba a interpretação para a via determinística — custo aceito por projeto.

**Do escopo.** A API HTTP não tem autenticação nem limitação de taxa: destina-se a rede interna ou
a um gateway na frente. A execução agendada baixa a base a cada rodada (sem cache entre execuções).

## 17. Próximos passos

1. **Leitos de UTI (CNES)** — o último indicador exigido ainda não calculável: integrar leitos
   habilitados por UF para transformar o censo de pacientes em taxa de ocupação real.
2. **Feed oficial do SI-PNI** — substituir o arquivo de referência por extração automática quando
   houver fonte estável, mantendo o mesmo contrato e proveniência.
3. **Nowcasting do atraso de notificação** — estimar a subnotificação recente a partir da
   distribuição de atraso já medida, publicando intervalo em vez de apenas descartar a janela.
4. **Recorte por faixa etária** — o dado já está na camada analítica; falta expô-lo como parâmetro
   de tool com a supressão de pequenas células que já existe.
5. **Painel sobre `audit_events` e `runs.jsonl`** — duração por tool, taxa de degradação, deriva dos
   indicadores e custo por execução ao longo do tempo.
6. **Autenticação e limitação de taxa na API** — pré-requisito para expô-la fora da rede interna.
7. **Cache do dataset na execução agendada** — evitar o download de ~2 GB a cada rodada.

---

> Este projeto apresenta análise epidemiológica agregada de dados públicos de vigilância. Não
> constitui diagnóstico, prescrição, recomendação terapêutica nem orientação de conduta clínica
> individual.
