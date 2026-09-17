# Documentação

> **Certificação AI Engineering - Vinícius Barbaresco** -- Arquivo entregue: `docs/README.md`

| Artefato | Origem | Conteúdo |
|---|---|---|
| [`arquitetura.pdf`](arquitetura.pdf) | gerado por [`gerar_diagrama_pdf.py`](gerar_diagrama_pdf.py) | Diagrama conceitual da solução (entrega obrigatória) |
| [`dicionario_metricas.md`](dicionario_metricas.md) | gerado por [`gerar_documentacao.py`](gerar_documentacao.py) | Contrato métrica ↔ campo ↔ regra ↔ limitação |
| [`regras_transformacao.md`](regras_transformacao.md) | gerado por [`gerar_documentacao.py`](gerar_documentacao.py) | Contrato de colunas e regras de limpeza |
| [`catalogo_tools.md`](catalogo_tools.md) | gerado por [`gerar_documentacao.py`](gerar_documentacao.py) | Catálogo de tools e políticas de guardrail |
| [`exemplo_relatorio.md`](exemplo_relatorio.md) | gerado por `python main.py --no-llm` (caminhos locais substituídos) | Relatório completo de uma execução real sobre a base 2019/2022–2026 |
| [`pipeline_dados/README.md`](pipeline_dados/README.md) | redigido à mão, com números medidos na base real | Diagnóstico da camada de dados, dicionário analítico, regras epidemiológicas, relatório de qualidade e schema drift executados, e as respostas às treze perguntas obrigatórias sobre tratamento de dados |
| [`pipeline_dados/decisoes.md`](pipeline_dados/decisoes.md) | redigido à mão | Log de decisões da revisão da camada de dados — evidência, alternativa considerada, decisão, impacto — incluindo a rodada de revisão independente (Red Team) |

Os quatro primeiros artefatos são **gerados a partir do código**, não redigidos à mão: as mesmas
`MetricDefinition`, `ToolSpec` e `GuardrailPolicy` que a aplicação executa alimentam a
documentação e o diagrama. Assim uma mudança de comportamento não cria divergência silenciosa
com a documentação — ela aparece no diff da próxima geração.

```bash
python docs/gerar_documentacao.py
python docs/gerar_diagrama_pdf.py
```

Todo documento entregue traz, na primeira linha, a identificação
**Certificação AI Engineering - Vinícius Barbaresco** e o próprio nome de arquivo. Nos artefatos
gerados isso também vem do código: a identificação é a constante `DELIVERY_LABEL` de
[`src/config.py`](../src/config.py), usada pelos dois geradores e reproduzida à mão nos demais
documentos — um único lugar para alterar, sem risco de um artefato sair com identificação diferente.
A lista completa dos arquivos entregues está no
[README principal, seção "Entrega para avaliação"](../README.md#entrega-para-avaliação).

A visão geral da arquitetura, as decisões de projeto e as instruções de execução estão no
[README principal](../README.md).
