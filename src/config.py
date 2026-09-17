"""Configuracao centralizada do SRAG Intelligence Agent.

Toda constante operacional, caminho de arquivo e parametro epidemiologico vive
aqui. Nenhum outro modulo deve montar caminhos ou ler variaveis de ambiente por
conta propria -- isso garante que uma unica mudanca de configuracao se propague
por toda a aplicacao e que nenhum segredo fique embutido no codigo.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Ausencia ABSOLUTA tolerada por coluna, em pontos percentuais, no detector de
#: mudanca de esquema (`src/data/drift.py`).
#:
#: Existe porque o limiar relativo (`drift_missing_rate_delta_pp`) so enxerga a
#: DIFERENCA entre duas safras do mesmo ano. Uma coluna que ja estava vazia na
#: safra anterior e continua vazia tem delta zero, e uma safra que estabelece a
#: linha de base nao tem contra o que comparar -- nos dois casos o eixo temporal
#: pode chegar 100% vazio sem que uma unica comparacao relativa reclame. Sem
#: `DT_SIN_PRI` a view analitica fica vazia: nenhum indicador consegue situar o
#: caso no tempo, e `flag_data_invalida` exclui o registro.
#:
#: A calibracao vem das safras medidas, nao de arbitrio:
#:
#: * `DT_SIN_PRI` -- 0,00% ausente em TODAS as safras medidas. O teto de 5 pp e
#:   folga pura sobre um campo que a fonte sempre publica; ausencia acima disso
#:   e defeito de arquivo, nao variacao da fonte.
#: * `DT_DIGITA` -- 34,37% ausente no INFLUD19 e 0,00% no INFLUD25. Os 34,37%
#:   sao um fato legitimo do regime de vigilancia de 2019 e NAO podem virar
#:   ERROR. O teto de 60 pp deixa esse caso real folgado e ainda barra o cenario
#:   que importa (coluna majoritariamente ou totalmente vazia).
#:
#: Colunas fora deste mapa nao tem piso absoluto: para elas ausencia alta pode
#: ser caracteristica do campo (sintoma nao preenchido, vacina sem registro), e
#: so a variacao entre safras e informativa.
DRIFT_MAX_MISSING_PCT: Final[dict[str, float]] = {
    "DT_SIN_PRI": 5.0,
    "DT_DIGITA": 60.0,
}

#: Denominador minimo para que os pisos ABSOLUTOS acima (e o de datas
#: ilegiveis) sejam avaliados.
#:
#: Um piso absoluto e uma taxa, e taxa sobre um punhado de fichas descreve
#: unidades, nao a safra: num arquivo de 8 registros uma unica ficha sem data
#: e 12,5%. As safras reais tem de 48 mil (INFLUD19) a 336 mil fichas, entao
#: 100 registros e um piso que nenhuma safra real alcanca por baixo -- e uma
#: que alcancasse ja seria barrada pela queda de registros, que compara contra
#: a safra anterior do mesmo ano.
DRIFT_RATE_MIN_RECORDS: Final[int] = 100


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
    # Zero por padrao: a interpretacao deve ser reproduzivel entre execucoes
    # com o mesmo contexto. Nao ha uso legitimo de criatividade aqui.
    openai_temperature: float = Field(default=0.0, ge=0.0, le=1.0)

    # --- Agente: selecao de tools pelo modelo --------------------------------
    #
    # O contrato de entrega (indicadores, series e graficos) e deterministico e
    # nao depende destes parametros -- ver `src/agent/tool_calling.py`. Eles
    # limitam APENAS a camada de aprofundamento, em que o modelo pode acionar
    # analises adicionais por function calling.
    agent_tool_calling_enabled: bool = Field(default=True)
    # Teto de chamadas adicionais por execucao. Quatro cobrem os pedidos reais
    # ("compare com o nacional", "olhe 60 dias") e limitam o custo de uma
    # resposta que proponha dezenas de consultas.
    agent_max_tool_calls: int = Field(default=4, ge=0, le=20)
    # Iteracoes do laco de tool calling. Duas bastam para o padrao "pede, ve o
    # aceite, conclui"; mais do que isso e sinal de laco, nao de raciocinio.
    agent_max_tool_iterations: int = Field(default=2, ge=1, le=5)
    # Retentativas de uma chamada que falhou por rede ou limite de taxa.
    agent_max_tool_retries: int = Field(default=1, ge=0, le=5)

    # --- Janela epidemiologica ----------------------------------------------
    reporting_lag_days: int = Field(default=21, ge=0, le=90)
    growth_window_days: int = Field(default=30, ge=1, le=365)

    # --- Censo de UTI --------------------------------------------------------
    # Estadias sem data de saida nem de evolucao sao imputadas ate um teto de
    # permanencia. Por padrao o teto e empirico: o percentil abaixo, medido nas
    # estadias com saida registrada. ICU_STAY_CAP_DAYS fixa um valor e ignora o
    # percentil.
    icu_stay_cap_percentile: float = Field(default=0.95, gt=0.5, le=1.0)
    icu_stay_cap_days: int | None = Field(default=None, ge=1, le=365)

    # --- Privacidade ---------------------------------------------------------
    # Piso de denominador para publicar uma proporcao. Abaixo dele o indicador e
    # suprimido pela regra de celula pequena (src/guardrails/small_cells.py):
    # sobre pouquissimos casos uma taxa nao mede a populacao e se aproxima de
    # descrever individuos. Zero desativa a regra.
    min_cell_size: int = Field(default=5, ge=0)

    # --- Ingestao ------------------------------------------------------------
    # `NoDecode` desliga o parse JSON automatico que o pydantic-settings aplica
    # a campos de tipo composto. Sem ele, `SRAG_YEARS=2025,2026` no .env falha
    # antes do validador abaixo ser chamado, porque nao e JSON valido.
    srag_years: Annotated[list[int], NoDecode] = Field(default=[2025, 2026])

    # --- Deteccao de mudanca de esquema entre safras -------------------------
    # O DATASUS republica o mesmo ano varias vezes, e a comparacao e sempre
    # entre safras do MESMO ano (ver src/data/drift.py). Os tres limiares foram
    # calibrados contra as safras medidas, nao arbitrados.
    #
    # 5 pontos percentuais de variacao de ausencia: uma republicacao completa o
    # que faltava e move pouco a completude do mesmo ano. As diferencas grandes
    # medidas sao entre anos distintos (DT_DIGITA vazia em 34,37% do INFLUD19
    # contra 0,00% do INFLUD25), e a comparacao por ano ja as isola.
    drift_missing_rate_delta_pp: float = Field(default=5.0, ge=0, le=100)
    # 20% de queda de registros interrompe a carga: o ano e republicado de forma
    # cumulativa, entao perder ficha so pode vir de arquivo truncado, download
    # incompleto ou ano trocado. A folga cobre uma revisao de encerramento.
    drift_record_drop_pct: float = Field(default=20.0, ge=0, le=100)
    # 50% de crescimento apenas avisa: medido 165.397 -> 336.391 (+103%) entre
    # duas safras do mesmo INFLUD25. E legitimo e nao pode interromper, mas
    # dobrar o denominador desloca toda a serie historica do ano.
    drift_record_growth_pct: float = Field(default=50.0, ge=0)
    # Percentual de datas ILEGIVEIS (presentes no arquivo e nao interpretaveis
    # em nenhum dos tres formatos publicados) que interrompe a carga. Medido na
    # safra de referencia: 0 ilegiveis em 336.391 fichas -- a fonte publica a
    # data em formato valido ou nao publica nada. O piso e 0,5%: fica acima de
    # qualquer lixo pontual e, de proposito, ABAIXO do 1% de discordancia que a
    # inferencia de tipo tolera (`_KIND_AGREEMENT = 0.99`). As duas checagens
    # passam a se sobrepor em vez de deixar uma faixa cega entre elas -- e esta
    # aqui e a unica das duas que funciona na primeira carga do ano, quando nao
    # ha safra anterior contra a qual comparar tipo nenhum. O cenario que motiva
    # o limiar: uma safra que troca o formato de DT_SIN_PRI no meio do arquivo
    # torna ilegivel um terco das datas, apaga o eixo temporal desses registros
    # e os expulsa da view analitica sem que nada mude o codigo de saida.
    drift_unreadable_dates_pct: float = Field(default=0.5, ge=0, le=100)

    # --- Baseline sazonal ----------------------------------------------------
    # Anos usados como referencia historica para a mesma janela de calendario.
    # 2020 e 2021 ficam SEMPRE fora: a pandemia de covid-19 multiplicou as
    # notificacoes de SRAG e um baseline que os incluisse classificaria qualquer
    # ano normal como "abaixo do esperado". 2019 nao entra no padrao por outro
    # motivo: e o regime de vigilancia pre-pandemico, com cobertura de
    # notificacao muito menor (48 mil casos no ano contra 270 mil ou mais a
    # partir de 2022) -- comparavel em forma, nao em nivel. Pode ser incluido
    # via BASELINE_YEARS; a mediana absorve um ano discrepante, mas nao dois.
    baseline_years: Annotated[list[int], NoDecode] = Field(default=[2022, 2023, 2024])
    # Minimo de anos efetivamente presentes na base para publicar o baseline.
    baseline_min_years: int = Field(default=2, ge=1, le=10)

    # --- Referencias externas ------------------------------------------------
    # Arquivos de referencia versionados em data/reference/. Caminhos
    # alternativos servem aos testes e a bases de referencia proprias.
    population_reference_path: Path | None = Field(default=None)
    vaccination_reference_path: Path | None = Field(default=None)
    icu_capacity_reference_path: Path | None = Field(default=None)

    # Distancia maxima, em meses, entre a competencia da capacidade instalada
    # (CNES) e a data de corte analitica para que a ocupacao de UTI seja
    # publicada.
    #
    # O CNES publica leitos por competencia mensal e a base de SRAG tem sua
    # propria defasagem; as duas raramente coincidem no mesmo mes. Casar um
    # censo de 2026 com uma capacidade de 2019 seria um numero sem significado,
    # entao ha um limite -- e, alem dele, o indicador fica indisponivel com o
    # motivo em vez de sair com um denominador velho. Seis meses e a folga que
    # cobre a defasagem tipica das duas fontes sem atravessar a revisao anual da
    # rede hospitalar.
    icu_capacity_max_lag_months: int = Field(default=6, ge=0, le=60)

    # --- Alertas -------------------------------------------------------------
    # Limiares avaliados a cada execucao (src/monitoring/alerts.py). Um alerta
    # disparado aparece no relatorio e, com --fail-on-alert, muda o codigo de
    # saida -- e assim que a execucao agendada sinaliza uma mudanca de cenario.
    alert_growth_threshold_pct: float = Field(default=20.0, ge=0)
    alert_mortality_threshold_pct: float = Field(default=10.0, ge=0, le=100)
    alert_baseline_excess_threshold_pct: float = Field(default=50.0, ge=0)

    # --- Guardrail semantico -------------------------------------------------
    # Segunda camada sobre a saida do modelo: um revisor independente (outro
    # prompt, sem acesso ao texto do pedido original) procura conduta clinica,
    # numero sem lastro e obediencia a instrucoes vindas de noticias. So atua
    # quando ha credencial; sem ela, a camada lexical continua sozinha.
    semantic_judge_enabled: bool = Field(default=True)
    # Modelo do revisor. Na calibracao, gpt-4o-mini apontou "conduta clinica"
    # em frases sobre vies estatistico e "instrucao externa" em resumos de
    # manchetes com a fonte atribuida -- falsos positivos que derrubavam toda
    # interpretacao para a via deterministica. gpt-4o aprovou os mesmos textos
    # de forma estavel. O revisor le ~2 mil tokens por execucao, entao o
    # modelo mais forte custa centavos; o redator continua sendo OPENAI_MODEL.
    openai_judge_model: str = Field(default="gpt-4o")

    # --- Custo do modelo -----------------------------------------------------
    # Precos de lista por milhao de tokens, usados apenas para ESTIMAR o custo
    # de cada execucao no relatorio. Padrao: gpt-4o-mini (USD). Ajuste ao
    # trocar de modelo; o relatorio rotula o valor como estimativa.
    openai_input_price_per_1m_tokens: float = Field(default=0.15, ge=0)
    openai_output_price_per_1m_tokens: float = Field(default=0.60, ge=0)
    # Precos do modelo do revisor (padrao: gpt-4o, USD).
    openai_judge_input_price_per_1m_tokens: float = Field(default=2.50, ge=0)
    openai_judge_output_price_per_1m_tokens: float = Field(default=10.00, ge=0)

    # --- API HTTP ------------------------------------------------------------
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000, ge=1, le=65535)

    # --- Noticias ------------------------------------------------------------
    news_max_age_days: int = Field(default=45, ge=1, le=365)
    news_max_results: int = Field(default=12, ge=1, le=100)
    news_refresh_on_run: bool = Field(default=True)

    # --- Observabilidade -----------------------------------------------------
    log_level: str = Field(default="INFO")

    # --- Raiz de dados -------------------------------------------------------
    # Redireciona `data/` e `outputs/` para outro diretorio. Serve aos testes,
    # que montam uma base sintetica isolada, e a implantacoes em que o volume de
    # dados nao fica junto do codigo.
    data_root: Path | None = Field(default=None)

    @field_validator("srag_years", "baseline_years", mode="before")
    @classmethod
    def _parse_years(cls, value: object) -> object:
        """Aceita `SRAG_YEARS=2025,2026` (e `BASELINE_YEARS`) alem da lista nativa.

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
    def history_dir(self) -> Path:
        return self.outputs_dir / "history"

    @property
    def run_history_path(self) -> Path:
        """Historico de execucoes: uma linha JSON por relatorio gerado.

        Permite comparar a execucao corrente com a anterior de mesmo recorte
        (variacao dos indicadores entre relatorios) e alimenta os alertas.
        """
        return self.history_dir / "runs.jsonl"

    @property
    def docs_dir(self) -> Path:
        return PROJECT_ROOT / "docs"

    @property
    def reference_dir(self) -> Path:
        """Dados de referencia externos, pequenos e versionados com o codigo.

        Nao segue `DATA_ROOT` de proposito: populacao do IBGE e cobertura
        vacinal sao insumo do calculo, nao artefato de execucao, e precisam
        acompanhar a versao do codigo que os interpreta.
        """
        return PROJECT_ROOT / "data" / "reference"

    @property
    def population_reference_file(self) -> Path:
        return self.population_reference_path or (self.reference_dir / "populacao_uf.csv")

    @property
    def vaccination_reference_file(self) -> Path:
        return self.vaccination_reference_path or (self.reference_dir / "cobertura_vacinal_uf.csv")

    @property
    def icu_capacity_reference_file(self) -> Path:
        """Capacidade instalada de leitos de UTI por UF e competencia (CNES)."""
        return self.icu_capacity_reference_path or (self.reference_dir / "leitos_uti_uf.csv")

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
    def schema_baseline_path(self) -> Path:
        """Linha de base do esquema do arquivo bruto, por ano.

        Vive em `data/processed` porque segue `DATA_ROOT` -- os testes precisam
        de uma linha de base isolada, e uma carga apontada para outra raiz de
        dados nao pode escrever sobre a linha de base do projeto.

        E o unico arquivo de `data/processed` que o `.gitignore` PERMITE
        versionar, e a excecao existe porque ele descreve o **contrato** com a
        fonte: a revisao de uma mudanca de esquema comeca pelo diff dele. Ainda
        assim o repositorio **nao** traz uma linha de base commitada, e a
        omissao e deliberada -- a linha de base e derivada dos CSVs brutos, que
        nao sao versionados (centenas de MB, reproduziveis via
        `src.data.download`). Uma linha de base escrita a mao seria uma
        afirmacao sobre arquivos que nem o clone nem a CI possuem, e o diff dela
        nao seria verificavel contra nada.

        A consequencia operacional precisa ficar dita em vez de subentendida:
        em clone novo e na CI a **primeira** carga nao compara nada -- ela
        apenas estabelece a linha de base, e o relatorio diz exatamente isso
        ("linha de base estabelecida, sem comparacao possivel"). A protecao do
        detector comeca na segunda carga. Quem quiser proteger tambem a
        primeira commita o `schema_baseline.json` gerado por uma carga de
        referencia, que e para isso que a excecao do `.gitignore` existe.
        """
        return self.processed_dir / "schema_baseline.json"

    @property
    def schema_drift_path(self) -> Path:
        """Achados de mudanca de esquema da carga mais recente.

        Existe separado do `quality_report.json` por causa do caso em que ele
        mais importa: uma carga interrompida por ERROR de esquema nao gera
        relatorio de qualidade -- ela nao chegou a produzir dado --, e sem este
        arquivo o motivo da interrupcao ficaria apenas no log.
        """
        return self.processed_dir / "schema_drift.json"

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
            self.history_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


