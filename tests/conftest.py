"""Fixtures compartilhadas dos testes.

Os testes rodam sobre uma base **sintetica** de valores conhecidos, montada em
diretorio temporario. Isso mantem a suite rapida, independente da rede e do
download de 600 MB do DATASUS, e permite afirmar resultados exatos -- inclusive
os casos de borda (denominador zero, codigo 9, data invalida) que raramente
aparecem em volume suficiente na base real.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.config import reset_settings_cache
from src.data.cleaning.base import CleaningContext
from src.data.cleaning.derived import DeriveSemanticFlags
from src.data.cleaning.epiweek import DeriveEpidemiologicalWeek
from src.data.quality import AdjustmentLog, QualityReport

#: Data de digitacao mais recente da base sintetica. Todas as janelas dos testes
#: sao ancoradas nela, exatamente como o sistema faz em producao.
REFERENCE_DATE = date(2026, 8, 23)

#: Corte analitico esperado com REPORTING_LAG_DAYS = 21.
EXPECTED_CUTOFF = REFERENCE_DATE - timedelta(days=21)

#: Atraso entre sintomas e digitacao nas janelas comparadas pela taxa de
#: aumento. Menor que REPORTING_LAG_DAYS, de modo que os casos das duas janelas
#: ja estejam digitados quando cada janela e observada.
_REPORTING_DELAY_DAYS = 10

#: Populacao sintetica por UF (IBGE simulado): SP 10 mi, RJ 5 mi, demais 1 mi.
#: Total de 40 milhoes. O validador exige as 27 UFs, como a referencia real.
SYNTHETIC_POPULATION = {"SP": 10_000_000, "RJ": 5_000_000}
SYNTHETIC_POPULATION_DEFAULT = 1_000_000
SYNTHETIC_POPULATION_YEAR = 2026

#: Casos por ano de baseline na mesma janela de calendario da janela atual.
#: Mediana = 150 = casos da janela atual em SP -> excesso de 0% no recorte SP e
#: de (151 - 150) / 150 = 0,67% no nacional (o caso de RJ entra no numerador).
SYNTHETIC_BASELINE_CASES = {2023: 180, 2024: 120}


@pytest.fixture(scope="session")
def data_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Diretorio isolado que substitui `data/` e `outputs/` durante os testes."""
    return tmp_path_factory.mktemp("srag-test-root")


@pytest.fixture(scope="session", autouse=True)
def configured_environment(data_root: Path, monkeypatch_session) -> None:
    """Aponta a configuracao da aplicacao para o diretorio temporario."""
    monkeypatch_session.setenv("DATA_ROOT", str(data_root))
    monkeypatch_session.setenv("REPORTING_LAG_DAYS", "21")
    monkeypatch_session.setenv("GROWTH_WINDOW_DAYS", "30")
    # Atualizacao em tempo real e testada com mock em um caso dedicado. O resto
    # da suite permanece hermetico e nunca acessa feeds externos.
    monkeypatch_session.setenv("NEWS_REFRESH_ON_RUN", "false")
    # Credencial vazia, e nao ausente: a variavel de ambiente tem precedencia
    # sobre o arquivo .env no pydantic-settings, entao isto neutraliza uma chave
    # real presente na maquina do desenvolvedor. Sem isso a suite deixaria de ser
    # hermetica e poderia chamar a API da OpenAI durante os testes.
    monkeypatch_session.setenv("OPENAI_API_KEY", "")
    # Referencias externas sinteticas: a populacao e gravada aqui, e a de
    # cobertura vacinal aponta para um arquivo que so alguns testes criam.
    population_path = data_root / "populacao_uf.csv"
    _write_synthetic_population(population_path)
    monkeypatch_session.setenv("POPULATION_REFERENCE_PATH", str(population_path))
    monkeypatch_session.setenv(
        "VACCINATION_REFERENCE_PATH", str(data_root / "cobertura_vacinal_uf.csv")
    )
    # 2022 configurado mas ausente na base sintetica: exercita a declaracao de
    # anos ausentes sem impedir o calculo (minimo de 2 anos presentes).
    monkeypatch_session.setenv("BASELINE_YEARS", "2022,2023,2024")
    monkeypatch_session.setenv("BASELINE_MIN_YEARS", "2")
    reset_settings_cache()


