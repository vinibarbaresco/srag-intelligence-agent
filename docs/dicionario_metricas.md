# Dicionario de metricas

> Documento gerado por `python docs/gerar_documentacao.py` a partir das definicoes do codigo. Nao edite a mao: altere a fonte e regere.
> Reproduzivel: o conteudo depende apenas do codigo, nao da data de geracao. O CI falha se este arquivo divergir do que o codigo produz.

- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Dataset:** https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026
- **Dicionario oficial de dados:** https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/dicionario-de-dados-2019-a-2025.pdf

## Visao geral

| Metrica | Campo(s) | Numerador | Denominador | Calculavel |
|---------|----------|-----------|-------------|------------|
| `case_growth_rate` | DT_SIN_PRI, DT_DIGITA | casos com DT_SIN_PRI na janela atual menos casos na janela anterior | casos com DT_SIN_PRI na janela anterior | sim |
| `mortality_rate` | EVOLUCAO, DT_SIN_PRI | casos com EVOLUCAO = 2 (Obito por SRAG) | casos com EVOLUCAO em (1-Cura, 2-Obito, 3-Obito por outras causas) | sim |
| `icu_admission_rate` | UTI, HOSPITAL, DT_SIN_PRI | hospitalizados com UTI = 1 (Sim) | hospitalizados com UTI informado (1-Sim ou 2-Nao) | sim |
| `icu_bed_occupancy_rate` | - | leitos de UTI ocupados | leitos de UTI disponiveis (capacidade instalada) | nao |
| `icu_patient_census` | DT_ENTUTI, DT_SAIDUTI, DT_EVOLUCA | pacientes de SRAG com permanencia em UTI cobrindo o dia | nao aplicavel (contagem absoluta, nao proporcao) | sim |
| `vaccination_coverage_among_cases` | VACINA_COV, VACINA, DT_SIN_PRI | casos com vacinacao declarada como 1-Sim | casos com a informacao vacinal preenchida (1-Sim ou 2-Nao) | sim |
| `population_vaccination_coverage` | - | pessoas vacinadas na populacao de referencia | populacao total de referencia | nao |
| `daily_cases` | DT_SIN_PRI | casos com DT_SIN_PRI igual ao dia | nao aplicavel (contagem absoluta) | sim |
| `monthly_cases` | DT_SIN_PRI | casos com DT_SIN_PRI no mes | nao aplicavel (contagem absoluta) | sim |

## Detalhamento

### `case_growth_rate` - Taxa de aumento de casos

- **Definicao:** Variacao percentual do numero de casos de SRAG entre duas janelas consecutivas de mesmo tamanho, medidas pela data dos primeiros sintomas: (casos_periodo_atual - casos_periodo_anterior) / casos_periodo_anterior x 100.
- **Numerador:** casos com DT_SIN_PRI na janela atual menos casos na janela anterior
- **Denominador:** casos com DT_SIN_PRI na janela anterior
- **Campos utilizados:** `DT_SIN_PRI`, `DT_DIGITA`
- **Periodo:** duas janelas consecutivas de GROWTH_WINDOW_DAYS dias (padrao: 30)
- **Unidade:** %
- **Tratamento de dados ausentes:** Registros sem DT_SIN_PRI ou com linha do tempo inconsistente ficam fora da view analitica e sao contabilizados no relatorio de qualidade.

**Limitacoes:**

- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- Mede variacao de casos notificados, nao incidencia populacional: nao ha denominador populacional no dataset.
- Quando a janela anterior tem zero casos, a variacao percentual e indefinida e o indicador retorna valor nulo.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `mortality_rate` - Taxa de mortalidade por SRAG (letalidade entre casos encerrados)

- **Definicao:** Proporcao de obitos por SRAG entre os casos encerrados elegiveis: obitos (EVOLUCAO = 2) / casos encerrados (EVOLUCAO em 1, 2 ou 3) x 100.
- **Numerador:** casos com EVOLUCAO = 2 (Obito por SRAG)
- **Denominador:** casos com EVOLUCAO em (1-Cura, 2-Obito, 3-Obito por outras causas)
- **Campos utilizados:** `EVOLUCAO`, `DT_SIN_PRI`
- **Periodo:** casos com primeiros sintomas na janela analisada
- **Unidade:** %
- **Tratamento de dados ausentes:** EVOLUCAO = 9 (Ignorado) e EVOLUCAO nulo ficam fora do numerador e do denominador. Casos ainda em aberto tambem nao entram no denominador, para nao subestimar a letalidade.

