"""Carga das referencias externas no banco analitico.

As metricas consultam tabelas do DuckDB, nunca os CSVs. Isto mantem o SQL das
metricas homogeneo (um unico banco, um unico modo somente leitura) e faz a
ausencia de uma referencia aparecer como **tabela vazia**, que a metrica traduz
em "nao calculavel" com o motivo -- em vez de um erro de arquivo no meio da
execucao.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from src.config import get_settings
from src.data.reference.population import PopulationReferenceError, read_population_reference
from src.data.reference.vaccination import (
    VaccinationReferenceError,
    read_vaccination_reference,
)
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

TABLE_POPULATION: Final[str] = "populacao_uf"
TABLE_VACCINATION: Final[str] = "cobertura_vacinal_uf"

_POPULATION_TABLE_SQL = f"""
CREATE OR REPLACE TABLE {TABLE_POPULATION} (
    uf        VARCHAR,
    ano       INTEGER,
    populacao BIGINT
)
"""

_VACCINATION_TABLE_SQL = f"""
CREATE OR REPLACE TABLE {TABLE_VACCINATION} (
    uf              VARCHAR,
    ano             INTEGER,
    campanha        VARCHAR,
    doses_aplicadas BIGINT,
    populacao_alvo  BIGINT,
    fonte           VARCHAR,
    url             VARCHAR
)
"""


def _nullable(value: Any, cast: Any) -> Any:
    """Converte NA/NaN do pandas em NULL do banco; caso contrario aplica `cast`."""
    return None if pd.isna(value) else cast(value)


def load_reference_tables(connection: Any) -> dict[str, int]:
    """(Re)cria as tabelas de referencia e as popula a partir dos CSVs.

    Uma referencia ausente ou invalida deixa a tabela correspondente vazia e
    registra o motivo em log; a carga do banco principal nao e interrompida.

    Returns:
        Mapa `tabela -> linhas carregadas`.
    """
    settings = get_settings()
    loaded: dict[str, int] = {}

    connection.execute(_POPULATION_TABLE_SQL)
    try:
        population = read_population_reference(settings.population_reference_file)
    except PopulationReferenceError as exc:
        logger.warning("referencia populacional nao carregada", extra={"motivo": str(exc)})
        loaded[TABLE_POPULATION] = 0
    else:
        connection.executemany(
            f"INSERT INTO {TABLE_POPULATION} VALUES (?, ?, ?)",
            [
                (str(row.uf), int(row.ano), int(row.populacao))
                for row in population.itertuples(index=False)
            ],
        )
        loaded[TABLE_POPULATION] = int(len(population))

    connection.execute(_VACCINATION_TABLE_SQL)
    try:
        vaccination = read_vaccination_reference(settings.vaccination_reference_file)
    except VaccinationReferenceError as exc:
        logger.info("referencia de cobertura vacinal nao carregada", extra={"motivo": str(exc)})
        loaded[TABLE_VACCINATION] = 0
    else:
        connection.executemany(
            f"INSERT INTO {TABLE_VACCINATION} VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(row.uf),
                    int(row.ano),
                    str(row.campanha),
                    int(row.doses_aplicadas),
                    _nullable(row.populacao_alvo, int),
                    _nullable(row.fonte, str),
                    _nullable(row.url, str),
                )
                for row in vaccination.itertuples(index=False)
            ],
        )
        loaded[TABLE_VACCINATION] = int(len(vaccination))

    logger.info("tabelas de referencia carregadas", extra=loaded)
    return loaded
