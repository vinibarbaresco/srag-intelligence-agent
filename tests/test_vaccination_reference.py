"""Cobertura vacinal populacional: contrato, ingestao do PNI e indisponibilidade.

A cobertura populacional e o segundo indicador que depende de fonte externa. Os
testes abaixo cobrem as tres fronteiras que importam: o contrato do arquivo de
referencia, a agregacao do extrato oficial do PNI (que nunca pode persistir
registro individual) e o calculo com e sem populacao-alvo.
"""

from __future__ import annotations

import io
import json
import pathlib

import duckdb
import pandas as pd
import pytest

from src.config import get_settings, reset_settings_cache
from src.data.reference.tables import TABLE_VACCINATION, load_reference_tables
from src.data.reference.vaccination import (
    VACCINATION_COLUMNS,
    VaccinationReferenceError,
    aggregate_pni_csv,
    build_vaccination_reference,
    classify_campaign,
    read_provenance,
    read_vaccination_reference,
    write_vaccination_reference,
)
from src.metrics.epidemiology import population_vaccination_coverage
from src.metrics.filters import AnalyticFilters

_HEADER = "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url,data_extracao\n"

#: Cabecalho minimo do extrato do PNI, com as quatro colunas lidas. As outras 56
#: colunas do arquivo oficial nao aparecem aqui de proposito: se o agregador
#: passar a depender de alguma delas, este teste falha.
_PNI_HEADER = "sg_uf_paciente;ds_nome;dt_vacina;st_documento\n"


def _pni(rows: str) -> io.StringIO:
    return io.StringIO(_PNI_HEADER + rows)


class TestClassificacaoDeCampanha:
    @pytest.mark.parametrize(
        ("descricao", "esperado"),
        [
            ("VACINA COVID-19 COMIRNATY", "covid19"),
            ("Vacina influenza trivalente", "influenza"),
            ("VACINA GRIPE", "influenza"),
            ("VACINA CORONAVIRUS (RECOMBINANTE)", "covid19"),
            ("VACINA BCG", None),
            ("VACINA HPV QUADRIVALENTE", None),
            ("", None),
            # Vacinas combinadas com o componente Haemophilus influenzae B
            # citam "influenza" no nome da bacteria, nao da vacina de gripe.
            # Nomes reais do catalogo do PNI (extrato de fev/2026) -- inclusive
            # a grafia sem o "e" final em "Hib", que por si so bateria com a
            # palavra-chave "INFLUENZA".
            (
                "vacina adsorvida difteria, tetano, pertussis, hepatite B "
                "(recombinante) e Haemophilus influenzae B (conjugada)",
                None,
            ),  # Penta
            ("vacina Haemophilus influenza B (conjugada)", None),  # Hib
            ("diluente para vacina Haemophilus influenzae B (conjugada)", None),  # DILHib
        ],
    )
    def test_campanhas_sao_distinguidas_e_o_resto_e_descartado(self, descricao, esperado):
        assert classify_campaign(descricao) == esperado

    def test_influenza_e_covid_nunca_sao_somadas(self):
        totals, _ = aggregate_pni_csv(
            _pni(
                "SP;VACINA INFLUENZA;2026-04-10;final\n"
                "SP;VACINA COVID-19 COMIRNATY;2026-04-11;final\n"
            ),
            year=2026,
        )
        assert totals[("SP", "influenza")] == 1
        assert totals[("SP", "covid19")] == 1


