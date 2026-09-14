> Exemplo de relatorio gerado por `python main.py --no-llm` sobre a base do Open DATASUS (2022-2026), via deterministica. Caminhos locais substituidos por `<repo>`. Regere com `make demo`; o conteudo muda a cada republicacao da fonte.

# Relatorio epidemiologico de SRAG

- **Execucao (run_id):** `9c28d65e-1fcd-4d18-b6b9-8911e005abad`
- **Gerado em:** 2026-09-14 01:56 E. South America Standard Time
- **Solicitacao:** Gere o relatorio de monitoramento de SRAG com os indicadores de aumento de casos, mortalidade, UTI e vacinacao, as series diaria e mensal, e o contexto de noticias recentes.
- **Recorte:** BR (nacional) | todas as classificacoes finais
- **Fonte dos dados:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026) ([dataset](https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026))
- **Via de interpretacao:** `deterministic-template`

> Este relatorio apresenta analise epidemiologica agregada de dados publicos de vigilancia. Nao constitui diagnostico, prescricao, recomendacao terapeutica nem orientacao de conduta clinica individual.

## Resumo dos indicadores

| # | Indicador | Valor | Numerador | Denominador | Status |
|---|-----------|-------|-----------|-------------|--------|
| 1 | case_growth_rate | -35.38% | -10586 | 29922 | calculado |
| 2 | mortality_rate | 5.33% | 715 | 13404 | calculado |
| 3 | icu_admission_rate | 28.47% | 4845 | 17016 | calculado |
| 4 | vaccination_coverage_among_cases | 39.93% | 7636 | 19124 | calculado |
| 5 | incidence_rate | 9.03 por 100 mil hab. | 19336 | 214211951 | calculado |
| 6 | seasonal_excess | -12.08% | -2657 | 21993 | calculado |

## DADO - Alertas e acompanhamento

**Nivel consolidado: NORMAL.** Nenhuma regra de alerta disparada; indicadores dentro dos limiares configurados.

| Regra | Indicador | Observado | Limiar | Disparada |
|-------|-----------|-----------|--------|-----------|
| crescimento_de_casos | case_growth_rate | -35.38% | 20.0% | nao |
| letalidade | mortality_rate | 5.33% | 10.0% | nao |
| excesso_sazonal | seasonal_excess | -12.08% | 50.0% | nao |

### Variacao desde a execucao anterior

- **Execucao anterior:** `da8a8f6a-61af-43b3-b1ed-e202aaa3b2be` (gerada em 2026-09-14T04:54:43, corte 2026-08-23; corte atual 2026-08-23)

| Indicador | Anterior | Atual | Variacao |
|-----------|----------|-------|----------|
| case_growth_rate | -35.38 | -35.38 | 0.0 |
| mortality_rate | 5.33 | 5.33 | 0.0 |
| icu_admission_rate | 28.47 | 28.47 | 0.0 |
| vaccination_coverage_among_cases | 39.93 | 39.93 | 0.0 |
| incidence_rate | 9.03 | 9.03 | 0.0 |
| seasonal_excess | -12.08 | -12.08 | 0.0 |

*As duas execucoes usam a mesma data de corte analitica: a variacao reflete atualizacao da base pela fonte, nao passagem do tempo.*

## DADO - Indicadores epidemiologicos

### 1. Taxa de aumento de casos

**Valor:** -35.38%

- **Definicao:** Variacao percentual do numero de casos de SRAG entre duas janelas consecutivas de mesmo tamanho, medidas pela data dos primeiros sintomas: (casos_periodo_atual - casos_periodo_anterior) / casos_periodo_anterior x 100.
- **Numerador:** -10586
- **Denominador:** 29922
- **Registros na base do calculo:** 49258
- **Periodo:** 2026-06-25 a 2026-08-23 (janela atual 2026-07-25 a 2026-08-23 comparada a 2026-06-25 a 2026-07-24)
- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Casos na janela atual:** 19336
- **Casos na janela anterior:** 29922
- **Data de corte analitica:** 2026-08-23 (atraso mediano observado: 7.0 dias, p90: 27.0 dias)

<details><summary>Limitacoes declaradas</summary>

- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- Mede variacao de casos notificados, nao incidencia populacional: nao ha denominador populacional no dataset.
- Quando a janela anterior tem zero casos, a variacao percentual e indefinida e o indicador retorna valor nulo.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

</details>

### 2. Taxa de mortalidade

**Valor:** 5.33%

