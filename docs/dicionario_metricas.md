# Dicionario de metricas

> **Certificação AI Engineering - Vinícius Barbaresco** -- Arquivo entregue: `docs/dicionario_metricas.md`

> Documento gerado por `python docs/gerar_documentacao.py` a partir das definicoes do codigo. Nao edite a mao: altere a fonte e regere.
> Reproduzivel: o conteudo depende apenas do codigo, nao da data de geracao. O CI falha se este arquivo divergir do que o codigo produz.

- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Dataset:** https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026
- **Dicionario oficial de dados:** https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/dicionario-de-dados-2019-a-2025.pdf

## Visao geral

| Metrica | Campo(s) | Numerador | Denominador | Calculavel |
|---------|----------|-----------|-------------|------------|
| `case_growth_rate` | DT_SIN_PRI, DT_DIGITA | casos com DT_SIN_PRI na janela atual menos casos na janela anterior | casos com DT_SIN_PRI na janela anterior digitados ate o fechamento dessa janela mais REPORTING_LAG_DAYS dias | sim |
| `mortality_rate` | EVOLUCAO, DT_SIN_PRI | casos com EVOLUCAO = 2 (Obito por SRAG) | casos com EVOLUCAO em (1-Cura, 2-Obito, 3-Obito por outras causas) | sim |
| `icu_admission_rate` | UTI, HOSPITAL, DT_SIN_PRI | internados com UTI = 1 (Sim) | internados com UTI informado (1-Sim ou 2-Nao) | sim |
| `icu_bed_occupancy_rate` | DT_ENTUTI, DT_SAIDUTI, DT_EVOLUCA, UTI | pacientes de SRAG presentes em UTI no dia (censo diario, SIVEP-Gripe) | leitos de UTI adulto e pediatrica existentes na UF na competencia do CNES compativel com a janela (capacidade instalada) | sim |
| `icu_patient_census` | DT_ENTUTI, DT_SAIDUTI, DT_EVOLUCA | pacientes de SRAG com permanencia em UTI cobrindo o dia | nao aplicavel (contagem absoluta, nao proporcao) | sim |
| `vaccination_coverage_among_cases` | VACINA_COV, VACINA, DT_SIN_PRI | casos com vacinacao declarada como 1-Sim | casos com a informacao vacinal preenchida (1-Sim ou 2-Nao) | sim |
| `population_vaccination_coverage` | - | doses aplicadas na campanha, na UF e no ano de referencia (SI-PNI) | populacao-alvo da campanha ou populacao residente (IBGE) | sim |
| `incidence_rate` | DT_SIN_PRI, SG_UF | casos com DT_SIN_PRI na janela analisada, pela UF de residencia (SG_UF) | populacao residente estimada (IBGE) da UF ou do Brasil, no ano mais proximo | sim |
| `seasonal_excess` | DT_SIN_PRI | casos na janela atual menos a mediana dos anos de baseline na mesma janela | mediana dos casos na mesma janela de calendario nos anos de baseline | sim |
| `daily_cases` | DT_SIN_PRI | casos com DT_SIN_PRI igual ao dia | nao aplicavel (contagem absoluta) | sim |
| `monthly_cases` | DT_SIN_PRI | casos com DT_SIN_PRI no mes | nao aplicavel (contagem absoluta) | sim |

## Detalhamento

### `case_growth_rate` - Taxa de aumento de casos

- **Definicao:** Variacao percentual do numero de casos de SRAG entre duas janelas consecutivas de mesmo tamanho, medidas pela data dos primeiros sintomas: (casos_periodo_atual - casos_periodo_anterior) / casos_periodo_anterior x 100.
- **Numerador:** casos com DT_SIN_PRI na janela atual menos casos na janela anterior
- **Denominador:** casos com DT_SIN_PRI na janela anterior digitados ate o fechamento dessa janela mais REPORTING_LAG_DAYS dias
- **Campos utilizados:** `DT_SIN_PRI`, `DT_DIGITA`
- **Periodo:** duas janelas consecutivas de GROWTH_WINDOW_DAYS dias (padrao: 30)
- **Unidade:** %
- **Tratamento de dados ausentes:** Registros sem DT_SIN_PRI ou com linha do tempo inconsistente ficam fora da view analitica e sao contabilizados no relatorio de qualidade. Registros da janela anterior digitados depois do prazo de observacao dela ficam fora do denominador e sao publicados em `casos_excluidos_por_imaturidade`.