class TestAgregacaoDoExtratoPNI:
    def test_documento_substituido_nao_conta_duas_vezes(self):
        """A RNDS mantem a versao antiga ao lado da corrigida."""
        totals, discarded = aggregate_pni_csv(
            _pni("SP;VACINA INFLUENZA;2026-04-10;final\nSP;VACINA INFLUENZA;2026-04-10;replaced\n"),
            year=2026,
        )
        assert totals[("SP", "influenza")] == 1
        assert discarded["documento_nao_final"] == 1

    def test_doses_de_outro_ano_sao_descartadas_e_contadas(self):
        totals, discarded = aggregate_pni_csv(
            _pni("SP;VACINA INFLUENZA;2025-04-10;final\nSP;VACINA INFLUENZA;2026-04-10;final\n"),
            year=2026,
        )
        assert totals[("SP", "influenza")] == 1
        assert discarded["outro_ano"] == 1

    def test_uf_invalida_e_data_ilegivel_sao_contadas_nao_silenciadas(self):
        totals, discarded = aggregate_pni_csv(
            _pni("XX;VACINA INFLUENZA;2026-04-10;final\nSP;VACINA INFLUENZA;nao-e-data;final\n"),
            year=2026,
        )
        assert not totals
        assert discarded["uf_invalida"] == 1
        assert discarded["data_ilegivel"] == 1

    def test_layout_inesperado_e_recusado_com_mensagem_acionavel(self):
        with pytest.raises(VaccinationReferenceError, match="colunas esperadas"):
            aggregate_pni_csv(io.StringIO("coluna_a;coluna_b\n1;2\n"), year=2026)

    def test_agregado_nao_carrega_nenhuma_coluna_de_paciente(self, tmp_path):
        """O extrato bruto tem co_paciente; o agregado nao pode ter nada disso."""
        extract = tmp_path / "vacinacao_abr_2026.csv"
        extract.write_text(
            "co_paciente;sg_uf_paciente;ds_nome;dt_vacina;st_documento\n"
            "abc123;SP;VACINA INFLUENZA;2026-04-10;final\n"
            "def456;RJ;VACINA COVID-19;2026-04-11;final\n",
            encoding="utf-8",
        )
        frame, provenance = build_vaccination_reference([extract], year=2026)

        assert list(frame.columns) == list(VACCINATION_COLUMNS)
        serialized = frame.to_csv(index=False) + json.dumps(provenance)
        assert "abc123" not in serialized
        assert "def456" not in serialized
        assert "co_paciente" not in provenance["colunas_lidas_do_bruto"]
        assert "pseudonimizado" in provenance["nota_de_privacidade"]

    def test_proveniencia_registra_hash_e_descartes_por_arquivo(self, tmp_path):
        extract = tmp_path / "vacinacao_abr_2026.csv"
        extract.write_text(
            _PNI_HEADER + "SP;VACINA INFLUENZA;2026-04-10;final\nSP;VACINA BCG;2026-04-10;final\n",
            encoding="utf-8",
        )
        _, provenance = build_vaccination_reference([extract], year=2026)

        registro = provenance["extratos"][0]
        assert registro["arquivo"] == "vacinacao_abr_2026.csv"
        assert len(registro["sha256"]) == 64
        assert registro["descartes"]["campanha_fora_do_escopo"] == 1
        assert provenance["campanhas_obtidas"] == ["influenza"]

    def test_extrato_sem_dose_das_campanhas_nao_gera_arquivo(self, tmp_path):
        extract = tmp_path / "vacinacao_abr_2026.csv"
        extract.write_text(_PNI_HEADER + "SP;VACINA BCG;2026-04-10;final\n", encoding="utf-8")
        with pytest.raises(VaccinationReferenceError, match="Nenhuma dose"):
            build_vaccination_reference([extract], year=2026)

    def test_gravacao_produz_csv_e_proveniencia_com_sha256(self, tmp_path):
        extract = tmp_path / "vacinacao_abr_2026.csv"
        extract.write_text(_PNI_HEADER + "SP;VACINA INFLUENZA;2026-04-10;final\n", encoding="utf-8")
        frame, provenance = build_vaccination_reference(
            [extract], year=2026, target_population={("SP", "influenza"): 1000}
        )
        target = write_vaccination_reference(frame, provenance, tmp_path / "cobertura.csv")

        assert read_vaccination_reference(target).iloc[0]["populacao_alvo"] == 1000
        record = read_provenance(target)
        assert len(record["sha256"]) == 64
        assert record["populacao_alvo_informada"] is True