- **Definicao:** Proporcao de obitos por SRAG entre os casos encerrados elegiveis: obitos (EVOLUCAO = 2) / casos encerrados (EVOLUCAO em 1, 2 ou 3) x 100.
- **Numerador:** 715
- **Denominador:** 13404
- **Registros na base do calculo:** 19336
- **Periodo:** 2026-07-25 a 2026-08-23 (ultimos 30 dias ate a data de corte analitica)
- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Casos no periodo:** 19336
- **Obitos por SRAG:** 715
- **Obitos por outras causas:** 371
- **Casos ainda em aberto:** 5437
- **Evolucao ignorada (codigo 9):** 495

<details><summary>Limitacoes declaradas</summary>

- Trata-se de letalidade (case fatality ratio) entre casos notificados de SRAG, nao de mortalidade populacional por SRAG.
- Casos recentes ainda sem encerramento ficam fora do denominador. Como obitos costumam ser encerrados antes das curas, a letalidade da janela recente tende a ser SUPERESTIMADA; o percentual de casos em aberto e publicado junto do indicador para dimensionar esse vies.
- EVOLUCAO = 3 (obito por outras causas) entra no denominador como caso encerrado, mas nao no numerador.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

</details>

### 3. UTI (admissao e censo)

**Valor:** 28.47%

- **Definicao:** Proporcao de pacientes hospitalizados por SRAG que foram internados em UTI: UTI = 1 / (UTI em 1 ou 2), restrito a HOSPITAL = 1.
- **Numerador:** 4845
- **Denominador:** 17016
- **Registros na base do calculo:** 18572
- **Periodo:** 2026-07-25 a 2026-08-23 (ultimos 30 dias ate a data de corte analitica)
- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Hospitalizados no periodo:** 18572
- **Admitidos em UTI:** 4845
- **UTI ignorado (codigo 9):** 198
- **Pico do censo diario em UTI:** 3286 pacientes em 2026-07-27 (68.78% desse valor depende de imputacao de permanencia)
- **Admitidos em UTI sem o campo HOSPITAL preenchido:** 67 (entram no censo, nao na taxa de admissao)

**Qualidade da permanencia em UTI usada no censo** (estadias em UTI que intersectam a janela do censo):

- Estadias no censo: 8001
- Com data de saida registrada: 3121 (39.01%)
- Permanencia imputada pela data de evolucao: 2043
- **Permanencia imputada em aberto: 2837 (35.46%)**, das quais 1338 truncadas pelo teto de 31 dias (percentil 95% da permanencia das 261831 estadias com saida registrada)
- Estadias sem data de saida nem de evolucao sao tratadas como em curso ate o teto de permanencia. Sem o teto, pacientes admitidos meses antes contavam como internados ate a data de corte e inflavam o censo em ordem de grandeza.

> **Taxa de ocupacao de leitos de UTI: nao calculavel.** Nao e possivel calcular taxa de ocupacao de UTI com os dados disponiveis: o SIVEP-Gripe nao registra capacidade instalada nem leitos ocupados. Como aproximacao, sao reportados a taxa de admissao em UTI entre hospitalizados e o censo diario de pacientes de SRAG em UTI.

<details><summary>Limitacoes declaradas</summary>

- ATENCAO: este indicador NAO e taxa de ocupacao de leitos de UTI. O campo 53 do SIVEP-Gripe ('Internado em UTI?') registra se houve admissao em UTI, nao a ocupacao da capacidade instalada.
- Mede severidade clinica dos casos notificados, nao pressao sobre a rede hospitalar.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

</details>

### 4. Cobertura vacinal

**Valor:** 39.93%

- **Definicao:** Proporcao de casos notificados de SRAG com vacinacao declarada: VACINA_COV = 1 / (VACINA_COV em 1 ou 2) para covid-19, e VACINA = 1 / (VACINA em 1 ou 2) para influenza.
- **Numerador:** 7636
- **Denominador:** 19124
- **Registros na base do calculo:** 19336
- **Periodo:** 2026-07-25 a 2026-08-23 (ultimos 30 dias ate a data de corte analitica)
- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Covid-19:** 39.93% (7636 de 19124), completude da informacao 98.9%
- **Influenza:** 35.48% (6492 de 18300), completude da informacao 94.64%

> **Taxa de vacinacao da populacao: nao calculavel.** Referencia de doses aplicadas (SI-PNI) nao fornecida em data/reference/cobertura_vacinal_uf.csv. O SIVEP-Gripe so contem a informacao vacinal de pessoas notificadas com SRAG, que nao representa a populacao; sem a referencia externa o indicador nao e calculavel.

<details><summary>Limitacoes declaradas</summary>

