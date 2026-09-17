"""Ocupacao de leitos de UTI: numerador, denominador e indisponibilidades.

A ocupacao e o unico indicador do sistema cujo denominador vem de uma fonte
externa ao SIVEP-Gripe (capacidade instalada do CNES). Isso cria quatro formas
de o indicador *nao* poder ser calculado -- referencia ausente, UF sem
capacidade, competencia incompativel e denominador zero -- e cada uma delas
precisa produzir indisponibilidade explicita, nunca um numero aproximado.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from src.config import get_settings, reset_settings_cache
from src.data.reference.icu_capacity import (
    ICU_BED_TYPES_FOR_SRAG,
    ICUCapacityReferenceError,
    aggregate_cnes_beds,
    read_icu_capacity_reference,
)
from src.data.reference.tables import TABLE_ICU_CAPACITY, load_reference_tables
from src.metrics.epidemiology import icu_bed_occupancy_rate, icu_patient_census, icu_stay_cap
from src.metrics.filters import AnalyticFilters
from src.tools.registry import call_tool
from tests.conftest import SYNTHETIC_ICU_BEDS_PER_UF, SYNTHETIC_ICU_COMPETENCE

SP = AnalyticFilters(uf="SP")

_HEADER = "uf,competencia,tipo_leito,leitos_existentes,leitos_sus,fonte,url,data_extracao\n"


def _capacity_csv(path, rows: str):
    path.write_text(_HEADER + rows, encoding="utf-8")
    return path


def _connection_with(tmp_path, monkeypatch, csv_body: str | None, *, synthetic_database=None):
    """Copia do banco sintetico com OUTRA referencia de capacidade carregada.

    A ocupacao cruza duas fontes, entao o banco de teste precisa das duas: a
    view analitica de casos (que vem do banco sintetico) e a tabela de
    capacidade (que cada cenario substitui). Trabalhar sobre uma copia mantem o
    banco da suite intacto.
    """
    import shutil

    if csv_body is None:
        monkeypatch.setenv("ICU_CAPACITY_REFERENCE_PATH", str(tmp_path / "inexistente.csv"))
    else:
        monkeypatch.setenv(
            "ICU_CAPACITY_REFERENCE_PATH",
            str(_capacity_csv(tmp_path / "leitos.csv", csv_body)),
        )
    reset_settings_cache()

    target = tmp_path / "srag.duckdb"
    source = get_settings().database_path
    if synthetic_database is not None or source.exists():
        shutil.copy(source, target)
    connection = duckdb.connect(str(target))
    load_reference_tables(connection)
    return connection


class TestContratoDaReferencia:
    def test_extrato_do_cnes_e_agregado_por_uf_competencia_e_tipo(self):
        import io

        raw = (
            '"COMP";"UF";"UTI_ADULTO_EXIST";"UTI_ADULTO_SUS";"UTI_PEDIATRICO_EXIST";'
            '"UTI_PEDIATRICO_SUS";"UTI_NEONATAL_EXIST";"UTI_NEONATAL_SUS";'
            '"UTI_QUEIMADO_EXIST";"UTI_QUEIMADO_SUS";"UTI_CORONARIANA_EXIST";'
            '"UTI_CORONARIANA_SUS"\n'
            '"202606";"SP";"10";"5";"0";"0";"0";"0";"0";"0";"0";"0"\n'
            '"202606";"SP";"7";"2";"3";"1";"0";"0";"0";"0";"0";"0"\n'
            '"202606";"RJ";"4";"4";"0";"0";"0";"0";"0";"0";"0";"0"\n'
        )
        frame = aggregate_cnes_beds(io.StringIO(raw), url="u", extracted_at="2026-09-01")

        adulto_sp = frame.query("uf == 'SP' and tipo_leito == 'UTI_ADULTO'").iloc[0]
        assert adulto_sp["leitos_existentes"] == 17  # dois estabelecimentos somados
        assert adulto_sp["leitos_sus"] == 7
        assert adulto_sp["competencia"] == "2026-06"
        assert set(frame["uf"]) == {"SP", "RJ"}
        # UTI_TOTAL nao entra: no arquivo do CNES ele e a soma das partes.
        assert "UTI_TOTAL" not in set(frame["tipo_leito"])

    def test_leitos_sus_acima_do_total_sao_rejeitados(self, tmp_path):
        path = _capacity_csv(
            tmp_path / "leitos.csv",
            f"SP,{SYNTHETIC_ICU_COMPETENCE},UTI_ADULTO,10,20,CNES,u,2026-08-01\n",
        )
        with pytest.raises(ICUCapacityReferenceError, match="leitos SUS"):
            read_icu_capacity_reference(path)

    def test_tipo_de_leito_fora_do_dominio_e_rejeitado(self, tmp_path):
        path = _capacity_csv(
            tmp_path / "leitos.csv",
            f"SP,{SYNTHETIC_ICU_COMPETENCE},UTI_MARCIANA,10,5,CNES,u,2026-08-01\n",
        )
        with pytest.raises(ICUCapacityReferenceError, match="Tipos de leito desconhecidos"):
            read_icu_capacity_reference(path)

    def test_arquivo_ausente_explica_como_gerar(self, tmp_path):
        with pytest.raises(ICUCapacityReferenceError, match="src.data.reference.icu_capacity"):
            read_icu_capacity_reference(tmp_path / "inexistente.csv")

    def test_referencia_versionada_no_repositorio_e_valida(self):
        from src.config import PROJECT_ROOT

        path = PROJECT_ROOT / "data" / "reference" / "leitos_uti_uf.csv"
        if not path.exists():
            pytest.skip("referencia do CNES nao presente neste checkout")
        frame = read_icu_capacity_reference(path)
        assert set(frame["uf"]).__len__() == 27
        srag = frame[frame["tipo_leito"].isin(ICU_BED_TYPES_FOR_SRAG)]
        assert srag["leitos_existentes"].sum() > 0


class TestCalculo:
    def test_numerador_e_denominador_seguem_a_formula(self, connection):
        result = icu_bed_occupancy_rate(connection, SP)
        census = icu_patient_census(connection, SP, window_days=60)

        # O denominador e a soma dos tipos de leito atendiveis, so da UF do recorte.
        assert result.denominator == SYNTHETIC_ICU_BEDS_PER_UF
        # O numerador e o censo do dia publicado.
        day = result.components["data_do_valor_publicado"]
        observed = next(point for point in census if point["data"] == day)
        assert result.numerator == observed["pacientes_em_uti"]
        assert result.value == pytest.approx(round(result.numerator / result.denominator * 100, 2))
        # A janela madura preserva o tamanho da janela de analise; ela e
        # deslocada, nao truncada.
        assert result.components["dias_apurados"] == 30

    def test_janela_madura_e_deslocada_pelo_teto_de_permanencia(self, connection):
        """Truncar a janela deixaria a ocupacao permanentemente indisponivel."""
        result = icu_bed_occupancy_rate(connection, SP)
        cap = icu_stay_cap(connection, SP)["dias"]
        janela = result.components["janela_madura"]

        assert janela["deslocamento_dias"] == cap
        inicio = date.fromisoformat(janela["inicio"])
        fim = date.fromisoformat(janela["fim"])
        assert (fim - inicio).days == 29  # mesmo tamanho da janela de analise
        # O fim da janela madura fica `cap` dias antes do corte dos demais
        # indicadores, e o resultado diz isso em vez de deixar implicito.
        corte = date.fromisoformat(result.components["data_corte_analitica"])
        assert (corte - fim).days == cap
        assert "NAO coincide" in janela["nota"]

    def test_nacional_soma_as_27_ufs(self, connection):
        national = icu_bed_occupancy_rate(connection, AnalyticFilters())
        assert national.denominator == SYNTHETIC_ICU_BEDS_PER_UF * 27
        assert national.components["capacidade_instalada"]["ufs_cobertas"] == 27

    def test_leitos_neonatais_ficam_fora_do_denominador(self, connection):
        """A capacidade sintetica tem UTI neonatal; ela nao pode entrar."""
        result = icu_bed_occupancy_rate(connection, SP)
        assert result.components["capacidade_instalada"]["tipos_de_leito"] == list(
            ICU_BED_TYPES_FOR_SRAG
        )
        assert "UTI_NEONATAL" not in result.components["capacidade_instalada"]["tipos_de_leito"]

    def test_limitacao_de_alcance_acompanha_o_valor(self, connection):
        result = icu_bed_occupancy_rate(connection, SP)
        assert "piso" in result.components["alcance_do_indicador"]
        assert any("PISO da ocupacao total" in item for item in result.definition.limitations)

    def test_serie_diaria_publica_ocupacao_por_dia(self, connection):
        result = icu_bed_occupancy_rate(connection, SP)
        series = result.components["serie_diaria_de_ocupacao"]
        assert len(series) == result.components["dias_apurados"]
        for point in series:
            assert point["ocupacao_pct"] == pytest.approx(
                round(point["pacientes_em_uti"] / result.denominator * 100, 2)
            )


class TestIndisponibilidades:
    def test_sem_referencia_de_capacidade(self, tmp_path, monkeypatch, synthetic_database):
        monkeypatch.setenv("ICU_CAPACITY_REFERENCE_PATH", str(tmp_path / "inexistente.csv"))
        reset_settings_cache()
        try:
            from src.data.load_database import load_database

            load_database()
            result = call_tool("get_icu_bed_occupancy", {"uf": "SP"})
        finally:
            monkeypatch.undo()
            reset_settings_cache()
            from src.data.load_database import load_database

            load_database()

        assert result["value"] is None
        assert "nao carregada" in result["unavailable_reason"]
        assert "NAO sao ocupacao" in result["unavailable_reason"]

    def test_uf_sem_capacidade_cadastrada(self, tmp_path, monkeypatch, synthetic_database):
        connection = _connection_with(
            tmp_path,
            monkeypatch,
            f"SP,{SYNTHETIC_ICU_COMPETENCE},UTI_ADULTO,100,50,CNES,u,2026-08-01\n",
        )
        try:
            result = icu_bed_occupancy_rate(connection, AnalyticFilters(uf="RJ"))
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert result.value is None
        assert "RJ" in result.unavailable_reason

    def test_cobertura_parcial_nao_vira_denominador_nacional(
        self, tmp_path, monkeypatch, synthetic_database
    ):
        """Denominador de duas UFs sobre numerador nacional inflaria a ocupacao."""
        connection = _connection_with(
            tmp_path,
            monkeypatch,
            f"SP,{SYNTHETIC_ICU_COMPETENCE},UTI_ADULTO,100,50,CNES,u,2026-08-01\n"
            f"RJ,{SYNTHETIC_ICU_COMPETENCE},UTI_ADULTO,50,25,CNES,u,2026-08-01\n",
        )
        try:
            result = icu_bed_occupancy_rate(connection, AnalyticFilters())
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert result.value is None
        assert "Cobertura geografica insuficiente" in result.unavailable_reason
        assert "superestimada" in result.unavailable_reason

    def test_competencia_distante_demais_da_janela(self, tmp_path, monkeypatch, synthetic_database):
        connection = _connection_with(
            tmp_path,
            monkeypatch,
            "SP,2019-01,UTI_ADULTO,100,50,CNES,u,2019-02-01\n",
        )
        try:
            result = icu_bed_occupancy_rate(connection, SP)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert result.value is None
        assert "Periodo incompativel" in result.unavailable_reason
        assert "2019-01" in result.unavailable_reason

    def test_zero_leitos_nao_vira_divisao_por_zero(self, tmp_path, monkeypatch, synthetic_database):
        connection = _connection_with(
            tmp_path,
            monkeypatch,
            f"SP,{SYNTHETIC_ICU_COMPETENCE},UTI_ADULTO,0,0,CNES,u,2026-08-01\n",
        )
        try:
            result = icu_bed_occupancy_rate(connection, SP)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert result.value is None
        assert "0 leito" in result.unavailable_reason

    def test_tolerancia_temporal_e_configuravel(self, tmp_path, monkeypatch, synthetic_database):
        monkeypatch.setenv("ICU_CAPACITY_MAX_LAG_MONTHS", "60")
        connection = _connection_with(
            tmp_path,
            monkeypatch,
            "SP,2022-08,UTI_ADULTO,100,50,CNES,u,2022-09-01\n",
        )
        try:
            result = icu_bed_occupancy_rate(connection, SP)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        # Com a tolerancia aberta o indicador sai, e a defasagem fica declarada
        # ao lado do valor -- o leitor ve que o denominador e de outro periodo.
        assert result.value is not None
        assert result.components["capacidade_instalada"]["defasagem_meses"] > 12
        assert result.components["capacidade_instalada"]["competencia"] == "2022-08"


class TestCargaNoBanco:
    def test_tabela_de_capacidade_e_populada(self, tmp_path, monkeypatch, synthetic_database):
        connection = _connection_with(
            tmp_path,
            monkeypatch,
            f"SP,{SYNTHETIC_ICU_COMPETENCE},UTI_ADULTO,100,50,CNES,u,2026-08-01\n",
        )
        try:
            rows = connection.execute(f"SELECT count(*) FROM {TABLE_ICU_CAPACITY}").fetchone()[0]
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()
        assert rows == 1

    def test_referencia_ausente_deixa_tabela_vazia_sem_interromper(
        self, tmp_path, monkeypatch, synthetic_database
    ):
        connection = _connection_with(tmp_path, monkeypatch, None)
        try:
            rows = connection.execute(f"SELECT count(*) FROM {TABLE_ICU_CAPACITY}").fetchone()[0]
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()
        assert rows == 0

    def test_proveniencia_das_referencias_e_declarada(self):
        from src.data.reference.tables import reference_provenance

        provenance = reference_provenance()
        assert set(provenance) == {"populacao_uf", "cobertura_vacinal_uf", "leitos_uti_uf"}
        for entry in provenance.values():
            assert "disponivel" in entry


class TestToolDeOcupacao:
    def test_tool_publica_a_formula_e_a_proveniencia(self, synthetic_database):
        result = call_tool("get_icu_bed_occupancy", {"uf": "SP"})
        assert result["metric"] == "icu_bed_occupancy_rate"
        assert result["components"]["formula"].startswith("pacientes_srag_em_uti_no_dia")
        capacity = result["components"]["capacidade_instalada"]
        assert capacity["competencia"] == SYNTHETIC_ICU_COMPETENCE
        assert capacity["fonte"]
        assert capacity["url"]

    def test_admissao_censo_e_ocupacao_continuam_distintos(self, synthetic_database):
        admission = call_tool("get_icu_metrics", {"uf": "SP"})
        occupancy = call_tool("get_icu_bed_occupancy", {"uf": "SP"})
        assert admission["metric"] == "icu_admission_rate"
        assert occupancy["metric"] == "icu_bed_occupancy_rate"
        assert admission["value"] != occupancy["value"]
        assert admission["components"]["censo_diario_pico_pacientes_em_uti"] > 0
