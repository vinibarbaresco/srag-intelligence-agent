> **Certificação AI Engineering - Vinícius Barbaresco** -- Arquivo entregue: `docs/relatorio_de_entrega.md`

# Relatório de entrega — rodada multiagente de 2026-09-18

Referência: [PR #4](https://github.com/vinibarbaresco/srag-intelligence-agent/pull/4), commit
[`804bc33`](https://github.com/vinibarbaresco/srag-intelligence-agent/commit/804bc33),
mesclado em `main` no commit
[`aa7ec55`](https://github.com/vinibarbaresco/srag-intelligence-agent/commit/aa7ec55).

Este documento é o relatório de entrega exigido pelo processo multiagente adotado nesta rodada:
inventário e auditoria factual antes da implementação, contratos revisados, implementação paralela
sem sobreposição de arquivos, integração única e validação final — incluindo dois defeitos que só
apareceram ao rodar o sistema de verdade, não nos testes automatizados.

## 1. Resumo executivo

Dos 10 gaps apontados na auditoria inicial, a investigação factual (7 auditorias paralelas,
somente leitura) confirmou que metade já estava correta no código — nomenclatura de UTI,
orçamento/allowlist de tool calling, trilha de auditoria, janela de notícias no pipeline real
(45 dias, não 30). O trabalho desta rodada focou nos gaps reais e, durante a validação por
execução real (não só testes automatizados), surgiram e foram corrigidos dois defeitos que a
suíte de testes não pegava: um falso-positivo do novo guardrail de causalidade que derrubava a
seção de letalidade em 3 de 4 execuções reais com LLM, e um vazamento de estrutura técnica bruta
(exceção do SDK da OpenAI) no relatório publicado quando a credencial falha. Nenhuma fórmula
epidemiológica foi alterada. O repositório termina com 963 testes (0 skips, 0 falhas), lint e
formatação limpos, PDF de arquitetura em dia, e quatro execuções reais (`--no-llm`, com LLM, com
credencial inválida, com múltiplas iterações do guardrail causal) confirmando o comportamento fim
a fim.

## 2. Arquivos modificados

**Código:** `src/agent/nodes.py`; `src/agent/report.py` dividido em pacote
`src/agent/report/` (7 módulos: `__init__`, `formatting`, `markdown_sections`, `html_dashboard`,
`theme`, `markdown_to_html`, `writer`); `src/api/app.py`; `src/config.py`;
`src/data/reference/vaccination.py`; `src/guardrails/{output_guard,policies,sanitize,semantic_judge}.py`;
`src/news/{rss_client,vector_store}.py`; `src/tools/news_tools.py`.

**Testes:** `tests/test_{api,data_quality_decisions,guardrails,news_resilience,red_team,semantic_judge,tools,vaccination_reference}.py`
(modificados) + `tests/test_llm_tool_selection.py` (novo).

**Dados/proveniência:** `data/reference/cobertura_vacinal_uf.csv` + `.provenance.json` (novos, reais).

**Infra:** `.github/workflows/{ci,monitor}.yml`; `Dockerfile`; `Makefile`;
`requirements.lock.txt` + `requirements-dev.lock.txt` (novos).

**Docs:** `README.md`; `docs/README.md`; `docs/dicionario_datasus_manifesto.json` (novo);
`.env.example`.

## 3. Decisões metodológicas

- **UTI**: nenhuma mudança de fórmula — a separação admissão/censo/ocupação já estava correta;
  só um texto do relatório (`_limitation_callouts_html`) mentia sobre calculabilidade. Corrigido
  sem tocar em `src/metrics/`.
- **Vacinação populacional**: aprovado usar 1 extrato real (fevereiro/2026, o menor disponível)
  em vez dos 12 meses do ano — declarado como cobertura **parcial** na proveniência
  (`cobertura_temporal_observacao`), nunca escondido. Durante a agregação real surgiram 3
  correções factuais no agregador, documentadas com evidência no próprio código: encoding
  `cp1252` (não `utf-8`), coluna `ds_nome` (não `ds_vacina`, que não existe no extrato oficial), e
  exclusão de "Haemophilus influenzae" (vacina Hib/Penta) que contaminava a contagem de influenza
  por substring. São correções de bug contra a fonte real, não mudança de metodologia.
- **Causalidade**: "resultou em" foi aprovado, testado e **removido** da lista de conectivos após
  achado real de execução (ver item 7) — decisão registrada em `src/guardrails/policies.py` e no
  `README.md`.

## 4. Evidência — vacinação populacional (antes indisponível)

Execução real (`python main.py --no-llm`, banco recarregado com
`python -m src.data.load_database`):

```
Taxa de vacinacao da populacao (referencia externa, ano 2026):
- covid19: 0.2% (427874 doses sobre 214211951, populacao residente total (IBGE 2026))
- influenza: 0.19% (403884 doses sobre 214211951, ...)
```

`tests/test_vaccination_reference.py::test_referencia_versionada_no_repositorio_e_valida_se_existir`
— antes `pytest.skip`, agora roda e passa. Proveniência real em
`data/reference/cobertura_vacinal_uf.provenance.json` (SHA-256 do extrato oficial, contagens de
descarte auditáveis).

## 5. Evidência — nomenclatura correta da ocupação por SRAG

`icu_admission_rate` / `icu_patient_census` / `icu_bed_occupancy_rate` ("Taxa de ocupação de
leitos de UTI por pacientes de SRAG") consistentes em `src/metrics/definitions.py`, API,
`docs/dicionario_metricas.md` e relatório — confirmado por auditoria dedicada, sem mudança de
código. Bug real corrigido: `src/agent/report/html_dashboard.py` não diz mais "não é possível
calcular" quando o indicador está calculado.

## 6. Evidência — notícias dentro da janela temporal

Relatório real mostrou: `"Janela configurada: ate 365 dias antes da execucao"` — refletindo o
`.env` local usado na sessão de validação (`NEWS_MAX_AGE_DAYS=365`, acima do padrão documentado
de 45). A transparência introduzida nesta rodada torna essa configuração visível no relatório em
vez de invisível. Testes novos confirmam rejeição de notícia fora da janela
(`tests/test_news_resilience.py::TestJanelaDeNoticias`).

## 7. Evidência — grounding e bloqueio de causalidade (com achado real corrigido)

Snippet do próprio feed RSS (nunca segunda requisição HTTP) chega sanitizado ao LLM; a URL
continua fora do prompt. Guardrail causal bloqueia conectivo sem atribuição
(`tests/test_red_team.py::TestCausalidadeIndevida`).

**Achado de execução real:** rodando o LLM 4 vezes, 3 foram bloqueadas por falso-positivo em
"resultou em" descrevendo `mortality_rate` (ex.: *"uma proporção significativa resultou em
óbito"* — descritivo, não causal). Corrigido removendo o padrão da lista de conectivos; **14
execuções subsequentes, 0 bloqueios indevidos**.

**Segundo achado de execução real:** rodando com uma credencial OpenAI inválida, o relatório
publicado vazava `"Error code: N - {'error': {...}}"` (estrutura bruta da exceção do SDK) em vez
da frase de domínio esperada. Corrigido em `src/guardrails/sanitize.py`
(`public_reason` agora recusa qualquer texto que ainda contenha `{`/`}` após a limpeza), com teste
de regressão usando a exceção real observada.

## 8. Evidência — tool calling opcional

Allowlist, orçamento e trilha de auditoria já estavam testados; o gap fechado nesta rodada foi a
ausência de teste que mockasse o cliente OpenAI real dentro de `OpenAIInterpreter.select_tools`.
`tests/test_llm_tool_selection.py` (novo) prova retry com sucesso, esgotamento de tentativas
(`RuntimeError` → fallback determinístico) e parada exata em `agent_max_tool_iterations` — antes
só um `FakeSelector` sintético cobria esses laços.

## 9. Gate final

```
python -m pytest -q                    → 963 passed, 0 skipped, 0 failed
python -m ruff check .                 → All checks passed!
python -m ruff format --check .        → 119 files already formatted
python docs/verificar_diagrama_pdf.py  → conteudo em dia com o codigo
git diff --stat docs/ (pós gerar_documentacao.py) → 0 drift do gerador
```

Cenários reais executados (não só testes automatizados):

| Cenário | Resultado |
|---|---|
| Relatório nacional completo sem LLM | ✅ |
| Relatório com LLM (14+ execuções após a correção do guardrail causal) | ✅ 0 bloqueios indevidos |
| LLM com credencial inválida | ✅ fallback determinístico limpo, sem vazamento técnico |
| SI-PNI ausente → SI-PNI válido | ✅ |
| CNES válido (ocupação de UTI real, ~7,7%) | ✅ |
| Prompt injection / SQL injection | ✅ golden set: 60/60 casos corretos |
| Notícias indisponíveis / banco indisponível / dados ausentes / dados inconsistentes | ✅ via suíte de testes dedicada |

## 10. Divergências remanescentes

- SI-PNI cobre só **1 mês** (fevereiro/2026), não o ano civil completo — declarado em toda parte
  relevante (proveniência, README), com o comando de extensão documentado
  (`python -m src.data.reference.vaccination --from-pni <extratos> --year <ano>`).
- Segunda versão do dicionário DATASUS (`Dicionario_de_Dados_SRAG_Hospitalizado`) sem URL/hash
  rastreável nesta sessão — declarado como tal em `docs/dicionario_datasus_manifesto.json`, não
  inventado.
- Duplicação estilística menor entre `_format_value` (Markdown) e `_comparator_value_html` (HTML)
  no pacote `report/` — comportamento preservado deliberadamente, já que unificar mudaria a
  formatação visível do relatório Markdown.

## 11. Nota técnica estimada

**8,5–9/10.** Os quatro indicadores obrigatórios e os dois complementares calculam corretamente
com dado real, incluindo os dois que dependiam de fonte externa (ocupação de UTI via CNES e
vacinação populacional via SI-PNI); guardrails, auditoria e comportamento agentic têm cobertura de
falha real, não só do caminho feliz; zero testes ignorados; zero drift de documentação. O que evita
o 10: cobertura do SI-PNI de 1 mês em vez do ano civil completo (limitação real, declarada, não
escondida) e a duplicação estilística menor mencionada no item 10.

## 12. Recomendação

**Pronto para submissão.** Os dois achados de execução real (falso-positivo do guardrail causal e
vazamento de estrutura técnica) eram exatamente o tipo de defeito que só aparece rodando o sistema
de verdade — e ambos foram corrigidos, testados e verificados por execução real antes desta
entrega, não deixados para depois.
