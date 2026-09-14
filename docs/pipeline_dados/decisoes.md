# Log de decisões — revisão do pipeline de dados SRAG

Registro das decisões tomadas pelo Orchestrator na revisão da camada de ingestão,
seleção, limpeza, transformação, validação e preparação analítica dos dados de SRAG.

Cada decisão segue o formato: **Evidência → Alternativa considerada → Decisão → Impacto**.
As evidências numéricas foram **medidas** sobre `data_sus/INFLUD25_DATASUS-Versao26-06-2025.csv`
(165.397 registros, 194 colunas) e sobre os arquivos de `data/raw/`, não estimadas.

Divergências entre os especialistas foram resolvidas aqui, e a resolução está registrada.

---

## D-01 — Encoding detectado por arquivo, não fixado em constante

**Evidência.** `src/config.py` fixava `RAW_CSV_ENCODING = "latin-1"`. Medição do Orchestrator:
`data_sus/INFLUD25`, `data/raw/INFLUD19` e `data/raw/INFLUD25` decodificam **integralmente como
UTF-8**. `latin-1` decodifica qualquer byte sem erro — é exatamente por isso que o defeito era
silencioso: um arquivo UTF-8 lido como `latin-1` produz mojibake sem nada falhar.

**Alternativa considerada.** Trocar a constante para `"utf-8"`. Rejeitada: apenas troca um palpite
por outro. As safras antigas do SIVEP são exportadas de DBF e podem legitimamente ser `latin-1`;
o pipeline precisa sobreviver às duas.

**Decisão.** `src/data/encoding.py`: tenta decodificar o arquivo **inteiro** como UTF-8 de forma
incremental e só recorre a `latin-1` se houver byte inválido. Sem amostragem e sem heurística
estatística. O encoding escolhido é registrado na proveniência da carga.

**Impacto.** Nenhuma mudança de resultado hoje (toda a allowlist é ASCII). Elimina um defeito
latente que se manifestaria na primeira coluna de texto adicionada ao schema, e torna uma mudança
de encoding entre safras visível no histórico de ingestão em vez de virar caractere corrompido.

---

## D-02 — `SUPORT_VEN` não usa o mapa Sim/Não/Ignorado

**Evidência.** Lido diretamente no dicionário oficial (`Dicionario_de_Dados_SRAG_Hospitalizado.pdf`,
campo 56): **1-Sim invasivo, 2-Sim NÃO invasivo, 3-Não, 9-Ignorado**. Distribuição medida:
`2`=75.120, `3`=48.464, `1`=17.461, `9`=3.386, vazio=20.966.

**Alternativa considerada.** Reaproveitar `YES_NO_IGNORED` (1=Sim, 2=Não, 9=Ignorado), como as
demais variáveis binárias do SIVEP. **Rejeitada**: inverteria a leitura de 75.120 registros,
classificando como "não ventilado" o maior grupo de pacientes ventilados da base.

**Decisão.** Domínio próprio em `CODE_LABELS`, com comentário de aviso no código.
`foi_ventilado = isin([1, 2])`, `ventilacao_invasiva = isin([1])`,
`ventilacao_informada = isin([1, 2, 3])` — note que o `3` entra no denominador, porque "Não" é
informação, não ausência. Teste dedicado impede a regressão.

