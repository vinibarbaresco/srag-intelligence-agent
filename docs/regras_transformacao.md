# Regras de transformacao dos dados

> Documento gerado por `python docs/gerar_documentacao.py` a partir das definicoes do codigo. Nao edite a mao: altere a fonte e regere.
> Reproduzivel: o conteudo depende apenas do codigo, nao da data de geracao. O CI falha se este arquivo divergir do que o codigo produz.

- **Fonte:** Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)
- **Dicionario oficial:** https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/dicionario-de-dados-2019-a-2025.pdf

> A semantica dos campos foi conferida em **duas versoes independentes** do dicionario oficial (a publicada com o dataset 2019-2026 e a versao `Dicionario_de_Dados_SRAG_Hospitalizado`). A numeracao dos campos na ficha difere entre elas -- `CLASSI_FIN` e o campo 78 numa e 80 na outra -- mas os dominios dos codigos sao identicos, inclusive o de `UTI` (`Internado em UTI?`, 1-Sim/2-Nao/9-Ignorado), que sustenta a decisao de nao chamar aquele indicador de taxa de ocupacao.

## Principio

Nenhum registro e removido ou alterado silenciosamente. Toda regra aplicada e contabilizada em `data/processed/quality_report.json`, e registros inconsistentes sao **marcados** (`flag_data_invalida`), nao excluidos da camada processada.

## Minimizacao de dados

O arquivo bruto possui 194 colunas. Apenas **22** sao lidas do disco; as demais nunca entram em memoria.

### Colunas lidas

| Coluna | Dominio |
|--------|---------|
| `DT_SIN_PRI` | data |
| `DT_INTERNA` | data |
| `DT_ENTUTI` | data |
| `DT_SAIDUTI` | data |
| `DT_EVOLUCA` | data |
| `DT_ENCERRA` | data |
| `DT_DIGITA` | data |
| `SEM_PRI` | texto ou numero |
| `CS_SEXO` | texto ou numero |
| `TP_IDADE` | 1=Dia, 2=Mes, 3=Ano |
| `HOSPITAL` | 1=Sim, 2=Nao, 9=Ignorado |
| `UTI` | 1=Sim, 2=Nao, 9=Ignorado |
| `SUPORT_VEN` | 1=Sim, invasivo, 2=Sim, nao invasivo, 3=Nao, 9=Ignorado |
| `NOSOCOMIAL` | 1=Sim, 2=Nao, 9=Ignorado |
| `CLASSI_FIN` | 1=SRAG por influenza, 2=SRAG por outro virus respiratorio, 3=SRAG por outro agente etiologico, 4=SRAG nao especificado, 5=SRAG por covid-19 |
| `CRITERIO` | 1=Laboratorial, 2=Clinico epidemiologico, 3=Clinico, 4=Clinico imagem |
| `EVOLUCAO` | 1=Cura, 2=Obito, 3=Obito por outras causas, 9=Ignorado |
| `VACINA` | 1=Sim, 2=Nao, 9=Ignorado |
| `VACINA_COV` | 1=Sim, 2=Nao, 9=Ignorado |
| `SG_UF_NOT` | texto ou numero |
| `SG_UF` | texto ou numero |
| `NU_IDADE_N` | texto ou numero |

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
| `CS_GESTANT` | idade gestacional (dado sensivel de saude sem uso nas metricas) |
| `CS_RACA` | raca/cor (dado sensivel; nenhum indicador estratifica por raca) |
| `DOSE_1_COV` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DOSE_2REF` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DOSE_2_COV` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DOSE_ADIC` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DOSE_REF` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DOS_RE_BI` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DT_1_DOSE` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DT_2_DOSE` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DT_DOSEUNI` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `DT_NASC` | data de nascimento (quase-identificador direto) |
| `DT_UT_DOSE` | data de dose vacinal (quase-identificador; so o indicador vacinal e usado) |
| `ESTRANG` | indicador de nacionalidade estrangeira |
| `FAB_ADIC` | fabricante do imunizante (compoe o historico vacinal individual) |
| `FAB_COVRF` | fabricante do imunizante (compoe o historico vacinal individual) |
| `FAB_COVRF2` | fabricante do imunizante (compoe o historico vacinal individual) |
| `FAB_COV_1` | fabricante do imunizante (compoe o historico vacinal individual) |
| `FAB_COV_2` | fabricante do imunizante (compoe o historico vacinal individual) |
| `FAB_RE_BI` | fabricante do imunizante (compoe o historico vacinal individual) |
| `ID_MN_INTE` | municipio da unidade de internacao |
| `ID_MN_RESI` | municipio de residencia |
| `ID_MUNICIP` | municipio de notificacao (risco de reidentificacao) |
| `ID_REGIONA` | regional de saude de notificacao |
| `ID_RG_RESI` | regional de saude de residencia |
| `LOTE_1_COV` | lote do imunizante (rastreavel ao individuo) |
| `LOTE_2_COV` | lote do imunizante (rastreavel ao individuo) |
| `LOTE_ADIC` | lote do imunizante (rastreavel ao individuo) |
| `LOTE_REF` | lote do imunizante (rastreavel ao individuo) |
| `LOTE_REF2` | lote do imunizante (rastreavel ao individuo) |
| `LOT_RE_BI` | lote do imunizante (rastreavel ao individuo) |
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

