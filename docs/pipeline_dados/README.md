# Camada de dados SRAG — diagnóstico, regras e operação

Documentação da revisão do pipeline de ingestão, seleção, limpeza, transformação, validação e
preparação analítica dos dados de SRAG do Open DATASUS.

- Decisões, com evidência e alternativas: [`decisoes.md`](decisoes.md)
- Contrato de colunas e regras de limpeza (gerado do código): [`../regras_transformacao.md`](../regras_transformacao.md)
- Contrato métrica ↔ campo ↔ regra ↔ limitação (gerado do código): [`../dicionario_metricas.md`](../dicionario_metricas.md)

Todos os números deste documento foram **medidos** sobre
`data_sus/INFLUD25_DATASUS-Versao26-06-2025.csv` (165.397 registros, 194 colunas) e, quando
indicado, sobre `data/raw/INFLUD25-14-09-2026.csv` (336.391 registros, mesmo ano, safra posterior).

---

## 1. Diagnóstico do estado anterior

### Fluxo existente

```
data/raw/*.csv ──► download.py (manifesto + sha256)
                        │
                        ▼
                  preprocess.py ──► cleaning/*.py (regras nomeadas, ordem declarada)
                        │                 │
                        │                 └──► quality.py (agregado + log por registro)
                        ▼
              data/processed/srag_cases.parquet
                        │
                        ▼
              load_database.py ──► DuckDB: srag_cases + view srag_analytics
                        │
                        ▼
                   metrics/*.py ──► indicadores
```

### O que já estava certo e foi preservado

O pipeline **não** foi reescrito. A arquitetura estava bem desenhada e vários acertos são
estruturais:

- **Regras de limpeza como objetos nomeados** (`CleaningRule`), com a ordem declarada em
  `CLEANING_PIPELINE`. O tratamento pode ser lido como uma lista de regras, e cada uma é testável
  isoladamente.
- **Minimização de dados na fronteira de leitura**: allowlist de colunas, denylist explícita de PII,
  e as ~160 colunas restantes nunca entram em memória.
- **Rastreabilidade por registro**: a coluna `ajustes_aplicados` diz o que o pipeline fez com cada
  linha. Sem ela, um campo anulado pela limpeza seria indistinguível de um que já veio vazio.
- **Flags de coerência por dimensão**, não um booleano único: uma data de internação impossível não
  derruba o registro da contagem de casos.
- **Código 9 (Ignorado) preservado no dado e excluído apenas no cálculo** — separação correta entre
  camada de dado e camada de métrica.
- **Recusa honesta de indicadores não calculáveis** (`icu_bed_occupancy_rate`), com motivo
  publicado, em vez de renomear uma aproximação.
- **Denominador zero devolve `None` com motivo**, nunca zero.
- **Semântica derivada em Python, ao lado do dicionário de códigos**, e não em SQL — de modo que
  uma mudança no dicionário não possa deixar de chegar ao cálculo.
- **Definições como dado**, gerando a documentação publicada, com CI travando divergência.

### Problemas encontrados

| # | Problema | Gravidade | Evidência medida |
|---|---|---|---|
| 1 | Nenhuma detecção de mudança de esquema | CRITICAL | inexistente; só um `logger.warning` para coluna ausente |
| 2 | Teto de permanência em UTI furado pelo ramo `DT_EVOLUCA` | CRITICAL | estadia de 2025-01-10 terminava em 2026-08-02 em vez de 2025-01-15 |
| 3 | `reference_date` sem teto: uma digitação futura desloca todas as janelas | CRITICAL | `max(DT_DIGITA)` sem validação |
| 4 | `HOSPITAL` ausente lido como "não internado" | HIGH | 2,60% vazio + 0,18% código 9, saindo do denominador mesmo com `UTI=1` |
| 5 | Maturidade assimétrica entre as janelas do crescimento | HIGH | janela atual com 21 dias de digitação contra 51 da anterior |
| 6 | `_years_present` ignorava o recorte analítico | HIGH | ano sem casos na UF entrava na mediana com valor 0 |
| 7 | Ventilação inexistente no pipeline | HIGH | `SUPORT_VEN` fora do schema, apesar de exigida pelo escopo |
| 8 | Incidência com numerador e denominador geográficos incompatíveis | MEDIUM | 2.807 registros (1,70%) com residência ≠ notificação |
| 9 | `flag_internacao_inconsistente` com 99,85% de falso positivo | MEDIUM | 1.363 dos 1.365 marcados são casos nosocomiais legítimos |
| 10 | Datas absurdas não detectadas | MEDIUM | `DT_INTERNA` de **1695** a **2202**; anos **5202** e **8202** em `data/raw/` |
| 11 | Encoding fixado em `latin-1` sobre arquivos UTF-8 | MEDIUM | defeito latente: `latin-1` nunca falha, só corrompe |
| 12 | Denylist auditável incompleta | LOW | 16 colunas de dose/lote/fabricante omitidas |
| 13 | Semana epidemiológica inexistente | LOW | `SEM_PRI` fora do schema, sem derivação |

