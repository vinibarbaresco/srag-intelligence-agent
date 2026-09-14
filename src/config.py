"""Configuracao centralizada do SRAG Intelligence Agent.

Toda constante operacional, caminho de arquivo e parametro epidemiologico vive
aqui. Nenhum outro modulo deve montar caminhos ou ler variaveis de ambiente por
conta propria -- isso garante que uma unica mudanca de configuracao se propague
por toda a aplicacao e que nenhum segredo fique embutido no codigo.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Parametros da aplicacao, carregados de variaveis de ambiente e `.env`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM -----------------------------------------------------------------
    openai_api_key: str | None = Field(default=None)
    openai_model: str = Field(default="gpt-4o-mini")
    openai_embedding_model: str = Field(default="text-embedding-3-small")

    # --- Janela epidemiologica ----------------------------------------------
    reporting_lag_days: int = Field(default=21, ge=0, le=90)
    growth_window_days: int = Field(default=30, ge=1, le=365)

    # --- Privacidade ---------------------------------------------------------
    min_cell_size: int = Field(default=5, ge=0)

    # --- Ingestao ------------------------------------------------------------
    # `NoDecode` desliga o parse JSON automatico que o pydantic-settings aplica
    # a campos de tipo composto. Sem ele, `SRAG_YEARS=2025,2026` no .env falha
    # antes do validador abaixo ser chamado, porque nao e JSON valido.
    srag_years: Annotated[list[int], NoDecode] = Field(default=[2025, 2026])

    # --- Noticias ------------------------------------------------------------
    news_max_age_days: int = Field(default=45, ge=1, le=365)
    news_max_results: int = Field(default=12, ge=1, le=100)

    # --- Observabilidade -----------------------------------------------------
    log_level: str = Field(default="INFO")

    # --- Raiz de dados -------------------------------------------------------
    # Redireciona `data/` e `outputs/` para outro diretorio. Serve aos testes,
    # que montam uma base sintetica isolada, e a implantacoes em que o volume de
    # dados nao fica junto do codigo.
    data_root: Path | None = Field(default=None)

    @field_validator("srag_years", mode="before")
    @classmethod
    def _parse_years(cls, value: object) -> object:
        """Aceita `SRAG_YEARS=2025,2026` alem da forma lista nativa.

        Raises:
            ValueError: se algum item nao for um ano inteiro.
        """
        if isinstance(value, str):
            try:
                return [int(part.strip()) for part in value.split(",") if part.strip()]
            except ValueError as exc:
                raise ValueError(
                    f"SRAG_YEARS invalido: {value!r}. Use anos separados por "
                    "virgula, por exemplo: SRAG_YEARS=2025,2026"
                ) from exc
        return value

    # --- Caminhos ------------------------------------------------------------
    @property
    def _root(self) -> Path:
        return self.data_root or PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        return self._root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def analytics_dir(self) -> Path:
        return self.data_dir / "analytics"

    @property
    def outputs_dir(self) -> Path:
        return self._root / "outputs"

    @property
    def charts_dir(self) -> Path:
        return self.outputs_dir / "charts"

    @property
    def reports_dir(self) -> Path:
        return self.outputs_dir / "reports"

    @property
    def audit_dir(self) -> Path:
        return self.outputs_dir / "audit"

    @property
    def docs_dir(self) -> Path:
        return PROJECT_ROOT / "docs"

    @property
    def database_path(self) -> Path:
        """Banco analitico consultado pelas tools deterministicas."""
        return self.analytics_dir / "srag.duckdb"

    @property
    def vector_store_path(self) -> Path:
        """Vector DB de noticias (busca semantica de contexto externo)."""
        return self.analytics_dir / "news_vectors.duckdb"

    @property
    def processed_parquet_path(self) -> Path:
        return self.processed_dir / "srag_cases.parquet"

    @property
    def quality_report_path(self) -> Path:
        return self.processed_dir / "quality_report.json"

    @property
    def raw_manifest_path(self) -> Path:
        return self.raw_dir / "manifest.json"

    @property
    def ingestion_history_path(self) -> Path:
        """Historico de cargas, uma linha JSON por execucao da ingestao.

        `quality_report.json` guarda apenas a carga mais recente. Este arquivo
        acumula o resumo de todas, permitindo comparar o que mudou entre duas
        versoes da base -- inclusive detectar uma degradacao de qualidade na
        fonte.
        """
        return self.processed_dir / "ingestion_history.jsonl"

    @property
    def llm_enabled(self) -> bool:
        """Indica se ha credencial para a camada de interpretacao."""
        return bool(self.openai_api_key)

    def ensure_directories(self) -> None:
        """Cria a arvore de diretorios de trabalho, se ainda nao existir."""
        for directory in (
            self.raw_dir,
            self.processed_dir,
            self.analytics_dir,
            self.charts_dir,
            self.reports_dir,
            self.audit_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


# --- Fonte de dados ----------------------------------------------------------

DATASUS_DATASET_URL = "https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026"
DATASUS_DICTIONARY_URL = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/"
    "dicionario-de-dados-2019-a-2025.pdf"
)
DATASUS_SOURCE_LABEL = "Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)"

# Formato do arquivo bruto, verificado na fonte em 2026-09.
RAW_CSV_SEPARATOR = ";"
RAW_CSV_ENCODING = "latin-1"

# Nenhuma data anterior a esta e considerada valida (inicio da serie SIVEP-Gripe
# publicada neste dataset).
MIN_VALID_DATE = "2019-01-01"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna a instancia unica de configuracao da aplicacao."""
    return Settings()


def reset_settings_cache() -> None:
    """Descarta a configuracao memorizada.

    Necessario apenas quando o ambiente muda dentro do mesmo processo -- e o
    caso dos testes, que apontam `SRAG_DATA_ROOT` para um diretorio temporario.
    """
    get_settings.cache_clear()
