# Documentação

| Artefato | Origem | Conteúdo |
|---|---|---|
| [`arquitetura.pdf`](arquitetura.pdf) | gerado por [`gerar_diagrama_pdf.py`](gerar_diagrama_pdf.py) | Diagrama conceitual da solução (entrega obrigatória) |
| [`dicionario_metricas.md`](dicionario_metricas.md) | gerado por [`gerar_documentacao.py`](gerar_documentacao.py) | Contrato métrica ↔ campo ↔ regra ↔ limitação |
| [`regras_transformacao.md`](regras_transformacao.md) | gerado por [`gerar_documentacao.py`](gerar_documentacao.py) | Contrato de colunas e regras de limpeza |
| [`catalogo_tools.md`](catalogo_tools.md) | gerado por [`gerar_documentacao.py`](gerar_documentacao.py) | Catálogo de tools e políticas de guardrail |
| [`exemplo_relatorio.md`](exemplo_relatorio.md) | gerado por `python main.py --no-llm` (caminhos locais substituídos) | Relatório completo de uma execução real sobre a base 2019/2022–2026 |

Os quatro primeiros artefatos são **gerados a partir do código**, não redigidos à mão: as mesmas
`MetricDefinition`, `ToolSpec` e `GuardrailPolicy` que a aplicação executa alimentam a
documentação e o diagrama. Assim uma mudança de comportamento não cria divergência silenciosa
com a documentação — ela aparece no diff da próxima geração.

```bash
python docs/gerar_documentacao.py
python docs/gerar_diagrama_pdf.py
```

A visão geral da arquitetura, as decisões de projeto e as instruções de execução estão no
[README principal](../README.md).
