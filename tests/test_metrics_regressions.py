"""Regressoes dos defeitos epidemiologicos corrigidos na revisao do pipeline.

Cada teste aqui falha na implementacao anterior. Eles usam tabelas minimas
montadas na hora, em vez da base sintetica compartilhada de `conftest.py`,
porque cada defeito exige uma configuracao de dados patologica -- uma digitacao
no futuro, uma estadia sem alta com evolucao distante, um ano sem casos na UF
filtrada -- que nao faz sentido impor a base usada por toda a suite.

A tabela montada se chama `srag_analytics` porque e o nome que as funcoes de
metrica consultam; aqui ela e uma tabela real e nao uma view, o que nao muda
nada para o SQL das metricas.
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from src.metrics.epidemiology import (
    _years_present,
    icu_metrics,
    icu_patient_census,
    icu_stay_completeness,
)
from src.metrics.filters import AnalyticFilters, reference_date


def _connect(rows: list[dict]) -> duckdb.DuckDBPyConnection:
    """Monta `srag_analytics` em memoria com as colunas que as metricas leem."""
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE srag_analytics (
            data_sintomas       DATE,
            data_digitacao      DATE,
            data_entrada_uti    DATE,
            data_saida_uti      DATE,
            data_evolucao       DATE,
            estadia_uti_utilizavel BOOLEAN,
            SG_UF_NOT           VARCHAR,
            CLASSI_FIN          SMALLINT
        )
        """
    )
    for row in rows:
        connection.execute(
            "INSERT INTO srag_analytics VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                row.get("data_sintomas"),
                row.get("data_digitacao"),
                row.get("data_entrada_uti"),
                row.get("data_saida_uti"),
                row.get("data_evolucao"),
                row.get("estadia_uti_utilizavel", False),
                row.get("SG_UF_NOT"),
                row.get("CLASSI_FIN"),
            ],
        )
    return connection


class TestDataDeReferenciaIgnoraDigitacaoFutura:
    """D1: um unico `DT_DIGITA` corrompido deslocava todas as janelas."""

    def test_digitacao_no_futuro_nao_ancora_a_janela(self):
        connection = _connect(
            [
                {"data_sintomas": date(2026, 8, 1), "data_digitacao": date(2026, 8, 20)},
                {"data_sintomas": date(2026, 8, 2), "data_digitacao": date(2026, 8, 23)},
                # Digitacao impossivel: uma ficha nao pode ter sido digitada em 2091.
                {"data_sintomas": date(2026, 8, 3), "data_digitacao": date(2091, 1, 1)},
            ]
        )
        assert reference_date(connection, today=date(2026, 9, 14)) == date(2026, 8, 23)

    def test_sem_digitacao_plausivel_falha_com_causa_explicita(self):
        connection = _connect(
            [{"data_sintomas": date(2026, 8, 1), "data_digitacao": date(2091, 1, 1)}]
        )
        with pytest.raises(ValueError, match="plausivel"):
            reference_date(connection, today=date(2026, 9, 14))