**Impacto.** Torna possível o indicador de ventilação exigido pelo escopo, sem introduzir o erro
que o próprio enunciado adverte ("o exemplo 1→Sim, 2→Não, 9→Ignorado não deve ser assumido como
regra real sem validação no dicionário").

---

## D-03 — Ventilação invasiva como feature separada

**Evidência.** Medido pelo Orchestrator: **3.473 pacientes com `UTI=2` (não internados em UTI)
receberam ventilação invasiva**. Ventilação não é subconjunto de UTI.

**Alternativa considerada.** Publicar apenas `foi_ventilado`. Rejeitada: fundiria cânula e VNI com
intubação, apagando o indicador mais sensível de severidade crítica disponível na base.

**Decisão.** Manter `foi_ventilado`, `ventilacao_invasiva` e `ventilacao_nao_invasiva` como colunas
distintas.

**Impacto.** Dois indicadores de severidade em vez de um, ambos com denominador explícito.

---

## D-04 — `NOSOCOMIAL` incluída para corrigir um falso positivo total

**Evidência.** `flag_internacao_inconsistente` marca `DT_INTERNA < DT_SIN_PRI`. Medido:
1.365 registros violam a regra, e **1.363 deles (99,85%) têm `NOSOCOMIAL=1`** — infecção adquirida
no hospital, em que os sintomas começam *depois* da internação por definição. A regra atual não
detecta incoerência: ela reclassifica como defeito um fenômeno clínico que o dicionário prevê.

**Alternativa considerada.** Manter a regra e documentar a limitação. Rejeitada: a flag existe para
apontar dado ruim; 99,85% de falso positivo a torna ruído.

**Decisão.** Ler `NOSOCOMIAL` (custo de privacidade nulo: categórica clínica de 3 níveis) e
condicionar a regra: `(DT_INTERNA < DT_SIN_PRI) & ~caso_nosocomial`.

**Impacto.** A flag cai de 1.365 para 2 registros — os 2 com `NOSOCOMIAL=2`, que são incoerências
genuínas e continuam marcados.

---

## D-05 — DIVERGÊNCIA RESOLVIDA: regras de coerência temporal propostas vs. medidas

**Divergência.** O Agent 2 (análise conceitual) recomendou acrescentar as regras
`DT_ENTUTI >= DT_INTERNA`, `DT_SAIDUTI <= DT_EVOLUCA` e "rejeitar toda data posterior a
`DT_DIGITA`". O Agent 3 (medição empírica) mediu, sobre os mesmos dados:

| Regra proposta | Violações medidas |
|---|---|
| `DT_ENTUTI < DT_INTERNA` | **0** (n=40.515 pares) |
| `DT_SAIDUTI < DT_ENTUTI` | **0** (n=20.302) |
| `DT_EVOLUCA < DT_SIN_PRI` | **0** (n=114.656) |
| `DT_EVOLUCA > DT_DIGITA` | **60.632 — 52,88% dos pares** |

**Decisão do Orchestrator: a medição vence.**

1. As três primeiras regras **não são implementadas**: o sistema de origem já as impõe, e uma
   regra que nunca dispara é custo sem detecção.
2. `DT_DIGITA` **não é usada como teto superior** de datas de desfecho. A causa dos 52,88% não é
   dado corrompido: `DT_DIGITA` é a digitação **inicial** da ficha, não a última atualização.
   Implementar a regra marcaria 36,66% da base como inválida — um falso positivo de proporção
   catastrófica.
3. **Em lugar delas**, foi implementada a regra que o Agent 3 mostrou estar faltando: teto
   **absoluto** de plausibilidade por coluna de data (piso `MIN_VALID_DATE`, teto = data de
   execução). Medido: `DT_INTERNA` mínimo **1695-01-17** e máximo **2202-06-07**; `DT_ENTUTI`
   máximo 2028-05-07; nos arquivos de `data/raw/` há anos **5202** e **8202**. Nada no pipeline
   detectava isso.

**Impacto.** Captura a classe de erro real (datas absurdas) sem gerar falso positivo. A nova flag
conta e marca, mas não exclui o registro da view — só `flag_data_invalida` exclui.

---

## D-06 — DIVERGÊNCIA RESOLVIDA: validar idade contra `DT_NASC`

**Divergência.** O Agent 3 mediu **56 registros (0,034%)** cuja idade normalizada difere da idade
real em mais de um ano, e propôs derivar a idade de `DT_NASC` na ingestão para flagá-los.
`DT_NASC` está em `DENIED_COLUMNS` como quase-identificador direto.

**Decisão do Orchestrator: recusado.** Ler dado pessoal para validar 0,034% dos registros inverte
a relação custo-benefício e fura a fronteira de minimização, que é uma garantia estrutural do
projeto — não uma preferência.

**Em lugar disso**, foi implementada a validação de **domínio** de `TP_IDADE`, que vem do próprio
dicionário (1→dias 0-30, 2→meses 1-11, 3→anos), não precisa de PII e captura a mesma classe de
erro: um registro com `TP_IDADE=2` e `63` meses hoje vira 5,25 anos silenciosamente. Nesses casos
a idade é **anulada**, não reinterpretada — o pipeline não adivinha a unidade correta.

**Impacto.** 5 registros passam a ser detectados e marcados com o ajuste
`idade_unidade_implausivel` (ver D-17 para o piso adotado em meses). Os demais casos de divergência
permanecem indetectáveis sem PII, e isso está declarado como limitação residual.

---

## D-07 — `SG_UF` (residência) incluída; as duas UFs têm papéis distintos

**Evidência.** `incidence_rate` divide casos por **UF de notificação** pela população **residente**
do IBGE. São universos diferentes. Medido: **2.807 registros (1,70%)** têm residência e notificação
em UFs distintas, e o erro não é uniforme — concentra-se em UFs com fluxo assistencial líquido
(DF infla, GO deprime), com viés da ordem de 20-25% na incidência dessas UFs.

**Alternativa considerada.** Manter só `SG_UF_NOT` e renomear o indicador para "casos notificados
na UF por 100 mil residentes". Rejeitada: rotular corretamente um número enviesado não o conserta,
e a incidência é o único indicador populacional do sistema.

**Decisão.** Ler `SG_UF` (43 vazios, 0,026%; 27 UFs válidas, 0 fora de domínio) e separar os papéis:

- **incidência e qualquer indicador com denominador populacional externo** → `uf_residencia`;
- **carga assistencial** (UTI, ventilação, letalidade hospitalar, censo) → `uf_notificacao`,
  porque o leito é ocupado onde o paciente foi internado, não onde ele mora.

Proibido `coalesce(SG_UF, SG_UF_NOT)`: reintroduziria silenciosamente o mismatch que a inclusão
existe para eliminar. Os 43 registros sem UF de residência saem do denominador da incidência e
são reportados.

**Custo de privacidade e mitigação.** `SG_UF` isolada é agregado de centenas de milhares de pessoas
(nenhuma UF com menos de mil casos no arquivo). O risco está no **par** residência×notificação
cruzado com faixa etária e sexo. Mitigação obrigatória: as duas UFs são dimensões **alternativas**,
nunca cruzadas numa mesma tabela publicada, e o guardrail de célula mínima passa a valer para
recortes geográficos.

---

## D-08 — `CLASSI_FIN` vazio nunca vira "não especificado"

**Evidência.** Medido: `CLASSI_FIN` vazio em **22.748 registros (13,75%)**. O domínio oficial é
1..5 e **não possui código 9**: vazio significa caso ainda não encerrado.

**Decisão.** `grupo_etiologico` mapeia vazio para `"nao_encerrado"`, jamais para `4`
("SRAG não especificado"). São conceitos distintos: um é ausência de desfecho classificatório, o
outro é um desfecho. Colapsá-los inflaria `nao_especificado` em 37%.

**Consequência para o schema.** `MISSING_CODES = {9}` era global. `CLASSI_FIN` e `CRITERIO` não têm
código 9, então a constante global era inaplicável a elas. Passou a existir um mapa de códigos de
ausência **por coluna**.

---

## D-09 — Teto de permanência em UTI aplicado a todos os ramos imputados

**Evidência.** `_ICU_STAY_END_SQL` era
`least(coalesce(data_saida_uti, data_evolucao, entrada + teto), corte)`: o teto só protegia o
**terceiro** ramo. Demonstrado pelo Orchestrator com a expressão isolada — uma estadia iniciada em
2025-01-10, sem alta, com `DT_EVOLUCA` em 2026-08-20 e teto de 5 dias terminava em **2026-08-02**
(contando em toda a janela do censo) em vez de 2025-01-15.

**Decisão.** O teto passa a limitar também o ramo da evolução. A saída **registrada** continua
soberana e não é truncada: ela é o dado, não uma imputação.

**Impacto.** Fecha, por outro caminho, exatamente o defeito que o teto foi criado para corrigir.
A métrica de truncamento foi renomeada para `imputadas_truncadas_pelo_teto` e passou a cobrir os
dois ramos; o texto do relatório foi ajustado para não amarrá-la só ao ramo "em aberto".

---

## D-10 — Data de referência ignora digitação futura

**Evidência.** `reference_date` usava `max(data_digitacao)` sem teto, e a única validação envolvendo
`DT_DIGITA` era `DT_SIN_PRI > DT_DIGITA`. Um registro com digitação corrompida no futuro não é
flagado e passa a **ancorar todas as janelas do sistema**, que se deslocam para um período vazio.
O relatório sairia anunciando queda total de casos sem pista da causa.

**Decisão.** `reference_date(connection, today=None)` passa a ignorar digitações posteriores ao dia
de execução — uma ficha não pode ter sido digitada amanhã. O parâmetro `today` é explícito para
que os testes fixem o teto.

**Impacto.** Nenhuma mudança no resultado com os dados atuais; elimina um modo de falha que
derrubaria silenciosamente todos os indicadores de uma vez.

---

## D-11 — Anos de baseline respeitam o recorte analítico

**Evidência.** `_years_present` não recebia `filters.where_clause()`, ao contrário de
`count_between`. Verificado no código pelo Orchestrator.

**Decisão.** Passar o mesmo recorte.

**Impacto.** Com `uf="AC"`, um ano com casos no Brasil e nenhum no Acre era considerado *presente*
e entrava na mediana com valor 0, derrubando a mediana e inflando o excesso sazonal — ou tornando
o indicador não calculável por "mediana zero" quando o correto era declarar o ano ausente. O efeito
era maior justamente nas UFs pequenas, onde o alerta mais importa.

---

## D-12 — Denylist completada, ainda que inócua

**Evidência.** `DENIED_COLUMNS` omitia 16 colunas de data de dose, lote e fabricante presentes no
arquivo (`DOSE_2REF`, `DOSE_ADIC`, `DOS_RE_BI`, `LOTE_REF2`, `LOTE_ADIC`, `LOT_RE_BI`, `FAB_ADIC`,
`FAB_RE_BI`, `FAB_COV_1`, `FAB_COV_2`, `FAB_COVRF`, `FAB_COVRF2`, `DT_UT_DOSE`, `DT_DOSEUNI`,
`DT_1_DOSE`, `DT_2_DOSE`).

**Decisão.** Enumerá-las. Nenhuma era lida (a allowlist já as excluía por omissão), mas a denylist
existe precisamente para tornar a decisão **auditável** e para que o teste de regressão falhe se
alguém as adicionar. Uma lista auditável incompleta não cumpre sua função.

**Impacto.** Denylist passa de 33 para 49 colunas. Cobertura total de dose/lote/fabricante.

---

## D-13 — `FATOR_RISC` permanece fora, agora com justificativa medida

**Evidência.** Medido: no arquivo só existem **dois** valores — vazio (96.949) e `"1"` (68.448).
**Nunca `2` (Não) nem `9` (Ignorado)**, apesar de o dicionário declarar o domínio 1/2/9.

**Decisão.** Manter fora, substituindo a justificativa antiga ("nenhum indicador estratifica por
comorbidade") pela razão real: a coluna é **inutilizável como binário**, porque ausência não
distingue "sem fator de risco" de "não informado". Qualquer booleano derivado dela violaria a regra
"missing nunca vira Não".

---

## D-14 — Semana epidemiológica derivada, `SEM_PRI` usada só para reconciliação

**Evidência.** A semana epidemiológica do Ministério da Saúde **começa no domingo** (não é ISO, que
começa na segunda). `SEM_PRI` está 100% preenchida no arquivo, mas traz apenas 2 dígitos, sem ano.

**Decisão.** Derivar `semana_epi` de `DT_SIN_PRI` com a regra do MS. `SEM_PRI` entra no schema
apenas para **reconciliação**: divergências são contadas no relatório de qualidade. A derivada
manda no cálculo; nunca há coalesce de uma na outra.

**Impacto.** Custo de privacidade da inclusão é **negativo** — `SEM_PRI` é estritamente menos
granular que `DT_SIN_PRI`, que já era lida. Ganha-se uma verificação cruzada contra a própria fonte.

---

## D-15 — Duplicidade: medir certo, não deduplicar

**Evidência.** Medido: **0** linhas integralmente duplicadas nas 194 colunas e **0** `NU_NOTIFIC`
repetidos (chave primária íntegra). A duplicidade nas colunas persistidas é de 1.366 registros
(0,826%), enquanto o pipeline publicava 235 (0,142%) — porque media sobre `ALLOWED_COLUMNS`,
incluindo `NU_IDADE_N`/`TP_IDADE`, que são descartados adiante. Subestimava em 5,8×.

**Decisão.** Corrigir a **medição** (passa a usar as colunas realmente persistidas) e **não
deduplicar**. Sem o identificador da notificação — negado por minimização — não há como distinguir
duplicata real de pacientes distintos com os mesmos atributos agregados. Que `NU_NOTIFIC` seja
íntegro no arquivo confirma que deduplicar removeria casos reais.

---

## D-16 — CORREÇÃO DE ROTA: a duplicidade publicada cai, não sobe

**Evidência.** A instrução do Orchestrator ao Agent 5 repetia a medição do Agent 3 de que a
duplicidade real seria 5,8× maior que a publicada. **Estava errada.** O Agent 5 verificou que
`DeriveAge` já descarta `NU_IDADE_N`/`TP_IDADE` **antes** da linha de contagem, de modo que o
filtro `[c for c in ALLOWED_COLUMNS if c in combined.columns]` nunca as incluiu. A omissão real era
`faixa_etaria`, que é persistida e **não** é função de nenhuma coluna bruta sobrevivente.

**Decisão.** Ratificada a correção do Agent 5: medir sobre o conjunto realmente persistido.
Incluir `faixa_etaria` **separa** duplicatas em vez de fundi-las, e a contagem cai de 1.210 (0,732%)
para **752 (0,455%)**.

**Impacto.** O número publicado passa a descrever a base que existe. A conclusão de não deduplicar
permanece, e fica mais forte: com `NU_NOTIFIC` íntegro (0 repetições medidas) e 0 linhas
integralmente duplicadas nas 194 colunas, deduplicar removeria casos reais.

**Nota de método.** Uma medição de subagente contradisse outra; a verificação no código decidiu.
Nenhuma das duas foi aceita por autoridade.

---

## D-17 — Piso de 0 meses para `TP_IDADE=2`, divergindo do dicionário

**Evidência.** O dicionário declara o domínio de meses como 1-11. Medido pelo Orchestrator, a
distribuição de `NU_IDADE_N` com `TP_IDADE=2` é: `0`→**954**, `1`→7.901, `2`→6.706, `3`→5.016.

**Alternativa considerada.** Aplicar o domínio estrito `[1, 11]` do dicionário, anulando os 954.
Rejeitada por dois motivos, o segundo decisivo:

1. Zero meses converte para zero anos, que cai na faixa `0-4` — exatamente a mesma faixa de
   qualquer idade expressa em dias. A conversão é **provadamente correta**, e a faixa é a única
   coisa persistida (a idade exata é descartada por minimização).
2. Se `0` fosse um valor-padrão de preenchimento, apareceria como **pico** na distribuição.
   Aparece como **954 contra 7.901** em um mês — uma cauda decrescente coerente com recém-nascidos,
   a maioria dos quais é registrada em dias (`TP_IDADE=1`). Não é placeholder.

**Decisão.** Piso 0 para meses, divergência do dicionário **declarada no código** em
`AGE_UNIT_DOMAIN`. Os 5 valores genuinamente fora do domínio (`-9`, `-1`, `13`, `37`, `63`) são
anulados e recebem o ajuste `idade_unidade_implausivel`. A unidade nunca é reinterpretada.

**Impacto.** 5 registros anulados em vez de 959. Evita remover 954 lactentes corretamente
classificados.

---

## D-18 — Regra de `DT_SAIDUTI` sem `DT_ENTUTI` mantida mesmo detectando 0 novos

**Evidência.** O Agent 3 mediu 25 registros com saída de UTI sem entrada. O Agent 5 verificou que
**todos os 25 têm `UTI = 1`** e já eram capturados pela cláusula existente `UTI=1 sem DT_ENTUTI`.
Detecção incremental: zero.

**Decisão.** Manter a regra. A sobreposição total é propriedade **desta safra**, não da fonte: a
nova cláusula não depende de `UTI` estar preenchida, e uma safra futura com `UTI` ausente e
`DT_SAIDUTI` presente escaparia sem ela. Comentário e teste corrigidos para declarar isso, em vez
de sugerir uma detecção que hoje não ocorre.