**Limitacoes:**

- Trata-se de letalidade (case fatality ratio) entre casos notificados de SRAG, nao de mortalidade populacional por SRAG.
- Casos recentes ainda sem encerramento ficam fora do denominador. Como obitos costumam ser encerrados antes das curas, a letalidade da janela recente tende a ser SUPERESTIMADA; o percentual de casos em aberto e publicado junto do indicador para dimensionar esse vies.
- EVOLUCAO = 3 (obito por outras causas) entra no denominador como caso encerrado, mas nao no numerador.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `icu_admission_rate` - Taxa de admissao em UTI entre hospitalizados por SRAG

- **Definicao:** Proporcao de pacientes hospitalizados por SRAG que foram internados em UTI: UTI = 1 / (UTI em 1 ou 2), restrito a HOSPITAL = 1.
- **Numerador:** hospitalizados com UTI = 1 (Sim)
- **Denominador:** hospitalizados com UTI informado (1-Sim ou 2-Nao)
- **Campos utilizados:** `UTI`, `HOSPITAL`, `DT_SIN_PRI`
- **Periodo:** casos com primeiros sintomas na janela analisada
- **Unidade:** %
- **Tratamento de dados ausentes:** UTI = 9 (Ignorado) e UTI nulo sao excluidos do numerador e do denominador, e o volume de ignorados e reportado junto do resultado.

**Limitacoes:**

- ATENCAO: este indicador NAO e taxa de ocupacao de leitos de UTI. O campo 53 do SIVEP-Gripe ('Internado em UTI?') registra se houve admissao em UTI, nao a ocupacao da capacidade instalada.
- Mede severidade clinica dos casos notificados, nao pressao sobre a rede hospitalar.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `icu_bed_occupancy_rate` - Taxa de ocupacao de leitos de UTI

- **Definicao:** Proporcao de leitos de UTI ocupados em relacao a capacidade instalada: leitos_ocupados / leitos_totais x 100.
- **Numerador:** leitos de UTI ocupados
- **Denominador:** leitos de UTI disponiveis (capacidade instalada)
- **Campos utilizados:** nenhum
- **Periodo:** nao aplicavel
- **Unidade:** %
- **Tratamento de dados ausentes:** nao aplicavel

> **NAO CALCULAVEL COM ESTE DATASET.** Nao e possivel calcular taxa de ocupacao de UTI com os dados disponiveis: o SIVEP-Gripe nao registra capacidade instalada nem leitos ocupados. Como aproximacao, sao reportados a taxa de admissao em UTI entre hospitalizados e o censo diario de pacientes de SRAG em UTI.

**Limitacoes:**

- O dataset SRAG do SIVEP-Gripe nao contem capacidade instalada de leitos nem contagem de leitos ocupados por unidade de saude.
- Um denominador de capacidade exigiria fonte externa (CNES / leitos habilitados), fora do escopo desta PoC.

### `icu_patient_census` - Censo diario de pacientes de SRAG em UTI

- **Definicao:** Numero de pacientes de SRAG presentes em UTI em cada dia, contado como DT_ENTUTI <= dia <= coalesce(DT_SAIDUTI, DT_EVOLUCA, data de referencia).
- **Numerador:** pacientes de SRAG com permanencia em UTI cobrindo o dia
- **Denominador:** nao aplicavel (contagem absoluta, nao proporcao)
- **Campos utilizados:** `DT_ENTUTI`, `DT_SAIDUTI`, `DT_EVOLUCA`
- **Periodo:** serie diaria na janela analisada
- **Unidade:** pacientes
- **Tratamento de dados ausentes:** Registros com UTI = 1 mas sem DT_ENTUTI nao entram no censo e sao contabilizados a parte, pois a permanencia e desconhecida.

**Limitacoes:**

