# Regras de transformacao dos dados

> Documento gerado por `python docs/gerar_documentacao.py` a partir das definicoes do codigo. Nao edite a mao: altere a fonte e regere.

- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Dicionario oficial:** https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/dicionario-de-dados-2019-a-2025.pdf
- **Gerado em:** 2026-09-13

> A semantica dos campos foi conferida em **duas versoes independentes** do dicionario oficial (a publicada com o dataset 2019-2026 e a versao `Dicionario_de_Dados_SRAG_Hospitalizado`). A numeracao dos campos na ficha difere entre elas -- `CLASSI_FIN` e o campo 78 numa e 80 na outra -- mas os dominios dos codigos sao identicos, inclusive o de `UTI` (`Internado em UTI?`, 1-Sim/2-Nao/9-Ignorado), que sustenta a decisao de nao chamar aquele indicador de taxa de ocupacao.

## Principio

Nenhum registro e removido ou alterado silenciosamente. Toda regra aplicada e contabilizada em `data/processed/quality_report.json`, e registros inconsistentes sao **marcados** (`flag_data_invalida`), nao excluidos da camada processada.

## Minimizacao de dados

O arquivo bruto possui 194 colunas. Apenas **28** sao lidas do disco; as demais nunca entram em memoria.

### Colunas lidas

| Coluna | Dominio |
|--------|---------|
| `DT_NOTIFIC` | data |
| `DT_SIN_PRI` | data |
| `DT_INTERNA` | data |
| `DT_ENTUTI` | data |
| `DT_SAIDUTI` | data |
| `DT_EVOLUCA` | data |
| `DT_ENCERRA` | data |
| `DT_DIGITA` | data |
| `CS_SEXO` | texto ou numero |
| `CS_RACA` | 1=Branca, 2=Preta, 3=Amarela, 4=Parda, 5=Indigena, 9=Ignorado |
| `CS_GESTANT` | 1=1o trimestre, 2=2o trimestre, 3=3o trimestre, 4=Idade gestacional ignorada, 5=Nao, 6=Nao se aplica, 9=Ignorado |
| `TP_IDADE` | 1=Dia, 2=Mes, 3=Ano |
| `FATOR_RISC` | 1=Sim, 2=Nao, 9=Ignorado |
| `HOSPITAL` | 1=Sim, 2=Nao, 9=Ignorado |
| `UTI` | 1=Sim, 2=Nao, 9=Ignorado |
| `SUPORT_VEN` | 1=Sim, invasivo, 2=Sim, nao invasivo, 3=Nao, 9=Ignorado |
| `CLASSI_FIN` | 1=SRAG por influenza, 2=SRAG por outro virus respiratorio, 3=SRAG por outro agente etiologico, 4=SRAG nao especificado, 5=SRAG por covid-19 |
| `CRITERIO` | 1=Laboratorial, 2=Clinico epidemiologico, 3=Clinico, 4=Clinico imagem |
| `EVOLUCAO` | 1=Cura, 2=Obito, 3=Obito por outras causas, 9=Ignorado |
| `VACINA` | 1=Sim, 2=Nao, 9=Ignorado |
| `VACINA_COV` | 1=Sim, 2=Nao, 9=Ignorado |
| `SG_UF_NOT` | texto ou numero |
| `SG_UF` | texto ou numero |
| `NU_IDADE_N` | texto ou numero |
| `SEM_PRI` | texto ou numero |
| `DOSE_1_COV` | data |
| `DOSE_2_COV` | data |
| `DOSE_REF` | data |

### Colunas proibidas (nunca lidas)

Enumeradas explicitamente para que a decisao de nao processa-las fique auditavel. Um teste de regressao falha caso alguma delas alcance a camada analitica.