- ATENCAO: este indicador NAO e taxa de vacinacao da populacao. O denominador sao casos notificados de SRAG (majoritariamente hospitalizados), um grupo com perfil de risco distinto da populacao geral -- ha vies de selecao por definicao.
- A informacao e declarada no momento da notificacao e depende da apresentacao da caderneta; a subnotificacao de doses e conhecida.
- A cobertura vacinal populacional exigiria fonte externa (SI-PNI / localizaSUS) e denominador demografico (IBGE), fora do escopo da PoC.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

</details>

### 5. Incidencia por 100 mil habitantes (complementar)

**Valor:** 9.03 por 100 mil hab.

- **Definicao:** Casos de SRAG com primeiros sintomas na janela analisada, por 100 mil habitantes: casos / populacao residente estimada (IBGE) x 100.000.
- **Numerador:** 19336
- **Denominador:** 214211951
- **Registros na base do calculo:** 19336
- **Periodo:** 2026-07-25 a 2026-08-23 (ultimos 30 dias ate a data de corte analitica)
- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026) (casos) e IBGE (populacao)
- **Casos na janela:** 19336
- **Populacao de referencia:** 214211951 (IBGE - populacao residente estimada (tabela 6579), estimativa de 2026)

<details><summary>Limitacoes declaradas</summary>

- Incidencia de casos NOTIFICADOS de SRAG (majoritariamente hospitalizados), nao de infeccao respiratoria na populacao.
- A populacao e a estimativa anual do IBGE mais proxima da data de corte; o ano usado e publicado junto do indicador.
- Permite comparar UFs de tamanhos diferentes, o que a contagem absoluta nao permite.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.

</details>

### 6. Excesso sobre o baseline sazonal (complementar)

**Valor:** -12.08%

- **Definicao:** Variacao percentual dos casos da janela atual em relacao a MEDIANA dos casos observados na mesma janela de calendario nos anos de baseline: (casos_atuais - mediana_baseline) / mediana_baseline x 100.
- **Numerador:** -2657
- **Denominador:** 21993
- **Registros na base do calculo:** 88774
- **Periodo:** 2026-07-25 a 2026-08-23 (janela atual de 30 dias comparada a mesma janela de calendario em 3 ano(s) de baseline)
- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Casos na janela atual:** 19336
- **Mediana do baseline:** 21993 (media: 23146.0)
- **Anos considerados:** [2022, 2023, 2024] (configurados: [2022, 2023, 2024]; ausentes na base: [])
- **Anos excluidos por definicao:** [2020, 2021] -- anos pandemicos de covid-19: volume de SRAG fora de qualquer padrao sazonal, incompativel com um baseline

| Ano | Janela comparada | Casos |
|-----|------------------|-------|
| 2022 | 2022-07-25 a 2022-08-23 | 28680 |
| 2023 | 2023-07-25 a 2023-08-23 | 18765 |
| 2024 | 2024-07-25 a 2024-08-23 | 21993 |

<details><summary>Limitacoes declaradas</summary>

- 2020 e 2021 ficam fora do baseline por padrao: a pandemia de covid-19 multiplicou as notificacoes de SRAG e um baseline que os incluisse rotularia qualquer ano normal como 'abaixo do esperado'.
- Mede se a janela atual esta acima ou abaixo do padrao historico da mesma epoca do ano -- e o que distingue surto de sazonalidade. A taxa de aumento de casos, que compara janelas consecutivas, nao faz essa distincao.
- Mudancas de criterio de notificacao e de cobertura da vigilancia entre os anos afetam a comparacao; os anos efetivamente usados sao publicados.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.

</details>


## DADO - Series temporais

### Casos diarios (2026-07-25 a 2026-08-23)

- Total no periodo: **19336** casos
- Media diaria: 644.53 | maximo: 823 | minimo: 563

### Casos mensais (2025-09-01 a 2026-08-23)

- Total no periodo: **292087** casos

| Mes | Casos | Observacao |
|-----|-------|------------|
| 2025-09 | 24341 |  |
| 2025-10 | 24030 |  |
| 2025-11 | 21011 |  |
| 2025-12 | 16349 |  |
| 2026-01 | 12824 |  |
| 2026-02 | 15912 |  |
| 2026-03 | 27092 |  |
| 2026-04 | 31530 |  |
| 2026-05 | 39321 |  |
| 2026-06 | 37384 |  |
| 2026-07 | 27541 |  |
| 2026-08 | 14752 | mes parcial |


## Visualizacoes

### Numero diario de casos de SRAG - ultimos 30 dias

![Numero diario de casos de SRAG - ultimos 30 dias](../charts/casos_diarios.png)

Arquivo: `<repo>\outputs\charts\casos_diarios.png`