## Pipeline de tratamento

O tratamento e um pipeline declarado de regras nomeadas (`src/data/cleaning/`), nao um procedimento. Cada regra e uma classe com nome, descricao e teste proprio; a tabela abaixo e gerada da mesma estrutura que o codigo executa, entao nao pode divergir dele.

A ordem importa: datas antes da coerencia (que compara datas) e coerencia antes da semantica (porque a usabilidade de uma estadia em UTI depende da flag de coerencia).

| # | Regra | O que faz |
|---|-------|-----------|
| 1 | `parse_datas` | Converte as colunas de data dos tres formatos ja publicados pela fonte (ISO-8601 com e sem sufixo Z, e dd/mm/aaaa como fallback). Um valor presente no arquivo mas ilegivel vira nulo e e registrado como ajuste `data_ilegivel`, para nao se confundir com um campo vazio na origem. |
| 2 | `normaliza_codigos_categoricos` | Converte os campos categoricos do SIVEP-Gripe para inteiro nulavel, contabiliza os codigos de ausencia (padrao [9], vazio em ['CLASSI_FIN', 'CRITERIO'], que nao possuem o codigo 9 no dicionario) e conta os codigos fora do dominio declarado. Nada e anulado nem convertido: o codigo e preservado como esta, e quem o exclui e a camada de metricas, ao montar numerador e denominador. Um valor presente mas nao numerico -- sem correspondencia possivel no dicionario -- vira nulo e e registrado como ajuste `codigo_ilegivel`, para nao se confundir com um campo vazio na origem. |
| 3 | `normaliza_sexo` | Normaliza CS_SEXO para caixa alta sem espacos (dominio M/F/I). String vazia e tratada como ausencia, nao como valor. |
| 4 | `normaliza_numericos` | Converte a idade bruta (NU_IDADE_N) para inteiro nulavel. Valores nao numericos viram nulo em vez de interromper a carga. |
| 5 | `valida_uf` | Normaliza as siglas de UF e anula as que estao fora das 27 unidades federativas. A alteracao e registrada por registro como `uf_anulada:<coluna>`, de modo que um valor anulado pela limpeza nao se confunda com um campo vazio na origem. |
| 6 | `deriva_idade` | Converte NU_IDADE_N para anos conforme TP_IDADE (1-dia, 2-mes, 3-ano), anula a idade quando o numero esta fora do dominio da propria unidade (1-dia admite 0 a 30; 2-mes admite 1 a 11) registrando o ajuste `idade_unidade_implausivel` -- sem reinterpretar a unidade, que seria inventar dado -- e anula valores fora de [0, 120] anos, registrando o ajuste `idade_anulada`. O dicionario oficial valida ate 150 anos; adotamos um teto biologicamente plausivel, e a divergencia e declarada. Em seguida agrega a idade em faixa etaria e descarta a idade exata e os campos brutos: a camada analitica trabalha so com a faixa, por minimizacao de dados -- nenhuma metrica consome idade exata, e persisti-la sem consumidor formaria um quase-identificador com UF, sexo e datas. |
| 7 | `marca_ano_de_origem` | Grava o ano do arquivo de origem em `ano_referencia`, permitindo rastrear de qual safra do DATASUS cada registro veio. |
| 8 | `deriva_semana_epidemiologica` | Deriva de DT_SIN_PRI a semana epidemiologica pela regra do Ministerio da Saude -- semana iniciada no domingo, SE 1 sendo a primeira com ao menos 4 dias no ano novo -- e publica `semana_epi_ano`, `semana_epi_num`, o rotulo ordenavel `semana_epi`, o ano civil dos sintomas e o mes `AAAA-MM`. A semana ISO nao serve: na virada do ano ela atribui outro numero, e as vezes outro ano, ao mesmo dia. SEM_PRI, publicada pela fonte, e lida apenas para reconciliacao: a divergencia e contada no relatorio de qualidade e nenhuma das duas colunas corrige ou preenche a outra -- misturar dois criterios na mesma serie produziria um agregado que nao corresponde a nenhum deles. |
| 9 | `avalia_coerencia` | Confere a coerencia entre campos do mesmo registro (datas fora de ordem, datas fora do intervalo fisicamente possivel, campos obrigatorios ausentes dada a resposta declarada) e marca uma flag por dimensao: eixo temporal, internacao, UTI, evolucao e plausibilidade das datas. Casos nosocomiais nao sao marcados por internacao anterior aos sintomas, porque no dicionario isso e o esperado. Nenhum registro e removido -- cada metrica decide quais flags a afetam, e apenas o eixo temporal exclui o registro da camada analitica. |
| 10 | `deriva_semantica` | Traduz os codigos do dicionario oficial em conceitos usados pelas metricas (obito por SRAG, caso encerrado, hospitalizacao, admissao em UTI, suporte ventilatorio, infeccao nosocomial, grupo etiologico e vacinacao declarada). Uma unica definicao por conceito, compartilhada por indicadores, series e graficos, de modo que nao existam duas nocoes de 'caso encerrado' no projeto. |

