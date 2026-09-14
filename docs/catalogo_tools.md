# Catalogo de tools e guardrails

> Documento gerado por `python docs/gerar_documentacao.py` a partir das definicoes do codigo. Nao edite a mao: altere a fonte e regere.
> Reproduzivel: o conteudo depende apenas do codigo, nao da data de geracao. O CI falha se este arquivo divergir do que o codigo produz.


## Tools

Todas as tools sao deterministicas, com schema de entrada fechado (`extra=forbid`) e envelope de saida padronizado. Nao existe tool de consulta livre ao banco.

| Tool | Categoria | Parametros | Descricao |
|------|-----------|------------|-----------|
| `get_case_growth_rate` | indicador | `uf`, `classification`, `window_days` | Taxa de aumento de casos de SRAG: variacao percentual entre a janela mais recente analisavel e a janela anterior de mesmo tamanho, pela data dos primeiros sintomas. |
| `get_mortality_rate` | indicador | `uf`, `classification`, `window_days` | Taxa de mortalidade por SRAG: obitos (EVOLUCAO=2) sobre casos encerrados elegiveis (EVOLUCAO em 1,2,3), no periodo analisado. |
| `get_icu_metrics` | indicador | `uf`, `classification`, `window_days` | Indicadores de UTI: taxa de admissao em UTI entre hospitalizados por SRAG e censo diario de pacientes em UTI. A taxa de ocupacao de leitos NAO e calculavel com este dataset e retorna nula com o motivo. |
| `get_vaccination_metrics` | indicador | `uf`, `classification`, `window_days` | Cobertura vacinal declarada (covid-19 e influenza) entre casos notificados de SRAG. A taxa de vacinacao da POPULACAO nao e calculavel com este dataset e retorna nula com o motivo. |
| `get_notification_completeness` | diagnostico | `uf`, `classification`, `window_days` | Perfil do atraso de notificacao observado na base (percentis em dias) e verificacao de suficiencia do corte analitico configurado. |
| `get_daily_cases` | serie | `uf`, `classification`, `window_days` | Serie diaria de casos de SRAG na janela analisavel (padrao: 30 dias). |
| `get_monthly_cases` | serie | `uf`, `classification`, `window_months` | Serie mensal de casos de SRAG na janela analisavel (padrao: 12 meses). |
| `render_daily_cases_chart` | grafico | `uf`, `classification` | Gera o grafico PNG do numero diario de casos dos ultimos 30 dias. |
| `render_monthly_cases_chart` | grafico | `uf`, `classification` | Gera o grafico PNG do numero mensal de casos dos ultimos 12 meses. |
| `search_srag_news` | contexto_externo | `query`, `top_k`, `max_age_days` | Busca semantica de noticias recentes sobre SRAG, surtos respiratorios, influenza, covid-19 e pressao hospitalar, no acervo de fontes confiaveis atualizado antes de cada relatorio. Em falha de rede, consulta o cache persistido. Contexto externo apenas: nao altera nenhum indicador. |

## Guardrails

| # | Politica | Verificada em | Descricao |
|---|----------|---------------|-----------|
| 1 | Sem diagnostico ou conduta clinica | validate_request (entrada) e generate_interpretation, passo apply_output_guardrails (saida) | O sistema produz analise epidemiologica agregada. Nao emite diagnostico, prescricao, recomendacao terapeutica nem orientacao de conduta clinica individual. |
| 2 | Protecao de dados pessoais | schema de ingestao, regra de celula pequena nas tools, auditoria, generate_interpretation (varredura da saida) e generate_report (cabecalho) | Colunas identificaveis nunca sao lidas do dataset; toda saida passa por varredura de identificadores (CPF, CNS, e-mail, telefone). O agente consulta apenas agregados por periodo, UF e classificacao; nao existe tool para recuperar registros individuais. |
| 3 | Toda afirmacao quantitativa precisa de evidencia | validate_evidence e generate_interpretation, passo apply_output_guardrails | Numeros presentes na interpretacao sao confrontados com os valores efetivamente retornados pelas tools. Valor sem lastro bloqueia a publicacao do relatorio. |
| 4 | Sem SQL arbitrario gerado pelo modelo | camada de tools e conexao DuckDB | Nao existe tool que execute consulta livre. O modelo escolhe tools e preenche parametros tipados, validados contra dominios fechados; o SQL e literal no codigo e recebe valores por binding. O banco e aberto em modo somente leitura. |
| 5 | Noticias nao sobrescrevem dados oficiais | estado do grafo e renderizacao do relatorio | Noticias circulam em campo proprio do estado e entram no relatorio apenas sob o rotulo CONTEXTO EXTERNO. Nenhum indicador e calculado, ajustado ou corrigido a partir de conteudo jornalistico. |
| 6 | Declarar indisponibilidade em vez de extrapolar | camada de metricas e generate_report | Quando uma metrica nao pode ser calculada com seguranca, o sistema declara a indisponibilidade e o motivo. Nunca substitui ausencia por zero, media ou estimativa. |