### Numero mensal de casos de SRAG - ultimos 12 meses

![Numero mensal de casos de SRAG - ultimos 12 meses](../charts/casos_mensais.png)

Arquivo: `<repo>\outputs\charts\casos_mensais.png`


## Qualidade e tratamento dos dados

- **Carga (run_id):** `b7865012-1435-4500-b4ab-f1a3df4a6609`
- **Processada em:** 2026-09-14T04:12:32
- **Arquivos de origem:** INFLUD19-23-03-2026.csv, INFLUD22-23-03-2026.csv, INFLUD23-23-03-2026.csv, INFLUD24-23-03-2026.csv, INFLUD25-14-09-2026.csv, INFLUD26-14-09-2026.csv
- **Registros lidos:** 1.705.626
- **Registros descartados:** 0 (nenhum registro e removido silenciosamente)
- **Registros com valor alterado:** 17
- **Linhas identicas nas colunas lidas:** 16344 (contadas, nao deduplicadas: sem o identificador da notificacao, excluido por minimizacao, nao ha como distinguir duplicata de pacientes distintos com os mesmos atributos agregados)
- **Codigos fora do dominio do dicionario:** nenhum (contados, nao anulados; a camada de metricas so reconhece codigos validos)

### Proveniencia dos dados

| Ano | Arquivo | sha256 | Origem |
|-----|---------|--------|--------|
| 2019 | `INFLUD19-23-03-2026.csv` | `f6de547c1234e2de...` | download do Open DATASUS |
| 2022 | `INFLUD22-23-03-2026.csv` | `57250ce92b916b4f...` | download do Open DATASUS |
| 2023 | `INFLUD23-23-03-2026.csv` | `639b748fb2838336...` | download do Open DATASUS |
| 2024 | `INFLUD24-23-03-2026.csv` | `b516f6eae61203da...` | download do Open DATASUS |
| 2025 | `INFLUD25-14-09-2026.csv` | `a507a213d043d5aa...` | download do Open DATASUS |
| 2026 | `INFLUD26-14-09-2026.csv` | `333afa53758437ed...` | download do Open DATASUS |

### Alteracoes de valor aplicadas

Toda alteracao e registrada no proprio registro, na coluna `ajustes_aplicados`. Os registros afetados podem ser localizados individualmente: `SELECT * FROM srag_cases WHERE ajustes_aplicados <> ''`.

| Codigo | Registros | Significado |
|--------|-----------|-------------|
| `idade_anulada` | 17 | idade normalizada fora do intervalo plausivel [0, 120] anos; substituida por nulo |

### Flags de coerencia

Marcadas por dimensao, nao como um unico veredito. Apenas a flag do eixo temporal exclui o registro da camada analitica; as demais sao respeitadas somente pelas metricas que dependem daquela dimensao.

| Flag | Registros | % | Exclui da analise | Significado |
|------|-----------|---|-------------------|-------------|
| `flag_data_invalida` | 573 | 0.034% | sim | eixo temporal primario inutilizavel: DT_SIN_PRI ausente, anterior ao inicio da serie ou posterior a data de digitacao. Unica flag que exclui o registro da view analitica, porque sem ela nenhuma metrica pode situar o caso no tempo. |
| `flag_internacao_inconsistente` | 38507 | 2.258% | nao | DT_INTERNA anterior aos primeiros sintomas, ou HOSPITAL=1 sem data de internacao. Afeta apenas indicadores que dependem da internacao. |
| `flag_uti_inconsistente` | 15233 | 0.893% | nao | DT_ENTUTI anterior aos primeiros sintomas, DT_SAIDUTI anterior a DT_ENTUTI, ou UTI=1 sem data de entrada. Afeta o censo de UTI, que depende da permanencia. |
| `flag_evolucao_inconsistente` | 91081 | 5.34% | nao | DT_EVOLUCA anterior aos primeiros sintomas, ou caso encerrado (EVOLUCAO em 1,2,3) sem data de evolucao. Nao afeta a taxa de mortalidade, que usa o codigo e nao a data, mas afeta a imputacao de permanencia em UTI. |

### Codigo 9 (Ignorado) nos campos usados pelos indicadores

Preservado como esta e excluido de numeradores e denominadores; nunca convertido em `Nao` nem em zero.

| Campo | Registros com codigo 9 |
|-------|------------------------|
| `HOSPITAL` | 2762 |
| `UTI` | 19461 |
| `EVOLUCAO` | 36181 |
| `VACINA` | 289365 |
| `VACINA_COV` | 31606 |

## INFERENCIA - Interpretacao do cenario