class TestContratoDoArquivo:
    def test_campanha_desconhecida_e_rejeitada(self, tmp_path):
        path = tmp_path / "cob.csv"
        path.write_text(_HEADER + "SP,2026,sarampo,10,100,SI-PNI,,2026-09-01\n", encoding="utf-8")
        with pytest.raises(VaccinationReferenceError, match="Campanhas desconhecidas"):
            read_vaccination_reference(path)

    def test_populacao_alvo_nula_e_rejeitada(self, tmp_path):
        path = tmp_path / "cob.csv"
        path.write_text(_HEADER + "SP,2026,influenza,10,0,SI-PNI,,2026-09-01\n", encoding="utf-8")
        with pytest.raises(VaccinationReferenceError, match="nao positiva"):
            read_vaccination_reference(path)

    def test_populacao_alvo_vazia_e_legitima(self, tmp_path):
        path = tmp_path / "cob.csv"
        path.write_text(_HEADER + "SP,2026,influenza,10,,SI-PNI,,2026-09-01\n", encoding="utf-8")
        frame = read_vaccination_reference(path)
        assert pd.isna(frame.iloc[0]["populacao_alvo"])

    def test_referencia_sem_data_de_extracao_continua_valida(self, tmp_path):
        """Arquivos preenchidos a mao antes do contrato atual nao quebram."""
        path = tmp_path / "cob.csv"
        path.write_text(
            "uf,ano,campanha,doses_aplicadas,populacao_alvo,fonte,url\n"
            "SP,2026,influenza,10,100,SI-PNI,https://exemplo\n",
            encoding="utf-8",
        )
        frame = read_vaccination_reference(path)
        assert frame.iloc[0]["data_extracao"] is None

    def test_arquivo_ausente_explica_como_gerar(self, tmp_path):
        with pytest.raises(VaccinationReferenceError, match="--from-pni"):
            read_vaccination_reference(tmp_path / "inexistente.csv")


