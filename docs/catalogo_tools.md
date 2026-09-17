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
| `get_incidence_rate` | indicador | `uf`, `classification`, `window_days` | Incidencia de SRAG notificada por 100 mil habitantes na janela analisada, com denominador populacional do IBGE. Permite comparar UFs de tamanhos diferentes. |
| `get_seasonal_baseline` | indicador | `uf`, `classification`, `window_days` | Excesso de casos sobre o baseline sazonal: variacao da janela atual em relacao a mediana da mesma janela de calendario nos anos de referencia (2020-2021 excluidos). Distingue surto de sazonalidade. |
| `get_notification_completeness` | diagnostico | `uf`, `classification`, `window_days` | Perfil do atraso de notificacao observado na base (percentis em dias) e verificacao de suficiencia do corte analitico configurado. |
| `get_daily_cases` | serie | `uf`, `classification`, `window_days` | Serie diaria de casos de SRAG na janela analisavel (padrao: 30 dias). |
| `get_monthly_cases` | serie | `uf`, `classification`, `window_months` | Serie mensal de casos de SRAG na janela analisavel (padrao: 12 meses). |
| `render_daily_cases_chart` | grafico | `uf`, `classification` | Gera o grafico PNG do numero diario de casos dos ultimos 30 dias. |
| `render_monthly_cases_chart` | grafico | `uf`, `classification` | Gera o grafico PNG do numero mensal de casos dos ultimos 12 meses. |
| `search_srag_news` | contexto_externo | `query`, `top_k`, `max_age_days` | Busca semantica de noticias recentes sobre SRAG, surtos respiratorios, influenza, covid-19 e pressao hospitalar, no acervo de fontes confiaveis atualizado antes de cada relatorio. Em falha de rede, consulta o cache persistido. Contexto externo apenas: nao altera nenhum indicador. |

## Guardrails

| # | Politica | Verificada em | Descricao |
|---|----------|---------------|-----------|
| 1 | Sem diagnóstico ou conduta clínica | validate_request (entrada) e generate_interpretation, passo apply_output_guardrails (saída) | O sistema produz análise epidemiológica agregada. Não emite diagnóstico, prescrição, recomendação terapêutica nem orientação de conduta clínica individual. |
| 2 | Proteção de dados pessoais | schema de ingestão, regra de célula pequena nas tools, auditoria, generate_interpretation (varredura da saída) e generate_report (cabeçalho) | Colunas identificáveis nunca são lidas do dataset; toda saída passa por varredura de identificadores (CPF, CNS, e-mail, telefone). O agente consulta apenas agregados por período, UF e classificação; não existe tool para recuperar registros individuais. |
| 3 | Toda afirmação quantitativa precisa de evidência | validate_evidence e generate_interpretation, passo apply_output_guardrails | Números presentes na interpretação são confrontados com os valores efetivamente retornados pelas tools. Valor sem lastro bloqueia a publicação do relatório. |
| 4 | Sem SQL arbitrário gerado pelo modelo | camada de tools e conexão DuckDB | Não existe tool que execute consulta livre. O modelo escolhe tools e preenche parâmetros tipados, validados contra domínios fechados; o SQL é literal no código e recebe valores por binding. O banco é aberto em modo somente leitura. |
| 5 | Notícias não sobrescrevem dados oficiais | estado do grafo e renderização do relatório | Notícias circulam em campo próprio do estado e entram no relatório apenas sob o rótulo CONTEXTO EXTERNO. Nenhum indicador é calculado, ajustado ou corrigido a partir de conteúdo jornalístico. |
| 6 | Declarar indisponibilidade em vez de extrapolar | camada de métricas e generate_report | Quando uma métrica não pode ser calculada com segurança, o sistema declara a indisponibilidade e o motivo. Nunca substitui ausência por zero, média ou estimativa. |
| 7 | Revisão semântica independente da saída | generate_interpretation, passo apply_output_guardrails, após as verificações lexicais; somente sobre texto produzido por modelo | Depois das verificações lexicais, o texto do modelo é entregue a um revisor independente (outra chamada de modelo, prompt próprio, sem acesso ao pedido original) que procura conduta clínica parafraseada e dado individual -- achados bloqueantes -- e, em caráter consultivo, obediência a instruções vindas de notícias e extrapolação de indicador indisponível, que viram aviso. Indisponibilidade do revisor é declarada no relatório e a camada lexical permanece (fail-open). |