| Coluna | Motivo da exclusao |
|--------|--------------------|
| `CLASSI_OUT` | texto livre sobre agente etiologico |
| `COD_IDADE` | idade codificada com granularidade desnecessaria |
| `CO_MUN_NOT` | codigo do municipio de notificacao |
| `CO_MUN_RES` | codigo do municipio de residencia |
| `CO_MU_INTE` | codigo do municipio da unidade de internacao |
| `CO_PS_VGM` | local de viagem internacional |
| `CS_ETINIA` | etnia indigena (dado sensivel, nao necessario as metricas) |
| `DT_NASC` | data de nascimento (quase-identificador direto) |
| `ESTRANG` | indicador de nacionalidade estrangeira |
| `ID_MN_INTE` | municipio da unidade de internacao |
| `ID_MN_RESI` | municipio de residencia |
| `ID_MUNICIP` | municipio de notificacao (risco de reidentificacao) |
| `ID_REGIONA` | regional de saude de notificacao |
| `ID_RG_RESI` | regional de saude de residencia |
| `LOTE_1_COV` | lote do imunizante (rastreavel ao individuo) |
| `LOTE_2_COV` | lote do imunizante (rastreavel ao individuo) |
| `LOTE_REF` | lote do imunizante (rastreavel ao individuo) |
| `LO_PS_VGM` | local de viagem internacional |
| `MORB_DESC` | texto livre sobre comorbidades do paciente |
| `NM_UN_INTE` | nome da unidade de saude de internacao |
| `NU_NOTIFIC` | identificador individual da notificacao |
| `OBES_IMC` | IMC individual do paciente |
| `OUTRO_DES` | texto livre sobre outros sintomas |
| `OUT_MORBI` | texto livre sobre outras morbidades |
| `PAC_COCBO` | ocupacao do paciente (quase-identificador) |
| `PAC_DSCBO` | descricao da ocupacao do paciente |
| `PAIS_VGM` | historico de viagem internacional |
| `TEM_CPF` | indicador de presenca de CPF |

## Regras aplicadas

| # | Regra | Comportamento | Registro no relatorio de qualidade |
|---|-------|---------------|-------------------------------------|
| 1 | Parse de datas | Tres formatos ja publicados pela fonte para o mesmo campo: `2026-04-30T00:00:00.000Z`, `2024-12-29` e `30/04/2026` (este ultimo apenas como fallback, para nao criar ambiguidade dia/mes) | `datas_nao_parseaveis_por_coluna` |
| 2 | Codigos de ausencia | O codigo [9] (Ignorado) e preservado e excluido de numeradores e denominadores; nunca vira `Nao` nem zero | `codigo_9_ignorado_por_coluna` |
| 3 | Normalizacao de idade | `NU_IDADE_N` + `TP_IDADE` convertidos para anos; valores fora de [0, 120] anulados | `idade_fora_do_intervalo_plausivel` |
| 4 | Agregacao de idade | Faixas: 0-4, 5-11, 12-17, 18-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+ | - |
| 5 | Validacao de UF | Valor fora das 27 siglas e anulado | `uf_fora_do_dominio` |
| 6 | Coerencia por dimensao | Quatro flags independentes (detalhadas abaixo) em vez de um unico veredito de validade | `coherence_flags` |
| 7 | Recorte analitico | A view `srag_analytics` exclui apenas os registros com `flag_data_invalida`; a tabela `srag_cases` preserva todos | diferenca entre as duas contagens |

## Flags de coerencia

Um unico booleano `registro invalido` seria grosseiro demais: uma data de internacao impossivel nao deveria excluir o registro da contagem de casos, que depende apenas de `DT_SIN_PRI`. Na base de referencia isso descartaria 4.452 registros por um defeito irrelevante para a maior parte das metricas.

Por isso a coerencia e avaliada por dimensao. Cada metrica exclui somente o que compromete o seu proprio calculo, e o volume de cada problema aparece no relatorio em vez de sumir num descarte agregado.

| Flag | Exclui da view analitica | Significado |
|------|--------------------------|-------------|
| `flag_data_invalida` | sim | eixo temporal primario inutilizavel: DT_SIN_PRI ausente, anterior ao inicio da serie ou posterior a data de digitacao. Unica flag que exclui o registro da view analitica, porque sem ela nenhuma metrica pode situar o caso no tempo. |
| `flag_internacao_inconsistente` | nao | DT_INTERNA anterior aos primeiros sintomas, ou HOSPITAL=1 sem data de internacao. Afeta apenas indicadores que dependem da internacao. |
| `flag_uti_inconsistente` | nao | DT_ENTUTI anterior aos primeiros sintomas, DT_SAIDUTI anterior a DT_ENTUTI, ou UTI=1 sem data de entrada. Afeta o censo de UTI, que depende da permanencia. |
| `flag_evolucao_inconsistente` | nao | DT_EVOLUCA anterior aos primeiros sintomas, ou caso encerrado (EVOLUCAO em 1,2,3) sem data de evolucao. Nao afeta a taxa de mortalidade, que usa o codigo e nao a data, mas afeta a imputacao de permanencia em UTI. |

## Janela de analise

Toda janela temporal e ancorada na **maior data de digitacao da base** (`max(DT_DIGITA)`), nunca em `today()`: a base publicada tem defasagem em relacao ao dia corrente. Da data de referencia sao descontados `REPORTING_LAG_DAYS` dias, para nao confundir atraso de notificacao com queda real de casos. A tool `get_notification_completeness` mede os percentis do atraso observado e sinaliza quando o corte configurado e menor que o percentil 75.