### Semantica derivada em Python

A traducao dos codigos do dicionario em conceitos epidemiologicos acontece na camada de tratamento, ao lado de `CODE_LABELS`, e nao no SQL da view analitica. Manter a traducao em outra linguagem e outro arquivo permitiria que uma mudanca no dicionario nao alcancasse o calculo sem que nada falhasse. A view faz apenas projecao de tipo.

Colunas derivadas: `eh_obito_srag`, `caso_encerrado`, `foi_hospitalizado`, `hospitalizacao_informada`, `teve_admissao_uti`, `uti_informado`, `estadia_uti_utilizavel`, `foi_ventilado`, `ventilacao_invasiva`, `ventilacao_nao_invasiva`, `ventilacao_informada`, `caso_nosocomial`, `etiologia_laboratorial`, `etiologia_criterio_informado`, `grupo_etiologico`, `status_caso`, `vacinado_covid`, `vacina_covid_informada`, `vacinado_influenza`, `vacina_influenza_informada`.

## Registro no relatorio de qualidade

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
| `flag_data_implausivel` | nao | alguma coluna de data traz um valor fora do intervalo fisicamente possivel -- anterior ao inicio da serie SIVEP-Gripe ou posterior a data de execucao da carga. Captura erros de digitacao de ano (1695, 2202, 5202, 8202 foram medidos na fonte) que nenhuma comparacao entre datas detecta, porque a ordem relativa continua correta. Nao exclui o registro: marca e conta. |

## Rastreamento dos ajustes

Flags de coerencia descrevem o que o dado tem de errado. Esta secao trata do que o pipeline **fez** com ele -- sao coisas distintas: um registro pode estar incoerente sem ter sido tocado, e pode ter sido alterado sem estar incoerente.

Toda alteracao de valor e gravada no proprio registro, na coluna `ajustes_aplicados` (codigos separados por virgula; vazia quando o registro chegou intacto). Isso torna cada alteracao localizavel:

```sql
SELECT ano_referencia, ajustes_aplicados, count(*)
FROM srag_cases WHERE ajustes_aplicados <> '' GROUP BY 1, 2;
```

Sem isso, um campo anulado pelo pipeline seria indistinguivel de um que ja veio vazio da fonte -- e a alteracao seria, na pratica, silenciosa.

| Codigo | Significado |
|--------|-------------|
| `uf_anulada` | sigla de UF fora das 27 unidades federativas; o valor original era inutilizavel e foi substituido por nulo |
| `idade_anulada` | idade normalizada fora do intervalo plausivel [0, 120] anos; substituida por nulo |
| `data_ilegivel` | valor de data presente no arquivo bruto mas nao interpretavel em nenhum dos formatos publicados pela fonte; substituido por nulo |
| `codigo_ilegivel` | valor categorico presente no arquivo bruto mas nao numerico (portanto sem correspondencia possivel no dicionario); substituido por nulo. Registrado pelo mesmo motivo que `data_ilegivel`: sem isso um campo preenchido com lixo ficaria indistinguivel de um campo vazio na origem |
| `idade_unidade_implausivel` | NU_IDADE_N fora do dominio declarado para a unidade em TP_IDADE (1-dia admite 0 a 30; 2-mes admite 1 a 11); a idade derivada foi anulada. A unidade NAO e reinterpretada: nao ha como saber se o erro esta no numero ou na unidade, e escolher um dos dois seria inventar dado. Foram medidos 3 registros na safra de referencia |

### Rastreabilidade da carga

| Artefato | Conteudo |
|----------|----------|
| `data/raw/manifest.json` | proveniencia do arquivo-fonte: origem, URL ou caminho, tamanho, sha256 e data |
| `data/processed/quality_report.json` | `run_id` da carga, contagens por regra, flags, ajustes e proveniencia |
| `data/processed/ingestion_history.jsonl` | uma linha por carga, para comparar versoes da base e detectar degradacao na fonte |
| coluna `ajustes_aplicados` | alteracoes aplicadas a cada registro |
| tabela `audit_events` | eventos da ingestao e das execucoes do agente, consultaveis por `run_id` |

## Janela de analise

Toda janela temporal e ancorada na **maior data de digitacao da base** (`max(DT_DIGITA)`), nunca em `today()`: a base publicada tem defasagem em relacao ao dia corrente. Da data de referencia sao descontados `REPORTING_LAG_DAYS` dias, para nao confundir atraso de notificacao com queda real de casos. A tool `get_notification_completeness` mede os percentis do atraso observado e sinaliza quando o corte configurado e menor que o percentil 75.