class TestTetoDePermanenciaLimitaTodosOsRamos:
    """D2: o teto so protegia o ramo em aberto; `DT_EVOLUCA` o furava."""

    #: Estadias com alta registrada, curtas, que definem o teto empirico (p95).
    #:
    #: Ficam **fora** da janela do censo (que termina no corte, 21 dias antes da
    #: maior digitacao) de proposito: elas existem para calibrar o teto, e se
    #: aparecessem no censo mascarariam a estadia patologica que o teste mede.
    _COMPLETAS = [
        {
            "data_sintomas": date(2026, 6, 1),
            "data_digitacao": date(2026, 8, 23),
            "data_entrada_uti": date(2026, 6, 1),
            "data_saida_uti": date(2026, 6, 6),
            "estadia_uti_utilizavel": True,
        }
        for _ in range(20)
    ]

    def test_estadia_sem_alta_com_evolucao_distante_e_truncada(self):
        # Entrada muito antes da janela e evolucao so no fim: sem o teto no ramo
        # da evolucao, esta estadia contaria em todos os dias do censo.
        patologica = {
            "data_sintomas": date(2025, 1, 10),
            "data_digitacao": date(2026, 8, 23),
            "data_entrada_uti": date(2025, 1, 10),
            "data_saida_uti": None,
            "data_evolucao": date(2026, 8, 20),
            "estadia_uti_utilizavel": True,
        }
        connection = _connect([*self._COMPLETAS, patologica])

        censo = icu_patient_census(connection)
        pacientes = {ponto["data"]: ponto["pacientes_em_uti"] for ponto in censo}

        # O teto empirico e da ordem de 5 dias; uma estadia iniciada em jan/2025
        # nao pode aparecer no censo de agosto de 2026.
        assert max(pacientes.values()) == 0, (
            "estadia sem alta com DT_EVOLUCA distante escapou do teto de permanencia"
        )

    def test_truncamento_pelo_teto_e_contabilizado_no_ramo_da_evolucao(self):
        patologica = {
            "data_sintomas": date(2026, 7, 1),
            "data_digitacao": date(2026, 8, 23),
            "data_entrada_uti": date(2026, 7, 20),
            "data_saida_uti": None,
            "data_evolucao": date(2026, 7, 21),
            "estadia_uti_utilizavel": True,
        }
        # Evolucao no dia seguinte a entrada: dentro do teto, nao deve truncar.
        connection = _connect([*self._COMPLETAS, patologica])
        completude = icu_stay_completeness(connection)
        assert completude["imputadas_truncadas_pelo_teto"] == 0


class TestAnosDeBaselineRespeitamORecorte:
    """D5: um ano sem casos na UF filtrada contava como presente, com valor 0."""

    def test_ano_sem_casos_na_uf_filtrada_nao_conta_como_presente(self):
        connection = _connect(
            [
                {"data_sintomas": date(2023, 6, 1), "SG_UF_NOT": "SP"},
                {"data_sintomas": date(2024, 6, 1), "SG_UF_NOT": "SP"},
                # 2024 tem caso no AC; 2023 nao tem nenhum.
                {"data_sintomas": date(2024, 6, 2), "SG_UF_NOT": "AC"},
            ]
        )
        assert _years_present(connection, [2023, 2024]) == [2023, 2024]
        assert _years_present(connection, [2023, 2024], AnalyticFilters(uf="AC")) == [2024]