class TestCalculoDaCobertura:
    def _connection(self, tmp_path, monkeypatch, body: str):
        path = tmp_path / "cobertura_vacinal_uf.csv"
        path.write_text(_HEADER + body, encoding="utf-8")
        monkeypatch.setenv("VACCINATION_REFERENCE_PATH", str(path))
        reset_settings_cache()
        connection = duckdb.connect(str(tmp_path / "ref.duckdb"))
        load_reference_tables(connection)
        return connection

    def test_cobertura_usa_populacao_alvo_quando_publicada(self, tmp_path, monkeypatch):
        connection = self._connection(
            tmp_path,
            monkeypatch,
            "SP,2026,influenza,4000000,8000000,SI-PNI,https://exemplo,2026-09-01\n"
            "RJ,2026,influenza,1000000,4000000,SI-PNI,https://exemplo,2026-09-01\n",
        )
        try:
            national = population_vaccination_coverage(connection, AnalyticFilters(), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        influenza = national["campanhas"]["influenza"]
        assert influenza["value"] == pytest.approx(41.67)  # 5 mi / 12 mi
        assert influenza["denominador_fonte"] == "SI-PNI"
        assert influenza["data_extracao"] == "2026-09-01"
        assert national["formula"] == "doses_aplicadas / populacao_alvo * 100"

    def test_sem_populacao_alvo_o_ibge_entra_declarado_como_substituto(self, tmp_path, monkeypatch):
        connection = self._connection(
            tmp_path, monkeypatch, "SP,2026,covid19,2000000,,SI-PNI,https://exemplo,2026-09-01\n"
        )
        try:
            sp = population_vaccination_coverage(connection, AnalyticFilters(uf="SP"), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        covid = sp["campanhas"]["covid19"]
        assert covid["denominator"] == 10_000_000  # populacao sintetica de SP
        assert covid["value"] == pytest.approx(20.0)
        assert covid["denominador_fonte"] == "IBGE"
        assert "SUBESTIMA" in covid["justificativa_do_denominador"]

    def test_campanhas_permanecem_separadas_no_resultado(self, tmp_path, monkeypatch):
        connection = self._connection(
            tmp_path,
            monkeypatch,
            "SP,2026,influenza,1000,10000,SI-PNI,https://exemplo,2026-09-01\n"
            "SP,2026,covid19,5000,10000,SI-PNI,https://exemplo,2026-09-01\n",
        )
        try:
            sp = population_vaccination_coverage(connection, AnalyticFilters(uf="SP"), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert sorted(sp["campanhas"]) == ["covid19", "influenza"]
        assert sp["campanhas"]["influenza"]["value"] == pytest.approx(10.0)
        assert sp["campanhas"]["covid19"]["value"] == pytest.approx(50.0)
        assert sp["campanhas_distintas"] == ["covid19", "influenza"]

    def test_uf_fora_da_referencia_fica_indisponivel(self, tmp_path, monkeypatch):
        connection = self._connection(
            tmp_path,
            monkeypatch,
            "SP,2026,influenza,1000,10000,SI-PNI,https://exemplo,2026-09-01\n",
        )
        try:
            ac = population_vaccination_coverage(connection, AnalyticFilters(uf="AC"), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert ac["value"] is None
        assert "AC" in ac["unavailable_reason"]

    def test_sem_referencia_o_indicador_permanece_nao_calculavel(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VACCINATION_REFERENCE_PATH", str(tmp_path / "inexistente.csv"))
        reset_settings_cache()
        connection = duckdb.connect(str(tmp_path / "ref.duckdb"))
        try:
            load_reference_tables(connection)
            rows = connection.execute(f"SELECT count(*) FROM {TABLE_VACCINATION}").fetchone()[0]
            result = population_vaccination_coverage(connection, AnalyticFilters(), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        assert rows == 0
        assert result["value"] is None
        assert "SI-PNI" in result["unavailable_reason"]
        # A recusa tem de ensinar o caminho, nao so negar.
        assert "--from-pni" in result["unavailable_reason"]

    def test_cobertura_entre_casos_nunca_e_usada_como_proxy(self, synthetic_database):
        """O indicador populacional nao pode herdar o valor do indicador de casos."""
        from src.tools.registry import call_tool

        result = call_tool("get_vaccination_metrics", {})
        population = result["components"]["taxa_de_vacinacao_da_populacao"]
        assert population["value"] is None
        assert result["value"] is not None  # a cobertura ENTRE CASOS existe
        assert population["value"] != result["value"]

    def test_referencia_versionada_no_repositorio_e_valida_se_existir(self):
        settings = get_settings()
        path = settings.reference_dir / "cobertura_vacinal_uf.csv"
        if not path.exists():
            pytest.skip("referencia do SI-PNI nao fornecida neste checkout")
        frame = read_vaccination_reference(path)
        assert set(frame["campanha"]) <= {"influenza", "covid19"}


class TestFixtureDeDemonstracao:
    """A fixture de demonstracao nunca pode ser confundida com dado oficial.

    Ela existe para permitir demonstrar o CALCULO da cobertura populacional sem
    esperar a agregacao de alguns GB de extratos do SI-PNI (ver
    `src/data/reference/vaccination.py`), e a garantia central e que o rotulo
    de teste sobrevive ate o numero publicado no relatorio -- nunca so no nome
    do arquivo.
    """

    FIXTURE_PATH = (
        pathlib.Path(__file__).resolve().parent.parent
        / "data"
        / "reference"
        / "cobertura_vacinal_uf.FIXTURE_EXEMPLO.csv"
    )

    def test_fixture_existe_e_e_valida_pelo_mesmo_contrato(self):
        if not self.FIXTURE_PATH.exists():
            pytest.skip("fixture de demonstracao nao presente neste checkout")
        frame = read_vaccination_reference(self.FIXTURE_PATH)
        assert not frame.empty

    def test_toda_linha_da_fixture_se_autodeclara_teste(self):
        """Se alguem apontar VACCINATION_REFERENCE_PATH para ela, o numero
        publicado ainda vai carregar "FIXTURE" -- a mistura com dado oficial
        fica impossivel de passar despercebida.
        """
        if not self.FIXTURE_PATH.exists():
            pytest.skip("fixture de demonstracao nao presente neste checkout")
        frame = read_vaccination_reference(self.FIXTURE_PATH)
        assert (frame["fonte"].str.contains("FIXTURE", case=False)).all()
        assert (frame["fonte"].str.contains("NAO E DADO OFICIAL", case=False)).all()

    def test_fixture_nao_e_o_caminho_padrao(self):
        """O default de producao continua honesto: sem referencia real, o
        indicador populacional fica indisponivel -- a fixture e sempre opt-in.
        """
        assert get_settings().vaccination_reference_file != self.FIXTURE_PATH

    def test_rotulo_de_teste_chega_ao_resultado_calculado(self, tmp_path, monkeypatch):
        """Fim a fim: carregar a fixture faz o proprio numero calculado levar
        o aviso -- nao so o arquivo de origem.
        """
        if not self.FIXTURE_PATH.exists():
            pytest.skip("fixture de demonstracao nao presente neste checkout")

        monkeypatch.setenv("VACCINATION_REFERENCE_PATH", str(self.FIXTURE_PATH))
        reset_settings_cache()
        connection = duckdb.connect(str(tmp_path / "ref.duckdb"))
        try:
            load_reference_tables(connection)
            resultado = population_vaccination_coverage(connection, AnalyticFilters(uf="SP"), 2026)
        finally:
            connection.close()
            monkeypatch.undo()
            reset_settings_cache()

        influenza = resultado["campanhas"]["influenza"]
        assert influenza["value"] is not None
        assert "FIXTURE" in influenza["fonte"]
        assert "NAO E DADO OFICIAL" in influenza["fonte"]