*Texto produzido por `deterministic-template` a partir exclusivamente dos resultados das tools, validado pelo guardrail de evidencia.*

### 1. Panorama geral
A comparacao entre janelas consecutivas de 30 dias indica reducao de 35.38% no numero de casos de SRAG: 19336 casos na janela atual contra 29922 na anterior. No acumulado dos ultimos 12 meses foram 292087 casos notificados.
Isso corresponde a 9.03 casos notificados por 100 mil habitantes (populacao IBGE de 2026). Frente ao baseline sazonal, a janela esta 12.08% abaixo da mediana da mesma epoca em 3 anos de referencia (21993 casos), o que e compativel com a sazonalidade esperada.

### 2. Severidade e pressao assistencial
A letalidade entre casos encerrados foi de 5.33%: 715 obitos por SRAG em 13404 casos encerrados. Havia ainda 5437 casos sem encerramento no periodo, o que torna o indicador instavel na janela mais recente.
Entre os hospitalizados por SRAG, 28.47% foram internados em UTI (4845 de 17016 com a informacao preenchida). O censo diario de pacientes de SRAG em UTI atingiu pico de 3286 pacientes no periodo. Este indicador mede admissao em UTI, nao ocupacao de leitos: o dataset nao registra capacidade instalada.

### 3. Cobertura vacinal declarada
Entre os casos notificados com a informacao preenchida, 39.93% declararam vacinacao contra covid-19 (completude de 98.9%) e 35.48% contra influenza (completude de 94.64%). Trata-se de cobertura entre pessoas que adoeceram e foram notificadas, nao da cobertura vacinal da populacao.

### 4. Leitura do contexto externo
Foram recuperadas 12 noticias de fontes confiaveis no periodo. Entre elas: "Síndrome Respiratória Aguda Grave - SRAG - Prefeitura de São Paulo" (Prefeitura de São Paulo, 2026-08-14); "Síndrome Respiratória Aguda Grave - SRAG - prefeitura.sp.gov.br" (prefeitura.sp.gov.br, 2026-08-14); "RS e SC registram aumento de casos de SRAG por vírus respiratórios - CNN Brasil" (CNN Brasil, 2026-08-03). Esse material e contexto externo e nao altera nenhum dos indicadores calculados sobre os dados do DATASUS.

Este relatorio apresenta analise epidemiologica agregada de dados publicos de vigilancia. Nao constitui diagnostico, prescricao, recomendacao terapeutica nem orientacao de conduta clinica individual.

## CONTEXTO EXTERNO - Noticias recentes

> Noticias complementam a leitura do cenario. Elas **nao** alteram, corrigem nem substituem os indicadores calculados sobre os dados do DATASUS.