class TestDenominadorDeUtiNaoLeAusenciaComoNao:
    """D3/D4: `HOSPITAL` ausente ou ignorado excluia o registro do denominador.

    Era o unico ponto do pipeline em que missing virava "Nao", contrariando a
    regra declarada em `src/data/schema.py`. Numa base de SRAG **hospitalizada**,
    uma admissao em UTI declarada e evidencia direta de internacao.
    """

    def _connect_uti(self, rows: list[dict]):
        connection = duckdb.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE srag_analytics (
                data_sintomas   DATE,
                data_digitacao  DATE,
                data_entrada_uti DATE,
                data_saida_uti  DATE,
                data_evolucao   DATE,
                estadia_uti_utilizavel BOOLEAN,
                foi_hospitalizado BOOLEAN,
                teve_admissao_uti BOOLEAN,
                uti_informado   BOOLEAN,
                HOSPITAL        SMALLINT,
                UTI             SMALLINT,
                SG_UF_NOT       VARCHAR,
                CLASSI_FIN      SMALLINT
            )
            """
        )
        for row in rows:
            connection.execute(
                "INSERT INTO srag_analytics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    row["data_sintomas"],
                    date(2026, 8, 23),
                    None,
                    None,
                    None,
                    False,
                    row["HOSPITAL"] == 1,
                    row["UTI"] == 1,
                    row["UTI"] in (1, 2),
                    row["HOSPITAL"],
                    row["UTI"],
                    "SP",
                    5,
                ],
            )
        return connection

    def test_admissao_em_uti_com_hospital_ausente_entra_no_denominador(self):
        onset = date(2026, 7, 20)
        connection = self._connect_uti(
            [
                {"data_sintomas": onset, "HOSPITAL": 1, "UTI": 1},
                {"data_sintomas": onset, "HOSPITAL": 1, "UTI": 2},
                # Admissao em UTI declarada, mas HOSPITAL nao preenchido: antes
                # sumia do numerador e do denominador.
                {"data_sintomas": onset, "HOSPITAL": None, "UTI": 1},
                # Idem com HOSPITAL ignorado.
                {"data_sintomas": onset, "HOSPITAL": 9, "UTI": 1},
            ]
        )
        result = icu_metrics(connection)

        assert result.denominator == 4, "registros com UTI=1 ficaram fora do denominador"
        assert result.numerator == 3
        assert result.components["admitidos_em_uti_com_hospital_ausente"] == 1
        assert result.components["admitidos_em_uti_com_hospital_ignorado"] == 1

    def test_registro_sem_uti_e_sem_hospital_continua_fora(self):
        # Sem HOSPITAL='Sim' e sem admissao em UTI, nao ha evidencia de
        # internacao: o registro nao deve ser recuperado para o denominador.
        onset = date(2026, 7, 20)
        connection = self._connect_uti(
            [
                {"data_sintomas": onset, "HOSPITAL": 1, "UTI": 1},
                {"data_sintomas": onset, "HOSPITAL": None, "UTI": 2},
            ]
        )
        result = icu_metrics(connection)
        assert result.denominator == 1


class TestMaturidadeSimetricaEntreJanelas:
    """D6: a janela atual tinha menos tempo de digitacao que a anterior.

    O desconto de `REPORTING_LAG_DAYS` era aplicado uma vez ao fim da serie, nao
    a maturidade de cada janela. A janela atual era observada com menos dias de
    notificacao acumulada que a anterior, e o crescimento saia sistematicamente
    subestimado -- no indicador que dispara o alerta.
    """

    def _connect_janelas(self, rows: list[tuple[date, date]]):
        connection = duckdb.connect(":memory:")
        connection.execute(
            "CREATE TABLE srag_analytics ("
            "data_sintomas DATE, data_digitacao DATE, "
            "SG_UF_NOT VARCHAR, CLASSI_FIN SMALLINT)"
        )
        for onset, typed in rows:
            connection.execute("INSERT INTO srag_analytics VALUES (?, ?, 'SP', 5)", [onset, typed])
        return connection

    def test_chegada_tardia_da_janela_anterior_nao_infla_o_denominador(self):
        from src.metrics.epidemiology import case_growth_rate

        reference = date(2026, 8, 23)
        cutoff = date(2026, 8, 2)  # reference - 21
        current_start = cutoff - timedelta(days=29)
        previous_end = current_start - timedelta(days=1)

        rows: list[tuple[date, date]] = []
        # Janela atual: 40 casos, todos digitados 5 dias apos os sintomas.
        for index in range(40):
            onset = current_start + timedelta(days=index % 30)
            rows.append((onset, onset + timedelta(days=5)))
        # Janela anterior: 40 casos pontuais + 40 que so chegaram muito depois,
        # tarde demais para ja serem conhecidos quando a janela atual fechou.
        for index in range(40):
            onset = previous_end - timedelta(days=index % 30)
            rows.append((onset, onset + timedelta(days=5)))
        for index in range(40):
            onset = previous_end - timedelta(days=index % 30)
            rows.append((onset, reference))

        result = case_growth_rate(self._connect_janelas(rows))

        # Sem a correcao, a janela anterior teria 80 casos contra 40 da atual e o
        # indicador anunciaria -50%: queda inventada pela maturidade desigual.
        assert result.components["casos_periodo_anterior_sem_censura"] == 80
        assert result.components["casos_periodo_anterior"] == 40
        assert result.components["casos_periodo_atual"] == 40
        assert result.value == 0.0, "maturidade desigual ainda contamina a comparacao"