### Riscos que permanecem

Estão listados em [§8](#8-inconsistências-que-permanecem).

---

## 2. Dicionário analítico

### Colunas lidas do arquivo bruto — 22 de 194

| Variável | Significado (dicionário oficial) | Tipo | Uso | Missing | Tratamento |
|---|---|---|---|---:|---|
| `DT_SIN_PRI` | Data dos primeiros sintomas (campo 2) | data | eixo temporal de **toda** série e janela | 0,00% | parse explícito; ausência ou fora de ordem ⇒ `flag_data_invalida` (única que exclui da view) |
| `DT_INTERNA` | Data da internação (49) | data | coerência de internação | 6,43% | parse; teto absoluto de plausibilidade |
| `DT_ENTUTI` | Data de entrada na UTI (54) | data | início da permanência no censo | 74,87% | parse; coerência de UTI |
| `DT_SAIDUTI` | Data de saída da UTI (55) | data | fim da permanência no censo | 87,71% | parse; ausência ⇒ imputação limitada por teto |
| `DT_EVOLUCA` | Data da alta ou do óbito (81) | data | fallback de fim de permanência | 30,68% | parse; **não** é limitada por `DT_DIGITA` (ver D-05) |
| `DT_ENCERRA` | Data de encerramento do caso (82) | data | maturidade do desfecho | 23,01% | parse; usada como derivada, não persistida como âncora |
| `DT_DIGITA` | Data de digitação | data | âncora da janela analítica | 0,00% | parse; digitação futura ignorada na data de referência |
| `SEM_PRI` | Semana epidemiológica dos sintomas | texto | **reconciliação** da semana derivada | 0,00% | comparada, nunca coalescida |
| `CS_SEXO` | Sexo (11) | texto | perfil | 0,00% | domínio real M/F/I — o dicionário diz 1/2/9 e está desatualizado |
| `TP_IDADE` | Unidade da idade (14) | código | unidade de `NU_IDADE_N` | 0,00% | 1=dia, 2=mês, 3=ano; domínio validado |
| `NU_IDADE_N` | Idade na unidade de `TP_IDADE` (13) | inteiro | insumo da faixa etária | 0,00% | **descartado** após derivar a faixa |
| `HOSPITAL` | Houve internação (48) | código | severidade, denominador de UTI | 2,60% + 9: 0,18% | ausência **nunca** lida como "Não" |
| `UTI` | Internado em UTI (53) | código | admissão em UTI | 11,49% + 9: 1,12% | 9 e nulo fora de numerador e denominador |
| `SUPORT_VEN` | Suporte ventilatório (56) | código | **ventilação** | 12,68% + 9: 2,05% | **1=Sim invasivo, 2=Sim NÃO invasivo, 3=Não, 9=Ignorado** |
| `NOSOCOMIAL` | Infecção adquirida no hospital (32) | código | condiciona a coerência de internação | 10,23% | 4.008 casos legitimam sintomas após internação |
| `CLASSI_FIN` | Classificação final (78) | código | etiologia | 13,75% | 1..5, **sem código 9**; vazio = não encerrado |
| `CRITERIO` | Critério de encerramento (79) | código | confiança da etiologia | 17,48% | 1..4, **sem código 9** |
| `EVOLUCAO` | Evolução do caso (80) | código | mortalidade, status | 22,65% + 9: 2,26% | 9 e nulo fora do denominador; ausência ≠ sobrevivente |
| `VACINA_COV` | Vacina COVID-19 (36) | código | cobertura entre casos | 0,01% + 9: 0,81% | 9 e nulo fora do denominador |
| `VACINA` | Vacina influenza (40) | código | cobertura entre casos | 7,01% | idem |
| `SG_UF_NOT` | UF de notificação (3) | texto | **carga assistencial** | 0,00% | validada contra as 27 UFs |
| `SG_UF` | UF de residência (23) | texto | **incidência** (casa com IBGE) | 0,03% | validada; sem fallback para a de notificação |

### Colunas derivadas — 33

**Temporais:** `semana_epi`, `semana_epi_ano`, `semana_epi_num`, `ano_sintomas`, `mes_sintomas`,
`ano_referencia`.
Semana epidemiológica derivada de `DT_SIN_PRI` pela regra do MS (**semana começa no domingo**, não
ISO). `SEM_PRI` serve só para reconciliação — divergências são contadas, nunca corrigidas.

**Perfil:** `faixa_etaria`. A idade exata é descartada por minimização: nenhuma métrica a consome, e
persisti-la formaria um quase-identificador com UF, sexo e datas.

**Severidade e desfecho** (todas com booleano estrito — nulo e código 9 nunca produzem `True`):

| Coluna | Regra | Contagem medida |
|---|---|---:|
| `eh_obito_srag` | `EVOLUCAO == 2` | 9.885 |
| `caso_encerrado` | `EVOLUCAO ∈ {1,2,3}` | 124.188 |
| `foi_hospitalizado` | `HOSPITAL == 1` | 157.363 |
| `hospitalizacao_informada` | `HOSPITAL ∈ {1,2}` | 160.813 |
| `teve_admissao_uti` | `UTI == 1` | 42.519 |
| `uti_informado` | `UTI ∈ {1,2}` | 144.540 |
| `foi_ventilado` | `SUPORT_VEN ∈ {1,2}` | 92.581 |
| `ventilacao_invasiva` | `SUPORT_VEN == 1` | 17.461 |
| `ventilacao_nao_invasiva` | `SUPORT_VEN == 2` | 75.120 |
| `ventilacao_informada` | `SUPORT_VEN ∈ {1,2,3}` | 141.045 |
| `caso_nosocomial` | `NOSOCOMIAL == 1` | 4.008 |
| `estadia_uti_utilizavel` | admissão + data de entrada + sem incoerência de UTI | — |

**Etiologia:** `grupo_etiologico` (vazio → `nao_encerrado`, **nunca** `nao_especificado`),
`etiologia_laboratorial`, `etiologia_criterio_informado`.
**Status:** `status_caso` — `obito_srag` / `obito_outras` / `cura` / `desfecho_ignorado` /
`em_aberto` / `fora_do_dominio`. `em_aberto` e `desfecho_ignorado` nunca se somam a `cura`.

**Flags de coerência** (nenhuma remove registro; só `flag_data_invalida` exclui da view):
`flag_data_invalida`, `flag_internacao_inconsistente`, `flag_uti_inconsistente`,
`flag_evolucao_inconsistente`, `flag_data_implausivel`.

**Rastreabilidade:** `ajustes_aplicados` — códigos `uf_anulada`, `idade_anulada`, `data_ilegivel`,
`codigo_ilegivel`, `idade_unidade_implausivel`.

### Colunas descartadas

- **Por ausência de utilidade analítica:** `DT_NOTIFIC` e `SEM_NOT` (o eixo é `DT_SIN_PRI` e o corte
  é `DT_DIGITA`); `FATOR_RISC` — **inutilizável**: no arquivo só existem vazio (96.949) e `1`
  (68.448), nunca `2` nem `9`, de modo que ausência não distingue "sem fator de risco" de "não
  informado".
- **Por minimização de dados pessoais (49 colunas):** identificador da notificação, data de
  nascimento, município e regional (notificação, residência e internação), unidade de saúde,
  ocupação, raça/cor, etnia indígena, gestação, IMC, textos livres, e todas as datas, lotes e
  fabricantes de dose vacinal.

---

## 3. Regras epidemiológicas

### Princípio

> Ausência de informação nunca é convertida em negativa. `missing ≠ Não`, `ignorado ≠ Não`,
> `EVOLUCAO` ausente ≠ sobrevivente, `UTI` ausente ≠ sem UTI, `SUPORT_VEN` ausente ≠ não ventilado.

Em toda proporção, a ausência sai **simultaneamente** do numerador e do denominador, e o volume de
ausentes é publicado ao lado do valor.

### Numeradores e denominadores

| Indicador | Numerador | Denominador | Exclusões | Tratamento de pendentes |
|---|---|---|---|---|
| Taxa de aumento de casos | casos na janela atual − casos na janela anterior | casos na janela anterior **digitados até o fechamento dela + `REPORTING_LAG_DAYS`** | `flag_data_invalida` | maturidade simétrica; contagens sem censura publicadas ao lado |
| Mortalidade | `EVOLUCAO = 2` | `EVOLUCAO ∈ {1,2,3}` | código 9 e nulo | casos em aberto publicados como `casos_em_aberto`, fora do denominador |
| Admissão em UTI | internados com `UTI = 1` | internados com `UTI ∈ {1,2}` | `UTI` 9 e nulo | internado = `HOSPITAL = 1` **ou** `UTI = 1` |
| Ventilação | `SUPORT_VEN ∈ {1,2}` | `SUPORT_VEN ∈ {1,2,3}` | código 9 e nulo | o código 3 ("Não") **entra** no denominador: é informação |
| Censo de UTI | estadias que intersectam cada dia | contagem, não proporção | `estadia_uti_utilizavel = falso` | fim imputado, limitado por teto empírico em **todos** os ramos |
| Cobertura vacinal entre casos | `VACINA_COV = 1` | `VACINA_COV ∈ {1,2}` | código 9 e nulo | — |
| Incidência | casos na janela, por **UF de residência** | população residente do IBGE | casos sem UF de residência | dimensão geográfica publicada no resultado |
| Excesso sazonal | casos na janela − mediana do baseline | mediana do baseline | 2020 e 2021 por definição; anos ausentes **no recorte** | anos presentes, ausentes e excluídos, todos publicados |

### Critérios de exclusão, rastreáveis

Nenhum registro é excluído na ingestão. A exclusão ocorre na consulta, e é contabilizada:

| Regra | Motivo | Registros | % da base |
|---|---|---:|---:|
| `flag_data_invalida` | sem eixo temporal utilizável, nenhuma métrica pode situar o caso | 25 | 0,015% |
| `EVOLUCAO` 9 ou nulo | ausência de desfecho não é sobrevivência | 41.209 | 24,92% |
| `UTI` 9 ou nulo | ausência não é "sem UTI" | 20.857 | 12,61% |
| `SUPORT_VEN` 9 ou nulo | ausência não é "não ventilado" | 24.352 | 14,72% |
| `CLASSI_FIN` vazio, sob filtro de classificação | caso não encerrado | 22.748 | 13,75% |

---

## 4. Estratégia de tratamento

| Variável | Problema | Regra aplicada | Justificativa | Impacto medido |
|---|---|---|---|---|
| todas as datas | 3 formatos publicados pela fonte | parse ISO com fallback `dd/mm/aaaa`; valor ilegível vira nulo **e** ajuste | distinguir "vazio na origem" de "presente e ilegível" | 0 ilegíveis nesta safra |
| todas as datas | anos absurdos (1695, 2202, 5202, 8202) | `flag_data_implausivel` com piso `MIN_VALID_DATE` e teto na data de execução | nada detectava; não usa `DT_DIGITA` como teto (ver D-05) | 9 registros |
| `DT_INTERNA` | sintomas após internação | flag condicionada a `~caso_nosocomial` | 1.363 dos 1.365 marcados eram nosocomiais legítimos | 3.968 → 2.605 |
| `NU_IDADE_N` | unidade na coluna vizinha | conversão por `TP_IDADE`; fora do domínio ⇒ anula, não reinterpreta | ignorar a unidade mudaria a faixa de 21.123 registros (12,77%) | 5 anulados |
| `NU_IDADE_N` | idade implausível | anula fora de [0, 120] anos | o dicionário valida até 150 — validação de formulário, não fato biológico | 1 anulado |
| `SG_UF_NOT`, `SG_UF` | sigla fora das 27 UFs | anula e registra `uf_anulada` | valor inutilizável ≠ campo vazio | 0 nesta safra |
| categóricas | valor presente não numérico | vira nulo **e** registra `codigo_ilegivel` | simetria com o tratamento de datas | 0 nesta safra |
| categóricas | código fora do domínio | contado, **não** anulado | a camada de métricas só reconhece códigos válidos | 0 nesta safra |
| duplicidade | linhas repetidas | contadas, **nunca** removidas | `NU_NOTIFIC` íntegro (0 repetições) prova que são pacientes distintos | 752 (0,455%) |

---

## 5. Relatório de qualidade — execução real

Execução completa sobre a base oficial, em `DATA_ROOT` isolado:

```
Arquivo de origem:  INFLUD25_DATASUS-Versao26-06-2025.csv
Encoding detectado: utf-8

RAW          165.397 linhas × 194 colunas
PROCESSED    165.397 linhas ×  53 colunas

Registros descartados:  0
Registros ajustados:    6   (idade_unidade_implausivel 5, idade_anulada 1)

FLAGS DE COERÊNCIA
  flag_data_invalida              25   (0,015%)  exclui da view analítica
  flag_internacao_inconsistente 2.605   (1,575%)
  flag_uti_inconsistente        1.500   (0,907%)
  flag_evolucao_inconsistente   9.533   (5,764%)
  flag_data_implausivel             9   (0,005%)

QUALIDADE
  Datas não parseáveis:      0 em todas as 7 colunas
  UF fora do domínio:        0
  Idade fora do plausível:   1
  Linhas idênticas:        752 (0,455%) — contadas, não removidas
  Divergência SEM_PRI:       0 de 165.397

SCHEMA
  linha de base estabelecida, sem comparação possível (primeira carga)
```

A redução de 194 para 53 colunas é **minimização deliberada**, não perda: 22 colunas são lidas,
2 (`NU_IDADE_N`, `TP_IDADE`) são descartadas após derivar a faixa etária, e 33 são derivadas.

### Validação antes × depois

Nenhuma transformação altera a distribuição: **0 registros descartados** e **6 ajustados**
(0,004%). As contagens de hospitalização, UTI, ventilação, óbito e etiologia das colunas derivadas
reproduzem exatamente as contagens dos códigos brutos — conferidas na tabela de derivadas em §2.

---

## 6. Schema drift — reação a uma atualização real

Teste feito com dado real: depois da carga acima, o **mesmo ano** foi recarregado com a safra
posterior (`INFLUD25-14-09-2026.csv`, 336.391 linhas).

```
0 erro(s) e 6 aviso(s) de mudança de esquema

[WARNING] record_count_changes  a safra de 2025 cresceu +103,4% (165.397 → 336.391)
[WARNING] missing_rate_changes  EVOLUCAO    ausência  22,65% → 4,57%  (−18,08 pp)
[WARNING] missing_rate_changes  DT_ENCERRA  ausência  23,01% → 4,76%  (−18,25 pp)
[WARNING] missing_rate_changes  DT_EVOLUCA  ausência  30,68% → 13,65% (−17,03 pp)
[WARNING] missing_rate_changes  CRITERIO    ausência  17,48% → 4,95%  (−12,53 pp)
[WARNING] missing_rate_changes  CLASSI_FIN  ausência  13,75% → 2,59%  (−11,16 pp)
```

Os avisos não são ruído: eles mostram a **maturação do encerramento** dos casos entre as duas
safras — exatamente o fenômeno que produz os vieses de letalidade e de crescimento tratados em §3.
A carga seguiu (nenhum ERROR), e a linha de base avançou.

Comportamento por tipo de mudança:

| Mudança | Severidade | Efeito |
|---|---|---|
| Coluna de `ALLOWED_COLUMNS` desaparece | **ERROR** | carga interrompida; nenhum Parquet novo é gravado |
| Tipo de coluna lida muda | **ERROR** | carga interrompida |
| Queda de registros acima de 20% | **ERROR** | carga interrompida |
| Coluna nova no arquivo bruto | WARNING | registrado; a allowlist protege o cálculo |
| Coluna não lida desaparece | WARNING | registrado |
| Categoria nova | WARNING | registrado |
| Ausência varia mais de 5 pp | WARNING | registrado |
| Crescimento acima de 50% | WARNING | registrado |

Os achados são persistidos em `data/processed/schema_drift.json` **sempre** — inclusive quando a
carga é interrompida, que é justamente quando mais importam, porque uma carga abortada não gera
relatório de qualidade. A linha de base só avança quando a carga vai até o fim: uma carga
interrompida a deixa intacta, para que a próxima tentativa detecte a mesma mudança em vez de
aceitá-la por inércia.

---

## 7. Como operar

### Instalar

```bash
pip install -r requirements.txt
```

### Onde colocar uma atualização da base

Duas opções, ambas registram proveniência com sha256 no manifesto:

```bash
python -m src.data.download --years 2026
```

```bash
python -m src.data.download --local caminho/para/INFLUD26-XX-XX-XXXX.csv --year 2026
```

O arquivo local **não é copiado**: o manifesto guarda o caminho original. A base bruta é somente
leitura e nunca é sobrescrita pelo pipeline.

### Executar

```bash
python -m src.data.preprocess --years 2025 2026
```

```bash
python -m src.data.load_database
```

### Onde está a saída

| Artefato | Caminho |
|---|---|
| Camada processada | `data/processed/srag_cases.parquet` |
| Relatório de qualidade da carga | `data/processed/quality_report.json` |
| Histórico de todas as cargas | `data/processed/ingestion_history.jsonl` |
| Mudanças de esquema da carga | `data/processed/schema_drift.json` |
| Linha de base do esquema (versionada) | `data/processed/schema_baseline.json` |
| Banco analítico | `data/analytics/srag.duckdb` |

### Como interpretar os avisos

- **WARNING** — a fonte mudou, mas o cálculo continua válido. Leia `schema_drift.json`, confirme que
  a mudança é esperada e siga. Uma variação grande de ausência costuma ser maturação de
  encerramento, como demonstrado em §6.
- **ERROR** — a carga foi **interrompida** e nada foi gravado. Uma coluna de que um indicador depende
  sumiu, um tipo mudou, ou o arquivo perdeu registros. Investigue o arquivo antes de insistir.
- Para aceitar uma mudança conhecida e deliberada:

```bash
python -m src.data.preprocess --years 2026 --accept-drift
```

O aceite é gravado na própria linha de base, com data e a lista de achados aceitos — fica auditável.

### Rodar os testes

```bash
python -m pytest -q
```

### Localizar o que o pipeline alterou

```sql
SELECT ajustes_aplicados, count(*) FROM srag_cases
WHERE ajustes_aplicados <> '' GROUP BY 1;
```

---

## 8. Inconsistências que permanecem

Declaradas, não resolvidas:

1. **Divergência entre idade declarada e idade real.** O Agent 3 mediu 56 registros (0,034%) cuja
   idade normalizada difere da idade calculável por `DT_NASC` em mais de um ano. Detectá-los exigiria
   ler `DT_NASC`, que é quase-identificador direto e está na denylist. A validação de domínio de
   `TP_IDADE` captura a classe de erro sem PII, mas não esses casos. Ver D-06.
2. **Duplicidade indecidível.** 752 registros (0,455%) são idênticos nas colunas persistidas. Sem
   `NU_NOTIFIC` — negado por minimização — não há como distinguir duplicata de pacientes distintos
   com os mesmos atributos agregados. Contados, nunca removidos. Ver D-15.
3. **Taxa de ocupação de leitos de UTI é incalculável** com o SIVEP-Gripe, que não registra
   capacidade instalada. Declarada não calculável, com motivo, em vez de aproximada.
4. **12 colunas do arquivo não constam do dicionário** de 19/09/2022 (`SURTO_SG`, `CO_DETEC`,
   `VG_OMS`, `VG_LIN`, `REINF`, `TABAG`, entre outras). Nenhuma é lida, mas o significado oficial
   delas não pôde ser verificado.
5. **`CS_SEXO` diverge do dicionário.** O dicionário declara 1/2/9; o arquivo traz M/F/I. A
   implementação segue o arquivo, que é o dado real; a divergência fica registrada.
6. **A data de referência depende do dia de execução.** O teto contra digitação futura usa a data
   corrente. Uma reexecução em outro dia pode, em tese, admitir um registro antes rejeitado. O
   parâmetro é explícito na função para permitir fixá-lo.
