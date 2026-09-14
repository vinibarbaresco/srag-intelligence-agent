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

#: Data de digitacao mais recente da base sintetica. Todas as janelas dos testes
#: sao ancoradas nela, exatamente como o sistema faz em producao.
REFERENCE_DATE = date(2026, 8, 23)

#: Corte analitico esperado com REPORTING_LAG_DAYS = 21.
EXPECTED_CUTOFF = REFERENCE_DATE - timedelta(days=21)


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
    monkeypatch_session.setenv("MIN_CELL_SIZE", "5")
    # Credencial vazia, e nao ausente: a variavel de ambiente tem precedencia
    # sobre o arquivo .env no pydantic-settings, entao isto neutraliza uma chave
    # real presente na maquina do desenvolvedor. Sem isso a suite deixaria de ser
    # hermetica e poderia chamar a API da OpenAI durante os testes.
    monkeypatch_session.setenv("OPENAI_API_KEY", "")
    reset_settings_cache()


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
    ) -> None:
        rows.append(
            {
                "DT_NOTIFIC": pd.Timestamp(symptoms),
                "DT_SIN_PRI": pd.Timestamp(symptoms),
                "DT_INTERNA": pd.Timestamp(symptoms),
                "DT_ENTUTI": pd.Timestamp(entrada_uti) if entrada_uti else pd.NaT,
                "DT_SAIDUTI": pd.Timestamp(saida_uti) if saida_uti else pd.NaT,
                "DT_EVOLUCA": pd.Timestamp(evolucao_data) if evolucao_data else pd.NaT,
                "DT_ENCERRA": pd.NaT,
                "DT_DIGITA": pd.Timestamp(digitacao or REFERENCE_DATE),
                "CS_SEXO": "F",
                "CS_RACA": pd.NA,
                "CS_GESTANT": pd.NA,
                "TP_IDADE": 3,
                "FATOR_RISC": pd.NA,
                "HOSPITAL": hospital,
                "UTI": uti,
                "SUPORT_VEN": pd.NA,
                "CLASSI_FIN": classi_fin,
                "CRITERIO": 1,
                "EVOLUCAO": evolucao,
                "VACINA": vacina,
                "VACINA_COV": vacina_cov,
                "SG_UF_NOT": uf,
                "SG_UF": uf,
                "NU_IDADE_N": 45,
                "SEM_PRI": 30,
                "DOSE_1_COV": pd.NaT,
                "DOSE_2_COV": pd.NaT,
                "DOSE_REF": pd.NaT,
                "idade_anos": 45.0,
                "faixa_etaria": "40-49",
                "ano_referencia": symptoms.year,
                # As flags de coerencia sao definidas explicitamente aqui: a base
                # sintetica e construida coerente, e os casos problematicos sao
                # adicionados de proposito no fim de `_build_synthetic_frame`.
                "flag_data_invalida": False,
                "flag_internacao_inconsistente": False,
                "flag_uti_inconsistente": False,
                "flag_evolucao_inconsistente": False,
            }
        )

    # --- Janela anterior: exatamente 100 casos -------------------------------
    for index in range(100):
        add(previous_start + timedelta(days=index % 30))

    # --- Janela atual: exatamente 150 casos ----------------------------------
    for index in range(150):
        day = current_start + timedelta(days=index % 30)

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

    return pd.DataFrame(rows)


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