| Publicada em | Fonte | Titulo | URL | Recuperada em |
|--------------|-------|--------|-----|---------------|
| 2026-08-14 | Prefeitura de São Paulo | Síndrome Respiratória Aguda Grave - SRAG - Prefeitura de São Paulo | [link](https://news.google.com/rss/articles/CBMiigFBVV95cUxPRUxMeU9PR2dFMloyNG1qNVo2eHo4Q0RjVEV1akRmbGgwaXhLdUN2M3RlcHdMcHNzd240MDRkWVJXY1g1RVlWam5xMjQ3dWdPand2UUh4MHRxWlBzZjVNMWVCTE1Hd1dUN2JZUkxpM3hMcUJvblEwc2hMTXJGVUNndGNFSGs4NzRjTGc?oc=5) | 2026-09-14T01:38:15.238701+00:00 |
| 2026-08-14 | prefeitura.sp.gov.br | Síndrome Respiratória Aguda Grave - SRAG - prefeitura.sp.gov.br | [link](https://news.google.com/rss/articles/CBMiigFBVV95cUxPRUxMeU9PR2dFMloyNG1qNVo2eHo4Q0RjVEV1akRmbGgwaXhLdUN2M3RlcHdMcHNzd240MDRkWVJXY1g1RVlWam5xMjQ3dWdPand2UUh4MHRxWlBzZjVNMWVCTE1Hd1dUN2JZUkxpM3hMcUJvblEwc2hMTXJGVUNndGNFSGs4NzRjTGc?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-08-03 | CNN Brasil | RS e SC registram aumento de casos de SRAG por vírus respiratórios - CNN Brasil | [link](https://news.google.com/rss/articles/CBMipAFBVV95cUxPTC1tN09NenpNR1NZX3F6Z2VEU3Q4Yzg5RG1VTkF4d0JPQkppZWtvbFgtWXEweTlRVjFMMi1qVERHdWtrZ203Qm9vTTBzdzNBR2tWa0NEdVY0Y0NiT0NxdDlnMnRYYmtuQVdZeV8xMS1yRFVIMWhLOUR5ZHFId0Z6VXR2ZnlEVWxJekkteVNyMktjUmhNZ3JfbjhIRUdzeWlwYWcwRw?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-09-10 | Fundação Oswaldo Cruz (Fiocruz) | InfoGripe: cinco estados têm incidência de SRAG em nível de alerta e tendência de aumento - Fundação Oswaldo Cruz (Fiocruz) | [link](https://news.google.com/rss/articles/CBMitgFBVV95cUxNVzU1VjZFdHM4QTZnWWtabkNGeWpTVVZYcGdzNGotODlyeHU4VG15ai1jN2xrVVVja05KbDJfb25mMVhKd29rbWdMajRlWmdmMTdXNHRFUWM1Y3A4Vzh4RlppemtRWHJFV0hCSlZSaWctRkdMeUtlWHcxYnZZU2NKbUw0dnVZcnpTci03QzJhTG9XelBJUE9LQ0J1TDNKaXZ3R0NMU3VpdmlCNmc3SzluMDB0czA1Zw?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-09-04 | CNN Brasil | Casos de síndrome respiratória crescem em crianças e adolescentes no Brasil - CNN Brasil | [link](https://news.google.com/rss/articles/CBMisAFBVV95cUxQOGtEcGlOZ3dMak14RWlXWFgzMHlvNUlQY0gtX05FS3I3bmFlZGV2S1NjMlljYXBVWFZsRVh6UElhT1gxbEJ4LTBxdFpFd0NKZnVxLWg1dHY4YTVWLWpGdjlBWUZ2MW5xamE1Tk43OGU5WEYzRjRDdVdEcEZTQ1V3WnVqOXVsNHNrS1N4QUtwY3ZRR3h2dzdjTkQ5bmNLbkJjYm9ndVlJWkVxUXNTZm9ObA?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-08-13 | Fundação Oswaldo Cruz (Fiocruz) | InfoGripe: maior parte do país tem tendência de queda ou estabilização de casos de SRAG - Fundação Oswaldo Cruz (Fiocruz) | [link](https://news.google.com/rss/articles/CBMitwFBVV95cUxNZDFHTUROQ2dGSWtxaFRrOGlRcm1fbFgwV2UyTTF2U19VbUZUTGZsWWo5dDBSSnNRWk9ON1FORWJ1ck10V3R5dVNQWWZEc3BnODJUUEp1UGpWd3FadkhQTmM2WVVjdXNsN29aOWFjMkJUbmJuVzYybGczNzc1UTFYMjBBVlZwbTFVdXpPSzBaN2tJdkN3TlZrWEVwbzRZdTdFMkNuYmE1ci12SlVzRzAyZE15bHROdkk?oc=5) | 2026-09-14T04:52:59.421175+00:00 |
| 2026-09-03 | Agencia Brasil | Cresce número de casos de SRAG em crianças e adolescentes no país - Agencia Brasil | [link](https://news.google.com/rss/articles/CBMi1wFBVV95cUxPLXZvekVnMjJZNUJFbEg0MWlXS29URnpTQURONHB6T1NvMTg5djM5ajFoY25MQ3VSUHpOLW9oWjVYdTV2d05LZnk4OTA0RF80QXM2aGtXRE1DS0pvYUZuTk9RNDQ2cWNxZmI3RTFzWmhHQTNXRTFSZjZEa3JKekhESWNlTWdIUUd0WUZWTFNvTEJGSU52NG12b1JlbDZRSVN5UG5ZNTU4eXdhSkM1dmhINU4zZFg2ZTgxaldCc0N4cFRlXzM0cWV2NUVNc05WaUJwWk5JUWFBRQ?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-07-31 | Município de Corumbá | Saúde promove atualização sobre Síndrome Respiratória Aguda Grave para fortalecer atendimento na região - Município de Corumbá | [link](https://news.google.com/rss/articles/CBMi1AFBVV95cUxQc1BWUEdRQjZkN00yejdnR2ZjZlEzUG93YTd5S015STZnc1ZBbkFDQm9OQnB0V2tDZHVJYU1sQnBmMm9ZLWc4dHBObEdMZ1l4WS1JN1VKZGhnUlpiWnNKay1NaHBDWkxDSEtaNUp0c1p0NHc5Z1hHcWQ1dVI5ZHJMUmZzekJvMk1KcmtyRGxWYk5fNDI4dng2cDItUmgtVHNvUnR3ZWoxM2drWmJnT0JRNHZkeXFRSDdJNDVFcWM0SktQU29memhnRU8tMDYtbDhtS3FNXw?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-08-06 | Fundação Oswaldo Cruz (Fiocruz) | InfoGripe: número de casos de SRAG mantém tendência de queda em grande parte do país - Fundação Oswaldo Cruz (Fiocruz) | [link](https://news.google.com/rss/articles/CBMiugFBVV95cUxOSTNlY2VhZzFhUExtSWJseVVuWDZ1R0tjSkhCQnpiY1pkSzUteUg2M1VpdDZJa3Z2aVRmcnc1SC1hdXA5ajMtR1RwTm00V3RsVjVYWDhYak8xOGJ4dmEtOFFPRmU3MUN0QnpIMXczcHdpTEZ6cURWYWxOcHBTaHllaFl0QXNkb01SQlk3NnNKT3FJU1VpM2d2SER6SUhoZnRHb2RmenNvZGo3dEpRQ3dZaUNvVzlVcUhpRkE?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-08-29 | G1 | Leitos extras de UTI para síndrome respiratória grave serão fechados após queda nos casos em Três Corações, MG - G1 | [link](https://news.google.com/rss/articles/CBMigAJBVV95cUxOT05kOFpTV0ZkRXl5UjBFVXA0ZUotaFJqWVpQOU83NWV5Ylg2RzhPR2JBa1pZS2VYZ2ZGOHRjbHBMcWhLZVNKb2JWODlnZ3FTVktpZFZLQkN6R1pIMWlEUWJfckhUU294SXB0c0NBYTBnZ29sM2dyYi1CU3NpRWUtclBYRW9RRm9iUkdYLThRblBRbWhVSXItZFRmVFJ3R3Y1d2YyUVNKMUh2cVJaTUw2LXh2S3dGYWs1T2pmX3VhM1haNDhFLXBXT0pHYTg4REU1S1J1YU1FekhLMEJteE1XVkExbVpBU21kNVdMaF80SFZSZHg2Ynd3Y0RlTUJNTHhS0gGPAkFVX3lxTE9VZU01WUNFVHpndnZGTHdiak1IbXVYY2NwMHpwbFR1V0g3cHQ3alFMTjhjeUVYNzZpeXNfZ1JkTXdISFVSeHIwX1pDUXVmMl84R1JGWmZHeFc1M2FnSnVyV0RaUjVhV1dDeHlOck5yTnF1TWY1cE1mVWI4X3hQRXJGMzJGazlFdmtBVi1sN0VkVDhsanpiVC16ZHFuMFFIU3gwQXhheXFEYXROaUdQWW9ob1ZWTDl3VW42RUJUNGRkbUhGbmVyX243QXV1bE9felV0NFk4cG05VGZmQXZHMGZKYTlPQjFHNm9FVGkxRWZucmQyak5WS1BmR2FqUjNNMXFZUWZpNTJRZUN5MWdPS28?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-08-14 | prefeitura.sp.gov.br | Influenza B - prefeitura.sp.gov.br | [link](https://news.google.com/rss/articles/CBMiigFBVV95cUxNVzNyX2xZU0ZkTGZzckdpd1ZFcnh4a21RMk5ZazU2SmNSeTJJWTNaNEhVZlNqSG1tOVpPdEZfNG41Wms3aEMwLVB1RVdLNDlSNVVCbTgyZ0pnNU52eHdjWWF4bE5rLVlWTkUxTUtsNk4yRlFHVUJ6OFVvYjQ1d1J5bFNqeWRXdG4zanc?oc=5) | 2026-09-14T04:55:59.988220+00:00 |
| 2026-08-14 | Prefeitura de São Paulo | Influenza B - Prefeitura de São Paulo | [link](https://news.google.com/rss/articles/CBMiigFBVV95cUxNVzNyX2xZU0ZkTGZzckdpd1ZFcnh4a21RMk5ZazU2SmNSeTJJWTNaNEhVZlNqSG1tOVpPdEZfNG41Wms3aEMwLVB1RVdLNDlSNVVCbTgyZ0pnNU52eHdjWWF4bE5rLVlWTkUxTUtsNk4yRlFHVUJ6OFVvYjQ1d1J5bFNqeWRXdG4zanc?oc=5) | 2026-09-14T01:38:15.238701+00:00 |

*Acervo consultado: 13 noticias no Vector DB (backend de embedding: `openai:text-embedding-3-small`); ultima ingestao em 2026-09-14T04:55:59.988220.*

## Limitacoes conhecidas

- **Taxa de ocupacao de UTI nao e calculavel** com o SIVEP-Gripe: o dataset registra se houve admissao em UTI, nao a capacidade instalada nem os leitos ocupados. O relatorio apresenta a taxa de admissao em UTI entre hospitalizados e o censo diario de pacientes, ambos nomeados pelo que de fato medem.
- **Taxa de vacinacao da populacao nao e calculavel** com este dataset: ha informacao vacinal apenas de pessoas notificadas com SRAG, um grupo com vies de selecao. O relatorio apresenta a cobertura declarada entre casos notificados.
- **Atraso de notificacao**: a janela recente e incompleta; as analises descontam os dias mais recentes e ancoram o periodo na maior data de digitacao da base, nao na data de hoje.
- **Escopo**: o SIVEP-Gripe cobre casos de SRAG notificados, majoritariamente hospitalizados; nenhum indicador representa a populacao geral.

## Governanca, auditoria e guardrails

### Trilha de auditoria

- **run_id:** `9c28d65e-1fcd-4d18-b6b9-8911e005abad`
- **Eventos registrados:** 22
- **Duracao total:** 3488.46 ms
- **Status dos eventos:** {"ok": 22}
- **Arquivo:** `<repo>\outputs\audit\9c28d65e-1fcd-4d18-b6b9-8911e005abad.jsonl`

### Planejamento

- **Planejador:** `deterministic-template`
- **Tools no plano efetivo:** get_case_growth_rate, get_daily_cases, get_icu_metrics, get_incidence_rate, get_monthly_cases, get_mortality_rate, get_notification_completeness, get_seasonal_baseline, get_vaccination_metrics, render_daily_cases_chart, render_monthly_cases_chart, search_srag_news

### Verificacao de evidencia

- **Valores lastreados pelas tools:** 261
- **Indicadores calculados:** 6
- **Indicadores indisponiveis:** 0

### Guardrails ativos

| Politica | Verificada em | Descricao |
|----------|---------------|-----------|
| Sem diagnostico ou conduta clinica | validate_request (entrada) e generate_interpretation, passo apply_output_guardrails (saida) | O sistema produz analise epidemiologica agregada. Nao emite diagnostico, prescricao, recomendacao terapeutica nem orientacao de conduta clinica individual. |
| Protecao de dados pessoais | schema de ingestao, regra de celula pequena nas tools, auditoria, generate_interpretation (varredura da saida) e generate_report (cabecalho) | Colunas identificaveis nunca sao lidas do dataset; toda saida passa por varredura de identificadores (CPF, CNS, e-mail, telefone). O agente consulta apenas agregados por periodo, UF e classificacao; nao existe tool para recuperar registros individuais. |
| Toda afirmacao quantitativa precisa de evidencia | validate_evidence e generate_interpretation, passo apply_output_guardrails | Numeros presentes na interpretacao sao confrontados com os valores efetivamente retornados pelas tools. Valor sem lastro bloqueia a publicacao do relatorio. |
| Sem SQL arbitrario gerado pelo modelo | camada de tools e conexao DuckDB | Nao existe tool que execute consulta livre. O modelo escolhe tools e preenche parametros tipados, validados contra dominios fechados; o SQL e literal no codigo e recebe valores por binding. O banco e aberto em modo somente leitura. |
| Noticias nao sobrescrevem dados oficiais | estado do grafo e renderizacao do relatorio | Noticias circulam em campo proprio do estado e entram no relatorio apenas sob o rotulo CONTEXTO EXTERNO. Nenhum indicador e calculado, ajustado ou corrigido a partir de conteudo jornalistico. |
| Declarar indisponibilidade em vez de extrapolar | camada de metricas e generate_report | Quando uma metrica nao pode ser calculada com seguranca, o sistema declara a indisponibilidade e o motivo. Nunca substitui ausencia por zero, media ou estimativa. |
| Revisao semantica independente da saida | generate_interpretation, passo apply_output_guardrails, apos as verificacoes lexicais; somente sobre texto produzido por modelo | Depois das verificacoes lexicais, o texto do modelo e entregue a um revisor independente (outra chamada de modelo, prompt proprio, sem acesso ao pedido original) que procura conduta clinica parafraseada e dado individual -- achados bloqueantes -- e, em carater consultivo, obediencia a instrucoes vindas de noticias e extrapolacao de indicador indisponivel, que viram aviso. Indisponibilidade do revisor e declarada no relatorio e a camada lexical permanece (fail-open). |

**Resultado da validacao de saida:** aprovada
**Revisao semantica independente:** nao aplicavel: texto produzido pela via deterministica, sem modelo.

### Consumo do modelo de linguagem

Nenhuma chamada a modelo nesta execucao (via deterministica).