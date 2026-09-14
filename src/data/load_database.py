"""Carga da camada processada no banco analitico DuckDB.

O DuckDB e a unica fonte consultada pelas tools deterministicas. A tabela
`srag_cases` e materializada a partir do Parquet e enriquecida com as views que
as metricas usam, de modo que a mesma definicao de "caso", "obito" e "admissao
em UTI" valha para indicadores, series temporais e graficos -- sem calculo
duplicado.

A tabela `audit_events` e criada aqui para que a trilha de auditoria de todas as
execucoes possa ser consultada com SQL.

Uso::

    python -m src.data.load_database
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from src.config import get_settings
from src.data.schema import DENIED_COLUMNS, DERIVED_SEMANTIC_COLUMNS
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

TABLE_CASES = "srag_cases"
TABLE_AUDIT = "audit_events"
VIEW_ANALYTICS = "srag_analytics"

#: Definicao canonica do recorte analitico, compartilhada por todas as metricas.
#:
#: Apenas `flag_data_invalida` exclui o registro: sem eixo temporal utilizavel
#: nenhuma metrica consegue situar o caso. As demais flags de coerencia seguem
#: disponiveis como colunas, para que cada metrica exclua somente o que
#: compromete o seu proprio calculo -- uma data de internacao impossivel nao
#: deve derrubar o registro da contagem de casos.
#:
#: Os registros excluidos permanecem em `srag_cases`, e a diferenca entre as
#: duas contagens e reportada.
#: Recorte analitico canonico, compartilhado por todas as metricas.
#:
#: A view faz apenas **projecao de tipo**: converte timestamp para data e
#: trunca o mes. Toda a semantica -- o que e um obito, um caso encerrado, uma
#: admissao em UTI -- e derivada em Python, na camada de tratamento, ao lado do
#: dicionario de codigos que a define (`src/data/cleaning/derived.py`). Manter a
#: traducao dos codigos em SQL permitiria que uma mudanca no dicionario nao
#: chegasse ao calculo sem que nada falhasse.
#:
#: Apenas `flag_data_invalida` exclui o registro: sem eixo temporal utilizavel
#: nenhuma metrica consegue situar o caso. As demais flags de coerencia seguem
#: disponiveis como colunas, para que cada metrica exclua somente o que
#: compromete o seu proprio calculo.
#:
#: Os registros excluidos permanecem em `srag_cases`, e a diferenca entre as
#: duas contagens e reportada.
_ANALYTICS_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {VIEW_ANALYTICS} AS
SELECT
    *,
    CAST(DT_SIN_PRI AS DATE)                      AS data_sintomas,
    CAST(DT_DIGITA  AS DATE)                      AS data_digitacao,
    CAST(DT_EVOLUCA AS DATE)                      AS data_evolucao,
    CAST(DT_ENTUTI  AS DATE)                      AS data_entrada_uti,
    CAST(DT_SAIDUTI AS DATE)                      AS data_saida_uti,
    date_trunc('month', CAST(DT_SIN_PRI AS DATE)) AS mes_sintomas
FROM {TABLE_CASES}
WHERE flag_data_invalida = FALSE
"""

_AUDIT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_AUDIT} (
    run_id         VARCHAR,
    seq            INTEGER,
    timestamp      TIMESTAMP,
    node           VARCHAR,
    tool           VARCHAR,
    parameters     VARCHAR,
    status         VARCHAR,
    duration_ms    DOUBLE,
    result_summary VARCHAR,
    source         VARCHAR,
    error          VARCHAR
)
"""


@contextmanager
def connect(read_only: bool = True, path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Abre uma conexao com o banco analitico.

    Args:
        read_only: abre em modo somente leitura. As tools sempre usam `True` --
            nenhuma consulta do agente pode alterar a base (Guardrail 4).
        path: caminho alternativo do banco (usado pelos testes).

    Yields:
        Conexao DuckDB, fechada ao final do bloco.

    Raises:
        FileNotFoundError: se o banco ainda nao foi criado.
    """
    database_path = path or get_settings().database_path
    if read_only and not database_path.exists():
        raise FileNotFoundError(
            f"Banco analitico nao encontrado em {database_path}. Execute: "
            "python -m src.data.load_database"
        )

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(database_path), read_only=read_only)
    try:
        yield connection
    finally:
        connection.close()


def load_database(parquet_path: Path | None = None, database_path: Path | None = None) -> Path:
    """Materializa `srag_cases` e as views analiticas a partir do Parquet.

    Args:
        parquet_path: camada processada; padrao `data/processed/srag_cases.parquet`.
        database_path: destino; padrao `data/analytics/srag.duckdb`.

    Returns:
        Caminho do banco gerado.

    Raises:
        FileNotFoundError: se a camada processada nao existir.
        ValueError: se alguma coluna com dado pessoal chegou ao banco.
    """
    settings = get_settings()
    settings.ensure_directories()
    source = parquet_path or settings.processed_parquet_path
    target = database_path or settings.database_path

    if not source.exists():
        raise FileNotFoundError(
            f"Camada processada nao encontrada em {source}. Execute: "
            "python -m src.data.preprocess"
        )

    with connect(read_only=False, path=target) as connection:
        connection.execute(
            f"CREATE OR REPLACE TABLE {TABLE_CASES} AS "
            "SELECT * FROM read_parquet(?)",
            [str(source)],
        )
        connection.execute(_ANALYTICS_VIEW_SQL)
        connection.execute(_AUDIT_TABLE_SQL)

        columns = [row[0] for row in connection.execute(f"DESCRIBE {TABLE_CASES}").fetchall()]
        violations = sorted(set(columns) & set(DENIED_COLUMNS))
        if violations:
            raise ValueError(
                f"Colunas com dados pessoais presentes no banco analitico: {violations}"
            )

        # A semantica e derivada no tratamento, nao aqui. Se as colunas nao
        # chegarem, a view compila mas as metricas falhariam so na consulta --
        # melhor falhar na carga, com a causa explicita.
        missing = sorted(set(DERIVED_SEMANTIC_COLUMNS) - set(columns))
        if missing:
            raise ValueError(
                "Camada processada sem as colunas semanticas derivadas: "
                f"{missing}. Reexecute: python -m src.data.preprocess"
            )

        total = connection.execute(f"SELECT count(*) FROM {TABLE_CASES}").fetchone()[0]
        analytic = connection.execute(f"SELECT count(*) FROM {VIEW_ANALYTICS}").fetchone()[0]

    logger.info(
        "banco analitico carregado",
        extra={
            "banco": str(target),
            "linhas_tabela": total,
            "linhas_view_analitica": analytic,
            "linhas_excluidas_da_view": total - analytic,
        },
    )
    return target


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(description="Carrega o Parquet processado no DuckDB.")
    parser.add_argument("--parquet", type=Path, default=None)
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        path = load_database(args.parquet, args.database)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("carga falhou", extra={"motivo": str(exc)})
        return 1

    print(f"Banco analitico: {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
