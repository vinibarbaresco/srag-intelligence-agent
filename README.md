# Indicium HealthCare — SRAG Intelligence Agent

Agente de monitoramento epidemiológico de **Síndrome Respiratória Aguda Grave (SRAG)** sobre dados
reais do Open DATASUS. Prova de conceito com orquestração em LangGraph, cálculo determinístico em
SQL, busca semântica de notícias, guardrails explícitos e trilha de auditoria por execução.

```bash
python main.py --setup    # primeira execução: baixa dados, monta o banco, coleta notícias
python main.py            # gera o relatório
```

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
  (CSV, 194 cols)  download.py      │  ├─ indicadores (4)                 │
        │                           │  ├─ diagnóstico de completude       │
        ▼                           │  ├─ séries temporais (2)            │
  data/processed ◄── preprocess.py  │  ├─ gráficos (2)                    │
  (Parquet, 32 cols)                │  └─ busca de notícias (1)           │
        │                           └──────────────┬──────────────────────┘
        ▼                                          │ envelope com fonte
  data/analytics/srag.duckdb ──────────────────────┤
  (tabela + view analítica)                        │
                                                   ▼
  Google News RSS ──► ingest.py ──►        ┌───────────────────────────┐
  (allowlist + filtro de tema)             │  AGENTE (LangGraph)       │
        │                                  │  1. validate_request      │
        ▼                                  │  2. collect_metrics       │
  data/analytics/news_vectors.duckdb ─────►│  3. collect_time_series   │◄── LLM (OpenAI)
  (Vector DB, busca por cosseno)           │  4. search_external_news  │    planeja, seleciona
                                           │  5. validate_evidence     │    tools, interpreta
                                           │  6. generate_interpretation│
                                           │  7. generate_report       │
                                           └───────────┬───────────────┘
                                                       ▼
                                        outputs/reports/*.md + *.html
                                        outputs/charts/*.png
                                        outputs/audit/<run_id>.jsonl

  ══ TRANSVERSAL ══ guardrails · trilha de auditoria · logging estruturado · minimização de dados
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

| Ano | Registros | CSV bruto | Após o contrato de colunas |
|---|---|---|---|
| 2025 | 336.179 | 382 MB | — |
| 2026 | 198.129 | 221 MB | — |
| **Total** | **534.308** | **603 MB** | **Parquet de 9,5 MB (32 colunas)** |

## 6. Tratamento dos dados

Documentação completa: [`docs/regras_transformacao.md`](docs/regras_transformacao.md).

**Minimização na origem.** Das 194 colunas, **32** são lidas. As identificáveis (`NU_NOTIFIC`,
`DT_NASC`, `NM_UN_INTE`, município, ocupação, textos livres, lotes de imunizante…) estão numa
*denylist* explícita — enumeradas para que a decisão de excluí-las fique auditável, e cobertas por
teste de regressão. A idade é agregada em faixa etária; a granularidade geográfica máxima é a UF.

**Nada é removido silenciosamente.** Registros inconsistentes são **marcados**
(`flag_data_invalida`), não excluídos. Toda regra vira contagem em
`data/processed/quality_report.json`. Na execução de referência: 534.308 linhas lidas, **0
descartadas**, 89 marcadas por linha do tempo impossível.

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
| 4b | **Taxa de vacinação da população** | pessoas vacinadas | população total | **não calculável** |

### Os dois indicadores que o dataset não permite calcular

O desafio pede "taxa de ocupação de UTI" e "taxa de vacinação da população". **Nenhum dos dois é
calculável com o SIVEP-Gripe**, e o sistema diz isso em vez de renomear uma aproximação:

**Ocupação de UTI.** O campo 53 (`UTI`) registra *"Internado em UTI?"* — uma **admissão**, não a
ocupação de uma capacidade. O dataset não contém leitos instalados nem leitos ocupados. O sistema
entrega a taxa de admissão em UTI entre hospitalizados (o que de fato mede) e o censo diário de
pacientes de SRAG em UTI, derivado de `DT_ENTUTI`/`DT_SAIDUTI` — um censo, não uma taxa. O campo
`icu_bed_occupancy_rate` volta `null` com o motivo.

**Vacinação da população.** `VACINA_COV` só existe para pessoas que adoeceram e foram notificadas —
um grupo com viés de seleção por definição. O sistema entrega a cobertura declarada entre casos
notificados, acompanhada da **completude da informação**, e mantém `population_vaccination_coverage`
explicitamente nulo. O denominador populacional exigiria SI-PNI e estimativas do IBGE, fora do
escopo da PoC.

## 8. Agent Architecture

Grafo `StateGraph` linear, sete nós, sem ciclos:

```
START → validate_request → ┬→ END (solicitação recusada, nenhuma consulta ao banco)
                           └→ collect_epidemiological_metrics
                              → collect_time_series
                              → search_external_news
                              → validate_evidence
                              → generate_interpretation
                              → generate_report → END
```

O LLM atua em dois pontos: **planejamento** (escolhe tools, no `validate_request`) e
**interpretação** (`generate_interpretation`). O plano do modelo é **unido** ao conjunto obrigatório
do relatório — o modelo pode acrescentar tools, nunca suprimir uma exigida pela entrega.

Sem `OPENAI_API_KEY`, ou com `--no-llm`, um `DeterministicNarrator` redige o relatório por template
a partir dos mesmos resultados de tools. A via efetivamente usada é registrada no relatório e na
auditoria.

## 9. Tools

Catálogo completo: [`docs/catalogo_tools.md`](docs/catalogo_tools.md). Dez tools pequenas,
determinísticas e testadas, com schema de entrada fechado (`extra=forbid`) e envelope de saída
padronizado:

```python
{
  "metric": "mortality_rate", "value": 5.25, "unit": "%",
  "numerator": 921, "denominator": 17535,
  "period": {...}, "filters": {...}, "components": {...},
  "source": "Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)",
  "definition": "...", "limitations": [...], "unavailable_reason": null
}
```

## 10. Guardrails

| # | Política | Onde é aplicada | O que impede |
|---|---|---|---|
| 1 | Sem conduta clínica | entrada e saída | Pedidos de diagnóstico/prescrição são recusados; linguagem prescritiva na saída é bloqueada |
| 2 | Proteção de dados pessoais | ingestão, auditoria, saída | Colunas identificáveis nunca lidas; CPF/CNS/e-mail/telefone varridos de logs e do relatório |
| 3 | Evidência obrigatória | `validate_evidence` + saída | Todo número da interpretação é confrontado com os retornos das tools |
| 4 | Sem SQL arbitrário | camada de tools | Não existe tool de consulta livre; parâmetros tipados, SQL literal, banco somente leitura |
| 5 | Notícias não sobrescrevem dados | estado do grafo e relatório | Contexto externo em campo próprio, publicado só sob o rótulo CONTEXTO EXTERNO |
| 6 | Declarar incerteza | métricas e relatório | Métrica sem denominador retorna `null` + motivo, nunca zero ou estimativa |

O **guardrail 3** é o mais consequente. O conjunto de valores citáveis é montado a partir dos
retornos das tools; todo número do texto é extraído (notação pt-BR inclusa) e confrontado com ele.
Se o modelo publicar um valor sem lastro, o texto é **descartado** e substituído pela redação
determinística — e o bloqueio aparece no relatório.

## 11. Governança e auditoria

Cada execução tem um `run_id` (UUID4). Cada nó e cada tool gravam um evento com `timestamp`, `seq`,
`node`, `tool`, `parameters`, `status`, `duration_ms`, `result_summary`, `source` e `error`, em
`outputs/audit/<run_id>.jsonl` e na tabela `audit_events` do DuckDB. Parâmetros passam pelo
mascarador de dados pessoais antes de serem gravados.

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

`--setup` é idempotente: o download reaproveita o cache local.

| Comando | Efeito |
|---|---|
| `python main.py` | Relatório nacional completo |
| `python main.py --uf SP` | Recorte por unidade federativa |
| `python main.py --classification 5` | Apenas SRAG por covid-19 |
| `python main.py --no-llm` | Interpretação determinística, sem credencial |
| `python main.py --audit <run_id>` | Trilha de auditoria de uma execução |
| `python main.py --setup --years 2026` | Prepara apenas um ano |
| `python main.py --setup --csv arquivo.csv` | Usa um CSV já em disco, sem baixar nada |

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
| `MIN_CELL_SIZE` | `5` | Piso de contagem para agregados |
| `SRAG_YEARS` | `2025,2026` | Anos processados |
| `NEWS_MAX_AGE_DAYS` | `45` | Janela de notícias |
| `DATA_ROOT` | — | Redireciona `data/` e `outputs/` |

## 13. Exemplos

Execução de referência (dados de 24/08/2026, recorte nacional, corte analítico em 2026-08-02):

| Indicador | Valor | Detalhe |
|---|---|---|
| Taxa de aumento de casos | **−32,39 %** | 24.642 casos (jul/04–ago/02) contra 36.446 (jun/04–jul/03) |
| Taxa de mortalidade | **5,25 %** | 921 óbitos em 17.535 casos encerrados; 6.571 ainda em aberto |
| Taxa de admissão em UTI | **27,07 %** | 5.833 de 21.548 hospitalizados com UTI informado |
| Cobertura vacinal (covid-19) | **39,9 %** | 9.748 de 24.433; completude da informação 99,2 % |
| Ocupação de leitos de UTI | **não calculável** | o dataset não registra capacidade instalada |
| Vacinação da população | **não calculável** | denominador populacional exigiria fonte externa |

A queda calculada é corroborada — sem influenciar o cálculo — pelo contexto externo recuperado:
*"InfoGripe: maior parte do país tem tendência de queda ou estabilização"* (Fiocruz, 13/08/2026) e
*"Leitos extras de UTI para síndrome respiratória grave serão fechados após queda nos casos"* (G1,
29/08/2026).

Gráficos gerados: `outputs/charts/casos_diarios.png` e `outputs/charts/casos_mensais.png`.

## 14. Testes

```bash
python -m pytest -q          # 148 testes, ~30 s
```

A suíte roda sobre uma **base sintética de valores conhecidos**, montada em diretório temporário:
não exige os 603 MB nem acesso à rede, e permite afirmar resultados exatos — inclusive os casos de
borda que raramente aparecem em volume suficiente na base real.

| Arquivo | Cobre |
|---|---|
| `test_preprocessing.py` | Contrato de colunas, parse de datas nos dois formatos, código 9, idade implausível, marcação sem exclusão |
| `test_metrics.py` | Os 4 indicadores com valores exatos, denominador zero, filtro sem resultado, mês parcial |
| `test_database.py` | Coerência das flags derivadas com o dicionário, conexão somente leitura, binding de parâmetros |
| `test_tools.py` | Envelope completo, parâmetros inválidos, falha de tool, auditoria e mascaramento |
| `test_guardrails.py` | As 6 políticas, com caso bloqueado **e** caso legítimo (falso positivo importa) |
| `test_agent.py` | Grafo completo, planejamento, recusa na entrada, substituição de saída reprovada, degradação de notícias |

## 15. Estrutura do projeto

```
main.py                      entrypoint
src/
  config.py                  configuração centralizada (.env)
  data/        schema.py · download.py · preprocess.py · load_database.py
  metrics/     definitions.py · filters.py · epidemiology.py · timeseries.py
  tools/       schemas.py · registry.py · metric_tools.py · series_tools.py
               chart_tools.py · news_tools.py
  news/        rss_client.py · embeddings.py · vector_store.py · ingest.py
  guardrails/  policies.py · pii.py · input_guard.py · output_guard.py
  observability/ logging_config.py · audit.py
  agent/       state.py · llm.py · nodes.py · graph.py · orchestrator.py · report.py
  visualization/ charts.py
data/          raw/ · processed/ · analytics/          (não versionado)
outputs/       reports/ · charts/ · audit/             (não versionado)
docs/          arquitetura.pdf · dicionario_metricas.md · regras_transformacao.md
               catalogo_tools.md · gerar_diagrama_pdf.py · gerar_documentacao.py
tests/         6 arquivos · 148 testes · fixture sintética
```

## 16. Limitações

**Dos dados.** O SIVEP-Gripe cobre casos de SRAG **notificados**, majoritariamente hospitalizados —
nenhum indicador representa a população geral, e não há denominador populacional para calcular
incidência. A série recente é incompleta por atraso de notificação. A mortalidade é letalidade entre
casos encerrados, e a janela recente é instável enquanto muitos casos seguem em aberto. Ocupação de
leitos de UTI e cobertura vacinal populacional não são calculáveis (§7).

**Da implementação.** O embedding local (`hashing-ngram-local`) agrupa por vocabulário compartilhado,
não por sinonímia — a busca de notícias é notavelmente melhor com `OPENAI_API_KEY`. O acervo de
notícias depende do que os feeds do Google News expõem no momento da ingestão. O censo diário de UTI
imputa a permanência de quem não tem data de saída até a data de evolução ou de corte, o que
superestima os dias mais recentes. O guardrail de evidência isenta inteiros de 0 a 31 e anos de 2019
a 2030, para não bloquear frases legítimas como "os 4 indicadores".

**Do escopo.** PoC de execução local, sem autenticação, API ou agendamento.

## 17. Próximos passos

1. **Denominadores externos** — integrar CNES (leitos habilitados) e SI-PNI + IBGE, tornando
   calculáveis os dois indicadores hoje declarados indisponíveis.
2. **Nowcasting do atraso de notificação** — estimar a subnotificação recente a partir da
   distribuição de atraso já medida, publicando intervalo de confiança em vez de apenas descartar a
   janela.
3. **Séries plurianuais** — carregar 2019–2026 para comparação sazonal e detecção de anomalia contra
   a linha de base histórica.
4. **Recorte por faixa etária** — o dado já está na camada analítica; falta expô-lo como parâmetro
   de tool, com supressão por `MIN_CELL_SIZE`.
5. **Auditoria consultável** — carregar os JSONL em `audit_events` ao final de cada execução e expor
   um painel de execuções.
6. **Camada de apresentação** — API ou interface web sobre o mesmo orquestrador.

---

> Este projeto apresenta análise epidemiológica agregada de dados públicos de vigilância. Não
> constitui diagnóstico, prescrição, recomendação terapêutica nem orientação de conduta clínica
> individual.