**Limitacoes:**

- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- As duas janelas sao comparadas com maturidade simetrica: cada uma e contada como era conhecida REPORTING_LAG_DAYS dias apos o proprio fechamento. Sem isso a janela atual teria menos tempo de digitacao que a anterior e o crescimento sairia subestimado de forma sistematica. As contagens sem censura ficam publicadas ao lado, nos componentes.
- Mede variacao de casos notificados, nao incidencia populacional: nao ha denominador populacional no dataset.
- Quando a janela anterior tem zero casos, a variacao percentual e indefinida e o indicador retorna valor nulo.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `mortality_rate` - Letalidade entre casos encerrados de SRAG

- **Definicao:** Proporcao de obitos por SRAG entre os casos encerrados elegiveis: obitos (EVOLUCAO = 2) / casos encerrados (EVOLUCAO em 1, 2 ou 3) x 100.
- **Numerador:** casos com EVOLUCAO = 2 (Obito por SRAG)
- **Denominador:** casos com EVOLUCAO em (1-Cura, 2-Obito, 3-Obito por outras causas)
- **Campos utilizados:** `EVOLUCAO`, `DT_SIN_PRI`
- **Periodo:** casos com primeiros sintomas na janela analisada
- **Unidade:** %
- **Tratamento de dados ausentes:** EVOLUCAO = 9 (Ignorado) e EVOLUCAO nulo ficam fora do numerador e do denominador. Casos ainda em aberto tambem nao entram no denominador, para nao subestimar a letalidade.

**Limitacoes:**

- Trata-se de letalidade (case fatality ratio) entre casos notificados de SRAG, nao de mortalidade populacional por SRAG.
- Casos recentes ainda sem encerramento ficam fora do denominador. Como obitos costumam ser encerrados antes das curas, a letalidade da janela recente tende a ser SUPERESTIMADA; o percentual de casos em aberto e o percentual encerrado sao publicados junto do indicador para dimensionar esse vies.
- Duas janelas com percentuais de encerramento diferentes NAO sao diretamente comparaveis: a variacao entre elas pode ser artefato de maturacao, e nao mudanca real de gravidade. Por isso o indicador publica em `coorte_madura` a mesma taxa sobre uma janela deslocada o tempo tipico ate o encerramento (percentil 90 medido na propria base), com o percentual encerrado das duas. Na base de referencia a janela recente marca 7,86% com 68,8% encerrado, contra 6,04% com 85,5% na coorte madura -- a diferenca e maturacao, nao gravidade.
- EVOLUCAO = 3 (obito por outras causas) entra no denominador como caso encerrado, mas nao no numerador.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `icu_admission_rate` - Taxa de admissao em UTI entre internados por SRAG

- **Definicao:** Proporcao de pacientes internados por SRAG que foram admitidos em UTI: UTI = 1 / (UTI em 1 ou 2), restrito a internados. Um caso e considerado internado quando HOSPITAL = 1 **ou** quando ha admissao em UTI declarada (UTI = 1).
- **Numerador:** internados com UTI = 1 (Sim)
- **Denominador:** internados com UTI informado (1-Sim ou 2-Nao)
- **Campos utilizados:** `UTI`, `HOSPITAL`, `DT_SIN_PRI`
- **Periodo:** casos com primeiros sintomas na janela analisada
- **Unidade:** %
- **Tratamento de dados ausentes:** UTI = 9 (Ignorado) e UTI nulo sao excluidos do numerador e do denominador, e o volume de ignorados e reportado junto do resultado. HOSPITAL ausente ou igual a 9 (Ignorado) NAO e lido como 'nao internado': a base e de SRAG hospitalizada e uma admissao em UTI declarada e evidencia direta de internacao. Os registros recuperados por essa regra sao publicados em separado nos componentes, discriminados entre HOSPITAL ausente, ignorado e negado.

**Limitacoes:**

