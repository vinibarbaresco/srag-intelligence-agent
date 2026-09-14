"""Interface HTTP do agente (FastAPI).

Expoe, sem nenhuma logica propria, o que a CLI ja faz: indicadores e series
via as mesmas tools deterministicas, geracao de relatorio via o mesmo grafo, e
leitura de relatorios e trilhas de auditoria ja gravados. A API e um adaptador
de entrada -- toda regra continua nas camadas de metricas, guardrails e agente.

Uso::

    python -m src.api                # uvicorn em API_HOST:API_PORT
    curl localhost:8000/health
    curl localhost:8000/indicadores/get_mortality_rate?uf=SP
"""

from src.api.app import create_app

__all__ = ["create_app"]