# --- Fonte de dados ----------------------------------------------------------

DATASUS_DATASET_URL = "https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026"
DATASUS_DICTIONARY_URL = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/dicionario-de-dados-2019-a-2025.pdf"
)
DATASUS_SOURCE_LABEL = "Open DATASUS / SIVEP-Gripe (SRAG 2019-2026)"

# --- Identificacao da entrega ------------------------------------------------
#
# Cada documento entregue carrega esta identificacao e o proprio nome de
# arquivo, para que continue identificavel fora do repositorio -- impresso,
# anexado a um e-mail ou aberto isolado. A string mora aqui, e nao repetida em
# cada gerador, porque documento gerado e documento escrito a mao precisam
# exibir exatamente a mesma linha.
DELIVERY_LABEL = "Certificação AI Engineering - Vinícius Barbaresco"

# Formato do arquivo bruto, verificado na fonte em 2026-09.
RAW_CSV_SEPARATOR = ";"

# O encoding NAO e uma constante: ele nao e estavel entre safras do DATASUS
# (as publicacoes recentes sao UTF-8, as antigas latin-1) e e detectado por
# arquivo em `src/data/encoding.py`, que tambem declara o fallback. Fixa-lo
# aqui foi o defeito corrigido em D-01, e uma constante sem uso so convidaria
# a reintroduzi-lo.

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