- ATENCAO: este indicador NAO e taxa de ocupacao de leitos de UTI. O campo 53 do SIVEP-Gripe ('Internado em UTI?') registra se houve admissao em UTI, nao a ocupacao da capacidade instalada.
- Mede severidade clinica dos casos notificados, nao pressao sobre a rede hospitalar.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `icu_bed_occupancy_rate` - Taxa de ocupacao de leitos de UTI por pacientes de SRAG

- **Definicao:** Proporcao da capacidade instalada de UTI ocupada por pacientes de SRAG em um dia: pacientes_srag_em_uti_no_dia / leitos_uti_disponiveis_no_dia x 100. O numerador vem do censo diario calculado sobre o SIVEP-Gripe; o denominador vem da capacidade instalada publicada pelo CNES para a UF e a competencia compativel com a janela analisada.
- **Numerador:** pacientes de SRAG presentes em UTI no dia (censo diario, SIVEP-Gripe)
- **Denominador:** leitos de UTI adulto e pediatrica existentes na UF na competencia do CNES compativel com a janela (capacidade instalada)
- **Campos utilizados:** `DT_ENTUTI`, `DT_SAIDUTI`, `DT_EVOLUCA`, `UTI`
- **Periodo:** dia de pico do censo maduro dentro da janela analisada; a serie diaria completa acompanha o resultado
- **Unidade:** %
- **Tratamento de dados ausentes:** Sem a referencia de capacidade carregada, sem leitos cadastrados para a UF, com competencia do CNES distante da janela alem de ICU_CAPACITY_MAX_LAG_MONTHS, ou com denominador zero, o indicador e declarado NAO CALCULAVEL com o motivo. Nunca se usa capacidade estimada, extrapolada ou de outro periodo sem declarar.

**Limitacoes:**

- MEDE APENAS A PARCELA DE SRAG. O numerador conta pacientes de SRAG notificados em UTI; os leitos do denominador tambem atendem pacientes sem SRAG (trauma, pos-operatorio, sepse de outras causas). O valor e portanto um PISO da ocupacao total de UTI, nao a ocupacao total. Um valor baixo NAO significa rede com folga.
- O numerador depende da imputacao de permanencia das estadias sem data de saida registrada; o percentual imputado no dia publicado acompanha o indicador.
- A capacidade do CNES e o cadastro de leitos, nao leitos operacionais no dia: leito cadastrado pode estar bloqueado por falta de equipe. O denominador tende a superestimar a capacidade efetiva e, com isso, a subestimar a ocupacao.
- Numerador e denominador tem defasagens diferentes e competencias distintas; a competencia usada e a distancia dela ate a janela sao publicadas com o valor.
- O recorte geografico e a UF de NOTIFICACAO, nao a de residencia: o leito e ocupado onde o paciente foi internado.
- Leitos de UTI neonatal, de queimados e coronariana ficam fora do denominador: sao unidades fechadas para outras condicoes e nao estao disponiveis para o paciente de SRAG. Os quantitativos continuam na referencia e podem ser auditados.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.

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
- A cobertura vacinal populacional e um indicador SEPARADO (`population_vaccination_coverage`), com numerador do SI-PNI e denominador demografico. Esta metrica nunca deve ser lida no lugar dela, nem usada como aproximacao dela.
- O SIVEP-Gripe registra casos de SRAG notificados, majoritariamente hospitalizados. Nenhum indicador aqui representa a populacao geral.

### `population_vaccination_coverage` - Taxa de vacinacao da populacao

- **Definicao:** Doses aplicadas na campanha (referencia externa SI-PNI, por UF e ano) sobre a populacao-alvo da campanha -- ou, na ausencia dela, sobre a populacao residente estimada pelo IBGE -- x 100.
- **Numerador:** doses aplicadas na campanha, na UF e no ano de referencia (SI-PNI)
- **Denominador:** populacao-alvo da campanha ou populacao residente (IBGE)
- **Campos utilizados:** nenhum
- **Periodo:** ano de referencia da campanha mais proximo da data de corte analitica
- **Unidade:** %
- **Tratamento de dados ausentes:** Sem o arquivo de referencia de doses aplicadas, o indicador e declarado nao calculavel com o motivo. Nunca e estimado a partir dos casos. Uma referencia presente mas sem `periodo_completo=true` declarado pelo operador tambem fica indisponivel: um extrato mensal isolado do SI-PNI nao pode ser apresentado como cobertura anual ou populacional.