- E um censo de pacientes, nao uma taxa de ocupacao: nao ha denominador de leitos.
- Pacientes sem data de saida registrada tem a permanencia imputada ate a data de evolucao ou ate a data de referencia, o que superestima o censo nos dias mais recentes.
- Cobre apenas pacientes de SRAG notificados, nao a ocupacao total da UTI.

### `vaccination_coverage_among_cases` - Cobertura vacinal declarada entre casos notificados de SRAG

- **Definicao:** Proporcao de casos notificados de SRAG com vacinacao declarada: VACINA_COV = 1 / (VACINA_COV em 1 ou 2) para covid-19, e VACINA = 1 / (VACINA em 1 ou 2) para influenza.
- **Numerador:** casos com vacinacao declarada como 1-Sim
- **Denominador:** casos com a informacao vacinal preenchida (1-Sim ou 2-Nao)
- **Campos utilizados:** `VACINA_COV`, `VACINA`, `DT_SIN_PRI`
- **Periodo:** casos com primeiros sintomas na janela analisada
- **Unidade:** %
- **Tratamento de dados ausentes:** Codigo 9 (Ignorado) e valores nulos sao excluidos de numerador e denominador; a proporcao de nao informados e devolvida junto ao resultado porque ela condiciona a leitura do indicador.

**Limitacoes:**

- ATENCAO: este indicador NAO e taxa de vacinacao da populacao. O denominador sao casos notificados de SRAG (majoritariamente hospitalizados), um grupo com perfil de risco distinto da populacao geral -- ha vies de selecao por definicao.
- A informacao e declarada no momento da notificacao e depende da apresentacao da caderneta; a subnotificacao de doses e conhecida.
- A cobertura vacinal populacional exigiria fonte externa (SI-PNI / localizaSUS) e denominador demografico (IBGE), fora do escopo da PoC.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `population_vaccination_coverage` - Taxa de vacinacao da populacao

- **Definicao:** Proporcao da populacao de referencia com esquema vacinal completo: pessoas vacinadas / populacao total x 100.
- **Numerador:** pessoas vacinadas na populacao de referencia
- **Denominador:** populacao total de referencia
- **Campos utilizados:** nenhum
- **Periodo:** nao aplicavel
- **Unidade:** %
- **Tratamento de dados ausentes:** nao aplicavel

> **NAO CALCULAVEL COM ESTE DATASET.** Nao e possivel calcular a taxa de vacinacao da populacao com os dados disponiveis: o SIVEP-Gripe so contem informacao vacinal de pessoas notificadas com SRAG, o que nao representa a populacao geral. Como aproximacao, e reportada a cobertura vacinal declarada entre os casos notificados, com o viés de selecao explicitado.

**Limitacoes:**

- O dataset SRAG cobre apenas pessoas que adoeceram e foram notificadas; nao ha qualquer denominador populacional.
- O calculo exigiria integrar SI-PNI (doses aplicadas) e estimativas populacionais do IBGE.

### `daily_cases` - Numero diario de casos de SRAG

- **Definicao:** Contagem de casos de SRAG por data dos primeiros sintomas.
- **Numerador:** casos com DT_SIN_PRI igual ao dia
- **Denominador:** nao aplicavel (contagem absoluta)
- **Campos utilizados:** `DT_SIN_PRI`
- **Periodo:** ultimos 30 dias da janela analisavel
- **Unidade:** casos
- **Tratamento de dados ausentes:** Dias sem nenhum caso aparecem com valor zero, nao omitidos.

**Limitacoes:**

- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- A serie e por data de inicio de sintomas, nao por data de notificacao: muda a forma da curva em relacao a series publicadas por data de registro.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `monthly_cases` - Numero mensal de casos de SRAG

- **Definicao:** Contagem de casos de SRAG por mes da data dos primeiros sintomas.
- **Numerador:** casos com DT_SIN_PRI no mes
- **Denominador:** nao aplicavel (contagem absoluta)
- **Campos utilizados:** `DT_SIN_PRI`
- **Periodo:** ultimos 12 meses da janela analisavel
- **Unidade:** casos
- **Tratamento de dados ausentes:** Meses sem casos aparecem com valor zero, nao omitidos.

**Limitacoes:**

- O mes mais recente costuma estar incompleto, tanto por atraso de notificacao quanto por ser um mes parcial.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.