def _write_synthetic_population(path: Path) -> None:
    from src.data.schema import UF_CODES

    lines = ["uf,ano,populacao"]
    for uf in sorted(UF_CODES):
        population = SYNTHETIC_POPULATION.get(uf, SYNTHETIC_POPULATION_DEFAULT)
        lines.append(f"{uf},{SYNTHETIC_POPULATION_YEAR},{population}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="session")
def monkeypatch_session():
    """`monkeypatch` com escopo de sessao (o embutido e por teste)."""
    patcher = pytest.MonkeyPatch()
    yield patcher
    patcher.undo()


def _build_synthetic_frame() -> pd.DataFrame:
    """Monta a base sintetica com contagens deliberadamente verificaveis.

    Composicao da janela atual (30 dias ate o corte) e da anterior:

    * janela anterior: 100 casos;
    * janela atual: 150 casos  -> taxa de aumento esperada de +50%;
    * na janela atual, 60 casos encerrados dos quais 15 sao obitos por SRAG
      -> mortalidade esperada de 25%;
    * 100 hospitalizados na janela atual com UTI informado, 40 admitidos em UTI
      -> taxa de admissao esperada de 40%. Uma dessas 40 estadias tem datas
      incoerentes: continua contando na taxa de admissao, mas fica fora do
      censo diario (39 estadias utilizaveis);
    * 120 casos com informacao vacinal de covid, 30 vacinados
      -> cobertura declarada esperada de 25%.

    Alem disso a base contem registros com codigo 9 (Ignorado), datas
    inconsistentes e evolucao ausente, para exercitar o tratamento de ausencia.
    """
    rows: list[dict] = []
    cutoff = EXPECTED_CUTOFF
    current_start = cutoff - timedelta(days=29)
    previous_start = current_start - timedelta(days=30)

    def add(
        symptoms: date,
        *,
        evolucao=None,
        hospital=None,
        uti=None,
        vacina_cov=None,
        vacina=None,
        uf="SP",
        classi_fin=5,
        entrada_uti=None,
        saida_uti=None,
        digitacao=None,
        evolucao_data=None,
        suport_ven=None,
        nosocomial=2,
        criterio=1,
    ) -> None:
        rows.append(
            {
                "DT_SIN_PRI": pd.Timestamp(symptoms),
                "DT_INTERNA": pd.Timestamp(symptoms),
                "DT_ENTUTI": pd.Timestamp(entrada_uti) if entrada_uti else pd.NaT,
                "DT_SAIDUTI": pd.Timestamp(saida_uti) if saida_uti else pd.NaT,
                "DT_EVOLUCA": pd.Timestamp(evolucao_data) if evolucao_data else pd.NaT,
                "DT_ENCERRA": pd.Timestamp(evolucao_data) if evolucao_data else pd.NaT,
                "DT_DIGITA": pd.Timestamp(digitacao or REFERENCE_DATE),
                "CS_SEXO": "F",
                "HOSPITAL": hospital,
                "UTI": uti,
                "SUPORT_VEN": suport_ven,
                "NOSOCOMIAL": nosocomial,
                "CLASSI_FIN": classi_fin,
                "CRITERIO": criterio,
                "EVOLUCAO": evolucao,
                "VACINA": vacina,
                "VACINA_COV": vacina_cov,
                "SG_UF_NOT": uf,
                "SG_UF": uf,
                "faixa_etaria": "40-49",
                "ano_referencia": symptoms.year,
                # As flags de coerencia sao definidas explicitamente aqui: a base
                # sintetica e construida coerente, e os casos problematicos sao
                # adicionados de proposito no fim de `_build_synthetic_frame`.
                "flag_data_invalida": False,
                "flag_internacao_inconsistente": False,
                "flag_uti_inconsistente": False,
                "flag_evolucao_inconsistente": False,
                "flag_data_implausivel": False,
            }
        )

    # Atraso de notificacao das janelas comparadas.
    #
    # Todo caso das duas janelas e digitado `_REPORTING_DELAY_DAYS` dias apos o
    # inicio dos sintomas, em vez de todos na data de referencia. A diferenca
    # importa: com digitacao uniforme na data de referencia, a janela anterior
    # apareceria como notificada com 30 a 80 dias de atraso, e a comparacao de
    # maturidade simetrica -- que conta cada janela como ela era conhecida
    # `REPORTING_LAG_DAYS` dias apos o proprio fechamento -- esvaziaria a janela
    # anterior. Uma base sintetica em que ninguem atrasa nao consegue exercitar
    # atraso de notificacao.
    #
    # A data de referencia continua ancorada em REFERENCE_DATE pelos registros
    # que nao passam `digitacao` explicitamente.

    # --- Janela anterior: exatamente 100 casos -------------------------------
    for index in range(100):
        day = previous_start + timedelta(days=index % 30)
        add(day, digitacao=day + timedelta(days=_REPORTING_DELAY_DAYS))

    # --- Janela atual: exatamente 150 casos ----------------------------------
    for index in range(150):
        day = current_start + timedelta(days=index % 30)
        digitacao = min(day + timedelta(days=_REPORTING_DELAY_DAYS), REFERENCE_DATE)

        # 60 casos encerrados: 15 obitos por SRAG, 5 por outras causas, 40 curas.
        if index < 15:
            evolucao = 2
        elif index < 20:
            evolucao = 3
        elif index < 60:
            evolucao = 1
        elif index < 70:
            evolucao = 9  # Ignorado: fora do numerador e do denominador
        else:
            evolucao = None  # em aberto

        # 100 hospitalizados com UTI informado: 40 admitidos em UTI.
        if index < 40:
            hospital, uti = 1, 1
            entrada, saida = day, day + timedelta(days=3)
        elif index < 100:
            hospital, uti = 1, 2
            entrada = saida = None
        elif index < 110:
            hospital, uti = 1, 9  # Ignorado
            entrada = saida = None
        else:
            hospital, uti = 2, None
            entrada = saida = None

        # 120 casos com informacao vacinal de covid: 30 vacinados.
        if index < 30:
            vacina_cov = 1
        elif index < 120:
            vacina_cov = 2
        elif index < 135:
            vacina_cov = 9
        else:
            vacina_cov = None

        add(
            day,
            evolucao=evolucao,
            hospital=hospital,
            uti=uti,
            vacina_cov=vacina_cov,
            vacina=1 if index < 20 else 2,
            entrada_uti=entrada,
            saida_uti=saida,
            evolucao_data=day + timedelta(days=5) if evolucao in (1, 2, 3) else None,
            digitacao=digitacao,
        )

    # --- Registro inconsistente: deve ficar fora da view analitica ------------
    invalid = rows[-1].copy()
    invalid["flag_data_invalida"] = True
    rows.append(invalid)

    # --- Registro de outra UF, para validar o filtro --------------------------
    add(current_start + timedelta(days=1), uf="RJ", evolucao=1, hospital=1, uti=2)

    # --- Uma das estadias em UTI fica incoerente ------------------------------
    # O registro ja existente e corrompido, em vez de um novo ser acrescentado:
    # assim as contagens documentadas acima continuam valendo, e o unico efeito
    # e o esperado -- a estadia sai do censo diario, mas o caso permanece na
    # base, na view analitica e na taxa de admissao em UTI.
    incoerente = rows[100]  # primeiro registro da janela atual, com UTI = 1
    incoerente["DT_SAIDUTI"] = incoerente["DT_ENTUTI"] - pd.Timedelta(days=2)
    incoerente["flag_uti_inconsistente"] = True

    # --- Anos de baseline: mesma janela de calendario, anos anteriores ---------
    # Casos encerrados como cura e digitados 10 dias depois dos sintomas, para
    # nao interferir em nenhuma janela recente (mortalidade, UTI, series).
    for year, total in SYNTHETIC_BASELINE_CASES.items():
        delta = year - cutoff.year
        for index in range(total):
            day = (current_start + timedelta(days=index % 30)).replace(
                year=current_start.year + delta
            )
            add(day, evolucao=1, hospital=2, digitacao=day + timedelta(days=10))

    frame = pd.DataFrame(rows)

    # As colunas semanticas sao derivadas pela MESMA regra usada em producao,
    # em vez de reescritas aqui: assim a base sintetica nao pode divergir da
    # definicao real de "obito", "caso encerrado" ou "admissao em UTI".
    context = CleaningContext(
        year=2026, report=QualityReport(), adjustments=AdjustmentLog(frame.index)
    )
    frame = DeriveEpidemiologicalWeek().apply(frame, context)
    return DeriveSemanticFlags().apply(frame, context)


@pytest.fixture(scope="session")
def synthetic_database(configured_environment) -> Path:
    """Constroi o banco analitico sintetico e devolve seu caminho."""
    from src.config import get_settings
    from src.data.load_database import load_database

    settings = get_settings()
    settings.ensure_directories()

    frame = _build_synthetic_frame()
    frame.to_parquet(settings.processed_parquet_path, index=False)
    return load_database()


@pytest.fixture
def connection(synthetic_database):
    """Conexao somente leitura com o banco sintetico."""
    from src.data.load_database import connect

    with connect() as conn:
        yield conn