**Limitacoes:**

- O SIVEP-Gripe nao contem este dado: numerador e denominador vem de fontes externas (SI-PNI e IBGE), com sua propria defasagem.
- Doses aplicadas sao um proxy de pessoas vacinadas; em campanhas de dose unica (influenza) a aproximacao e boa, em esquemas de multiplas doses (covid-19) ela superestima a cobertura.
- Quando nao ha populacao-alvo informada, o denominador e a populacao total, o que subestima a cobertura do publico-alvo.
- So e publicado quando a referencia declara cobertura de periodo completo da campanha (`periodo_completo=true`); um extrato parcial (ex.: um unico mes) fica indisponivel em vez de gerar uma taxa anual ou populacional sem base temporal equivalente.

### `incidence_rate` - Incidencia de SRAG notificada por 100 mil habitantes

- **Definicao:** Casos de SRAG com primeiros sintomas na janela analisada, por 100 mil habitantes: casos / populacao residente estimada (IBGE) x 100.000.
- **Numerador:** casos com DT_SIN_PRI na janela analisada, pela UF de residencia (SG_UF)
- **Denominador:** populacao residente estimada (IBGE) da UF ou do Brasil, no ano mais proximo
- **Campos utilizados:** `DT_SIN_PRI`, `SG_UF`
- **Periodo:** ultimos GROWTH_WINDOW_DAYS dias ate a data de corte analitica
- **Unidade:** por 100 mil hab. no periodo
- **Tratamento de dados ausentes:** Sem a referencia populacional carregada, o indicador e declarado nao calculavel; nunca se usa um denominador aproximado. Casos sem UF de residencia ficam fora do numerador quando ha recorte por UF, e o volume e publicado nos componentes.

**Limitacoes:**

- Incidencia de casos NOTIFICADOS de SRAG (majoritariamente hospitalizados), nao de infeccao respiratoria na populacao.
- O recorte geografico e a UF de RESIDENCIA, para casar com o denominador residente do IBGE. Os indicadores de carga assistencial (UTI, ventilacao, censo) usam a UF de NOTIFICACAO, porque o leito e ocupado onde o paciente foi internado. As duas dimensoes nao sao intercambiaveis e nunca devem ser cruzadas numa mesma tabela.
- A populacao e a estimativa anual do IBGE mais proxima da data de corte; o ano usado e publicado junto do indicador.
- Permite comparar UFs de tamanhos diferentes, o que a contagem absoluta nao permite.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.

### `seasonal_excess` - Excesso de casos sobre o baseline sazonal

- **Definicao:** Variacao percentual dos casos da janela atual em relacao a MEDIANA dos casos observados na mesma janela de calendario nos anos de baseline: (casos_atuais - mediana_baseline) / mediana_baseline x 100.
- **Numerador:** casos na janela atual menos a mediana dos anos de baseline na mesma janela
- **Denominador:** mediana dos casos na mesma janela de calendario nos anos de baseline
- **Campos utilizados:** `DT_SIN_PRI`
- **Periodo:** janela atual e a mesma janela (mes/dia) em cada ano de BASELINE_YEARS
- **Unidade:** %
- **Tratamento de dados ausentes:** Anos de baseline sem nenhum caso na base sao considerados ausentes e excluidos; com menos de BASELINE_MIN_YEARS anos presentes o indicador e declarado nao calculavel.

**Limitacoes:**

- 2020 e 2021 ficam fora do baseline por padrao: a pandemia de covid-19 multiplicou as notificacoes de SRAG e um baseline que os incluisse rotularia qualquer ano normal como 'abaixo do esperado'.
- Mede se a janela atual esta acima ou abaixo do padrao historico da mesma epoca do ano -- e o que distingue surto de sazonalidade. A taxa de aumento de casos, que compara janelas consecutivas, nao faz essa distincao.
- Mudancas de criterio de notificacao e de cobertura da vigilancia entre os anos afetam a comparacao; os anos efetivamente usados sao publicados.
- A serie recente e incompleta por atraso de notificacao: casos com sintomas nos ultimos dias ainda nao foram digitados. As janelas excluem os dias mais recentes (REPORTING_LAG_DAYS) e usam como referencia a maior data de digitacao da base, nunca a data de hoje.

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
