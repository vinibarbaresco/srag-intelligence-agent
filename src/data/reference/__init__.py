"""Dados de referencia externos ao SIVEP-Gripe.

O dataset de SRAG nao tem denominador populacional nem doses de vacina
aplicadas. Os dois indicadores que dependem disso -- incidencia por 100 mil
habitantes e cobertura vacinal da populacao -- so podem ser calculados com uma
fonte externa, e este pacote e o unico lugar em que ela entra no sistema.

Cada referencia e um CSV pequeno, versionado em `data/reference/`, acompanhado
de um arquivo de proveniencia (`*.provenance.json`) com fonte, URL, periodo,
data de obtencao e sha256. A camada analitica le a referencia de uma tabela do
DuckDB carregada por :func:`src.data.reference.tables.load_reference_tables`; as
metricas nunca acessam o arquivo diretamente.
"""
