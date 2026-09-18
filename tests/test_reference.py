"""Referencias externas: populacao (IBGE) e cobertura vacinal (SI-PNI)."""

from __future__ import annotations

import duckdb
import pytest

from src.config import get_settings, reset_settings_cache
from src.data.reference.population import (
    PopulationReferenceError,
    read_population_reference,
)
from src.data.reference.tables import (
    TABLE_POPULATION,
    TABLE_VACCINATION,
    load_reference_tables,
)
from src.data.reference.vaccination import (
    TEMPLATE,
    VaccinationReferenceError,
    read_vaccination_reference,
)
from src.metrics.epidemiology import population_vaccination_coverage
from src.metrics.filters import AnalyticFilters
from src.tools.registry import call_tool


class TestReferenciaPopulacional:
    def test_referencia_sintetica_e_lida_e_validada(self):
        frame = read_population_reference(get_settings().population_reference_file)
        assert len(frame) == 27
        assert int(frame.loc[frame["uf"] == "SP", "populacao"].iloc[0]) == 10_000_000

    def test_uf_faltando_e_rejeitada(self, tmp_path):
        path = tmp_path / "pop.csv"
        path.write_text("uf,ano,populacao\nSP,2026,100\n", encoding="utf-8")
        with pytest.raises(PopulationReferenceError, match="faltam"):
            read_population_reference(path)

    def test_populacao_nao_positiva_e_rejeitada(self, tmp_path):
        source = get_settings().population_reference_file.read_text(encoding="utf-8")
        path = tmp_path / "pop.csv"
        path.write_text(source.replace("SP,2026,10000000", "SP,2026,0"), encoding="utf-8")
        with pytest.raises(PopulationReferenceError, match="nao positiva"):
            read_population_reference(path)

    def test_arquivo_ausente_explica_como_gerar(self, tmp_path):
        with pytest.raises(PopulationReferenceError, match="src.data.reference.population"):
            read_population_reference(tmp_path / "inexistente.csv")

    def test_referencia_versionada_no_repositorio_e_valida(self):
        """O CSV real em data/reference/ precisa passar pelo mesmo validador."""
        from src.config import PROJECT_ROOT

        path = PROJECT_ROOT / "data" / "reference" / "populacao_uf.csv"
        if not path.exists():
            pytest.skip("referencia do IBGE nao presente neste checkout")
        frame = read_population_reference(path)
        assert set(frame["uf"]) == set(read_population_reference().pipe(lambda f: f["uf"]))
        assert (frame["populacao"] > 100_000).all()


class TestReferenciaVacinal:
    def test_template_e_ignorado_como_comentario_e_vazio(self, tmp_path):
        path = tmp_path / "cob.csv"
        path.write_text(TEMPLATE, encoding="utf-8")
        with pytest.raises(VaccinationReferenceError, match="vazia"):
            read_vaccination_reference(path)

    def test_campanha_desconhecida_e_rejeitada(self, tmp_path):
        path = tmp_path / "cob.csv"
        path.write_text(
            "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url\n"
            "SP,2026,sarampo,10,100,SI-PNI,\n",
            encoding="utf-8",
        )
        with pytest.raises(VaccinationReferenceError, match="Campanhas desconhecidas"):
            read_vaccination_reference(path)

    def test_linha_duplicada_e_rejeitada(self, tmp_path):
        path = tmp_path / "cob.csv"
        path.write_text(
            "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url\n"
            "SP,2026,influenza,10,100,SI-PNI,\n"
            "SP,2026,influenza,20,100,SI-PNI,\n",
            encoding="utf-8",
        )
        with pytest.raises(VaccinationReferenceError, match="mais de uma linha"):
            read_vaccination_reference(path)


class TestCargaDasReferencias:
    def test_referencia_ausente_deixa_tabela_vazia_sem_interromper(self, tmp_path):
        connection = duckdb.connect(str(tmp_path / "ref.duckdb"))
        try:
            loaded = load_reference_tables(connection)
            rows = connection.execute(f"SELECT count(*) FROM {TABLE_VACCINATION}").fetchone()[0]
        finally:
            connection.close()
        assert loaded[TABLE_VACCINATION] == 0
        assert rows == 0
        assert loaded[TABLE_POPULATION] == 27

    def test_cobertura_populacional_calculada_a_partir_da_referencia(self, tmp_path, monkeypatch):
        reference = tmp_path / "cobertura_vacinal_uf.csv"
        reference.write_text(
            "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url,data_extracao,"
            "periodo_completo\n"
            "SP,2026,influenza,4000000,8000000,SI-PNI,https://exemplo,,true\n"
            "RJ,2026,influenza,1000000,4000000,SI-PNI,https://exemplo,,true\n"
            "SP,2026,covid19,2000000,,SI-PNI,https://exemplo,,true\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("VACCINATION_REFERENCE_PATH", str(reference))
        reset_settings_cache()
        connection = duckdb.connect(str(tmp_path / "ref.duckdb"))
        try:
            load_reference_tables(connection)
            national = population_vaccination_coverage(connection, AnalyticFilters(), 2026)
            sp = population_vaccination_coverage(connection, AnalyticFilters(uf="SP"), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        # Influenza com populacao-alvo: (4 + 1) mi doses sobre (8 + 4) mi alvo.
        influenza = national["campanhas"]["influenza"]
        assert influenza["value"] == pytest.approx(41.67)
        assert "populacao-alvo" in influenza["denominador_descricao"]
        # Covid sem populacao-alvo: denominador cai para a populacao do IBGE.
        covid = sp["campanhas"]["covid19"]
        assert covid["denominator"] == 10_000_000
        assert covid["value"] == pytest.approx(20.0)
        assert "IBGE" in covid["denominador_descricao"]

    def test_sem_referencia_vacinal_o_indicador_permanece_nao_calculavel(self, synthetic_database):
        result = call_tool("get_vaccination_metrics", {})
        population = result["components"]["taxa_de_vacinacao_da_populacao"]
        assert population["value"] is None
        assert "SI-PNI" in population["unavailable_reason"]


class TestToolsComplementares:
    def test_incidencia_via_tool(self, synthetic_database):
        result = call_tool("get_incidence_rate", {"uf": "SP"})
        assert result["metric"] == "incidence_rate"
        assert result["value"] == pytest.approx(1.5)
        assert result["unit"] == "por 100 mil hab. no periodo"

    def test_baseline_via_tool(self, synthetic_database):
        result = call_tool("get_seasonal_baseline", {})
        assert result["metric"] == "seasonal_excess"
        assert result["value"] == pytest.approx(0.67)
        assert result["components"]["anos_excluidos_por_definicao"]["anos"] == [2020, 2021]
