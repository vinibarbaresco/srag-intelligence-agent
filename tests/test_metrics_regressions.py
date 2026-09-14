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

    def test_ano_com_casos_so_fora_da_janela_nao_conta_como_presente(self):
        """H-5: o criterio era "tem caso no ano civil", nao "na janela comparada".

        Na base real, 2024 existe apenas com registros de 29 a 31 de dezembro.
        Ainda assim era considerado presente e entrava na mediana do baseline
        com zero casos na janela de maio-junho, puxando-a para zero.
        """
        connection = _connect(
            [
                # 2024 so tem casos em dezembro, fora da janela comparada.
                {"data_sintomas": date(2024, 12, 30), "SG_UF_NOT": "SP"},
                # 2023 tem caso dentro da janela deslocada.
                {"data_sintomas": date(2023, 6, 1), "SG_UF_NOT": "SP"},
            ]
        )
        janela = (date(2025, 5, 20), date(2025, 6, 18))
        assert _years_present(connection, [2023, 2024]) == [2023, 2024]
        assert _years_present(connection, [2023, 2024], window=janela) == [2023]

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
                hospitalizacao_informada BOOLEAN,
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
                "INSERT INTO srag_analytics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    row["data_sintomas"],
                    date(2026, 8, 23),
                    None,
                    None,
                    None,
                    False,
                    row["HOSPITAL"] == 1,
                    row["HOSPITAL"] in (1, 2),
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

        assert result.denominator == 4, "registros com UTI informado ficaram fora do denominador"
        assert result.numerator == 3
        assert result.components["com_uti_informado_e_hospital_ausente"] == 1
        assert result.components["com_uti_informado_e_hospital_ignorado"] == 1

    def test_resgate_e_simetrico_entre_os_dois_bracos(self):
        """M-1: resgatar so quem tem UTI=1 condiciona a entrada ao numerador.

        Seria vies de selecao: entre os registros de `HOSPITAL` desconhecido,
        so os admitidos em UTI entrariam, e todos no numerador -- inflando a
        taxa. O criterio de internacao nao olha para `UTI`.
        """
        onset = date(2026, 7, 20)
        connection = self._connect_uti(
            [
                {"data_sintomas": onset, "HOSPITAL": None, "UTI": 1},
                {"data_sintomas": onset, "HOSPITAL": None, "UTI": 2},
            ]
        )
        result = icu_metrics(connection)
        assert result.denominator == 2, "o braco UTI=2 ficou fora, enquanto UTI=1 entrou"
        assert result.numerator == 1
        assert result.value == 50.0

    def test_internacao_negada_continua_fora(self):
        # HOSPITAL=2 e declaracao explicita de nao internacao: nao e ausencia,
        # e nao deve ser resgatada.
        onset = date(2026, 7, 20)
        connection = self._connect_uti(
            [
                {"data_sintomas": onset, "HOSPITAL": 1, "UTI": 1},
                {"data_sintomas": onset, "HOSPITAL": 2, "UTI": 2},
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


class TestCensuraDeMaturidadeESimetrica:
    """H-2: a condicao de maturidade valia so para a janela anterior.

    Registro sem `DT_DIGITA` entrava no numerador e nunca no denominador.
    Invisivel na safra de referencia (0% de ausencia), mas grave numa carga
    multi-ano: no INFLUD19 a coluna esta 34,37% vazia.
    """

    def test_registro_sem_digitacao_sai_das_duas_janelas(self):
        from src.metrics.epidemiology import case_growth_rate

        cutoff = date(2026, 8, 2)
        current_start = cutoff - timedelta(days=29)
        previous_end = current_start - timedelta(days=1)

        connection = duckdb.connect(":memory:")
        connection.execute(
            "CREATE TABLE srag_analytics ("
            "data_sintomas DATE, data_digitacao DATE, "
            "SG_UF_NOT VARCHAR, CLASSI_FIN SMALLINT)"
        )

        def add(onset: date, typed: date | None) -> None:
            connection.execute("INSERT INTO srag_analytics VALUES (?, ?, 'SP', 5)", [onset, typed])

        # Ancora da data de referencia.
        add(cutoff, date(2026, 8, 23))
        # 20 casos pontuais em cada janela.
        for index in range(20):
            onset = current_start + timedelta(days=index % 30)
            add(onset, onset + timedelta(days=5))
            onset = previous_end - timedelta(days=index % 30)
            add(onset, onset + timedelta(days=5))
        # 20 casos SEM digitacao na janela atual: antes inflavam so o numerador.
        for index in range(20):
            add(current_start + timedelta(days=index % 30), None)

        result = case_growth_rate(connection)

        assert result.components["casos_periodo_atual_sem_censura"] == 41
        assert result.components["casos_periodo_atual"] == 21
        assert result.components["casos_periodo_anterior"] == 20
        # 21 contra 20: so a ancora a mais. Sem a simetria, seriam 41 contra 20.
        assert result.value == 5.0


class TestLetalidadePublicaCoorteMadura:
    """H-3: a letalidade da janela recente e censurada a direita de forma desigual.

    Obito encerra depressa, cura encerra devagar. Duas janelas com percentuais
    de encerramento diferentes nao sao comparaveis, e a variacao entre elas pode
    ser artefato de maturacao. A coorte madura existe para separar as duas
    coisas, e o percentual encerrado precisa estar publicado dos dois lados.
    """

    def test_publica_encerramento_e_coorte_madura(self):
        from src.metrics.epidemiology import mortality_rate

        cutoff = date(2026, 8, 2)
        connection = duckdb.connect(":memory:")
        connection.execute(
            "CREATE TABLE srag_analytics ("
            "data_sintomas DATE, data_digitacao DATE, data_encerramento DATE, "
            "caso_encerrado BOOLEAN, eh_obito_srag BOOLEAN, EVOLUCAO SMALLINT, "
            "SG_UF_NOT VARCHAR, CLASSI_FIN SMALLINT)"
        )

        # O deslocamento da coorte madura e o percentil 90 do tempo ate o
        # encerramento, medido na propria base. Com 45 dias dominando a
        # distribuicao, a janela madura cai em cutoff-74..cutoff-45.
        atraso_ate_encerrar = 45

        def add(onset: date, evolucao: int | None) -> None:
            encerrado = evolucao in (1, 2, 3)
            connection.execute(
                "INSERT INTO srag_analytics VALUES (?,?,?,?,?,?,'SP',5)",
                [
                    onset,
                    date(2026, 8, 23),
                    onset + timedelta(days=atraso_ate_encerrar) if encerrado else None,
                    encerrado,
                    evolucao == 2,
                    evolucao,
                ],
            )

        # Janela recente: a maioria ainda em aberto, e os encerrados sao
        # sobretudo obitos -- o padrao que superestima a letalidade.
        for index in range(40):
            add(cutoff - timedelta(days=index % 30), 2 if index < 10 else None)
        # Coorte deslocada para dentro da janela madura: quase tudo encerrado,
        # com poucas mortes.
        for index in range(40):
            onset = cutoff - timedelta(days=45 + (index % 30))
            add(onset, 2 if index < 4 else 1)

        result = mortality_rate(connection)
        madura = result.components["coorte_madura"]

        assert result.components["percentual_encerrado"] is not None
        assert madura["percentual_encerrado"] > result.components["percentual_encerrado"]
        assert madura["letalidade"] < result.value, (
            "a coorte madura deveria revelar a superestimacao da janela recente"
        )
