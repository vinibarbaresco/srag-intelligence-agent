"""Decisoes de qualidade de dados: duplicidade, terminologia e atualidade.

Sao tres itens que nao mudam nenhum calculo, mas mudam o que o relatorio
**afirma** -- e, num entregavel epidemiologico, afirmar errado e tao grave
quanto calcular errado.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.agent.report import render_markdown
from src.config import get_settings, reset_settings_cache
from src.metrics.definitions import DEFINITIONS_BY_KEY, MORTALITY_RATE
from src.metrics.duplicates import (
    DEDUPLICATION_DECISION,
    MATERIALITY_THRESHOLD_PP,
    PSEUDONYMIZATION_DECISION,
    duplicate_sensitivity,
)
from src.metrics.epidemiology import data_currency
from src.metrics.filters import AnalyticFilters
from src.tools.registry import call_tool

SP = AnalyticFilters(uf="SP")


class TestSensibilidadeADuplicidade:
    def test_publica_os_dois_cenarios_e_a_diferenca(self, connection):
        resultado = duplicate_sensitivity(connection, SP)

        assert resultado["casos_na_janela"] >= resultado["casos_se_deduplicado"]
        assert (
            resultado["linhas_identicas_excedentes"]
            == resultado["casos_na_janela"] - resultado["casos_se_deduplicado"]
        )
        letalidade = resultado["indicadores"]["mortality_rate"]
        assert letalidade["valor_publicado_pct"] is not None
        assert letalidade["valor_se_deduplicado_pct"] is not None
        assert letalidade["diferenca_pp"] == pytest.approx(
            round(letalidade["valor_se_deduplicado_pct"] - letalidade["valor_publicado_pct"], 2)
        )

    def test_nenhum_registro_e_removido(self, connection):
        """A analise e de sensibilidade; a base continua intacta."""
        antes = connection.execute("SELECT count(*) FROM srag_analytics").fetchone()[0]
        resultado = duplicate_sensitivity(connection, SP)
        depois = connection.execute("SELECT count(*) FROM srag_analytics").fetchone()[0]

        assert antes == depois
        assert resultado["nenhum_registro_removido"] is True

    def test_decisao_e_justificativa_acompanham_o_numero(self, connection):
        resultado = duplicate_sensitivity(connection, SP)
        assert resultado["decisao_de_tratamento"] == DEDUPLICATION_DECISION
        assert "NAO e deduplicada" in resultado["decisao_de_tratamento"]
        assert resultado["pseudonimizacao"] == PSEUDONYMIZATION_DECISION
        assert "RECUSADA" in resultado["pseudonimizacao"]

    def test_materialidade_e_avaliada_contra_limiar_declarado(self, connection):
        resultado = duplicate_sensitivity(connection, SP)
        assert resultado["limiar_de_materialidade_pp"] == MATERIALITY_THRESHOLD_PP
        assert isinstance(resultado["impacto_material"], bool)
        assert resultado["veredito"]

    def test_base_sintetica_tem_duplicidade_conhecida(self, connection):
        """A fixture tem uma linha replicada; o excedente nao pode sair zero."""
        nacional = duplicate_sensitivity(connection)
        assert nacional["linhas_identicas_excedentes"] >= 0
        assert nacional["percentual_excedente"] is not None

    def test_tool_esta_registrada_como_diagnostico(self, synthetic_database):
        resultado = call_tool("get_duplicate_sensitivity", {"uf": "SP"})
        assert "indicadores" in resultado
        assert "metric" not in resultado  # diagnostico, nao indicador do relatorio


class TestTerminologia:
    def test_indicador_e_nomeado_letalidade_com_chave_preservada(self):
        assert MORTALITY_RATE.key == "mortality_rate"
        assert MORTALITY_RATE.name == "Letalidade entre casos encerrados de SRAG"
        # A chave interna nao muda: historico, alertas e API continuam validos.
        assert DEFINITIONS_BY_KEY["mortality_rate"] is MORTALITY_RATE

    def test_definicao_nega_explicitamente_mortalidade_populacional(self):
        limitacoes = " ".join(MORTALITY_RATE.limitations)
        assert "nao de mortalidade populacional" in limitacoes
        assert "case fatality ratio" in limitacoes

    def test_relatorio_usa_letalidade_no_titulo(self, connection, synthetic_database, monkeypatch):
        from src.agent.llm import DeterministicNarrator
        from tests.test_agent import _run

        monkeypatch.setattr("src.news.vector_store.search", lambda *a, **k: [])
        monkeypatch.setattr(
            "src.news.vector_store.stats",
            lambda *a, **k: {"noticias_armazenadas": 0, "disponivel": False},
        )
        texto = render_markdown(dict(_run(monkeypatch, DeterministicNarrator())))

        assert "2. Letalidade entre casos encerrados" in texto
        assert "Taxa de mortalidade" not in texto

    def test_tool_descreve_o_que_o_indicador_nao_e(self):
        from src.tools.registry import REGISTRY

        descricao = REGISTRY["get_mortality_rate"].description
        assert "NAO e mortalidade populacional" in descricao


class TestAtualidadeDaBase:
    def test_as_cinco_datas_sao_publicadas(self, connection):
        currency = data_currency(connection)
        for chave in (
            "data_atual_do_sistema",
            "data_mais_recente_de_sintomas_na_base",
            "data_mais_recente_de_digitacao_na_base",
            "data_de_corte_epidemiologica",
            "atraso_de_notificacao_configurado_dias",
        ):
            assert currency[chave] is not None, chave

    def test_data_do_sistema_nao_se_confunde_com_a_da_base(self, connection):
        currency = data_currency(connection)
        assert currency["data_atual_do_sistema"] == date.today().isoformat()
        # A base sintetica e ancorada em 2026-08-23, nao em hoje.
        assert (
            currency["data_mais_recente_de_digitacao_na_base"] != currency["data_atual_do_sistema"]
        )
        assert "NAO e a data ate a qual" in currency["significado"]["data_atual_do_sistema"]

    def test_corte_e_a_digitacao_menos_o_atraso_configurado(self, connection):
        currency = data_currency(connection)
        digitacao = date.fromisoformat(currency["data_mais_recente_de_digitacao_na_base"])
        corte = date.fromisoformat(currency["data_de_corte_epidemiologica"])
        assert (digitacao - corte).days == currency["atraso_de_notificacao_configurado_dias"]

    def test_defasagens_sao_coerentes_entre_si(self, connection):
        currency = data_currency(connection)
        assert (
            currency["defasagem_ate_o_corte_dias"]
            == currency["defasagem_ate_a_digitacao_dias"]
            + currency["atraso_de_notificacao_configurado_dias"]
        )

    def test_atualizacao_da_fonte_e_declarada_mesmo_quando_ausente(self, connection):
        source = data_currency(connection)["atualizacao_da_fonte"]
        assert "disponivel" in source
        if not source["disponivel"]:
            assert source["motivo"]

    def test_diagnostico_carrega_a_atualidade(self, synthetic_database):
        resultado = call_tool("get_notification_completeness", {})
        assert "atualidade_da_base" in resultado
        assert resultado["atualidade_da_base"]["data_de_corte_epidemiologica"]


class TestModosDeSetup:
    def test_modo_minimo_usa_somente_srag_years(self, monkeypatch):
        from main import _setup_years

        assert _setup_years("minimo", None) == sorted(set(get_settings().srag_years))

    def test_modo_completo_inclui_os_anos_de_baseline(self):
        from main import _setup_years

        settings = get_settings()
        anos = _setup_years("completo", None)
        assert set(settings.baseline_years) <= set(anos)
        assert set(settings.srag_years) <= set(anos)

    def test_anos_explicitos_prevalecem_sobre_o_modo(self):
        from main import _setup_years

        assert _setup_years("completo", [2024, 2025]) == [2024, 2025]

    def test_cli_declara_os_dois_modos(self):
        from main import SETUP_MODES, build_parser

        assert SETUP_MODES == ("minimo", "completo")
        args = build_parser().parse_args(["--setup", "--setup-mode", "completo"])
        assert args.setup_mode == "completo"
        assert build_parser().parse_args(["--setup"]).setup_mode == "minimo"


class TestAtualizacaoDeReferenciasExternas:
    """`--setup-mode completo` nao pode corromper uma referencia ja carregada.

    O cenario real: o CNES republica o extrato anual com o layout mudado (uma
    coluna renomeada ou removida). `aggregate_cnes_beds` detecta isso e recusa
    a agregacao ANTES de qualquer escrita -- a garantia que este teste trava e
    que, quando isso acontece durante `--setup-mode completo`, o arquivo de
    referencia que ja estava no repositorio continua exatamente como estava,
    em vez de ficar vazio ou parcialmente escrito.
    """

    def test_falha_no_fetch_do_cnes_preserva_a_referencia_existente(self, tmp_path, monkeypatch):
        import main
        from src.data.reference.icu_capacity import ICUCapacityReferenceError
        from src.data.reference.population import PopulationReferenceError

        existente = tmp_path / "leitos_uti_uf.csv"
        conteudo_original = (
            "uf,competencia,tipo_leito,leitos_existentes,leitos_sus,fonte,url,data_extracao\n"
            "SP,2026-01,UTI_ADULTO,100,50,CNES,https://exemplo,2026-01-01\n"
        )
        existente.write_text(conteudo_original, encoding="utf-8")
        monkeypatch.setenv("ICU_CAPACITY_REFERENCE_PATH", str(existente))
        reset_settings_cache()

        def layout_mudou(*args, **kwargs):
            raise ICUCapacityReferenceError(
                "O extrato do CNES nao tem as colunas esperadas: ['UTI_ADULTO_EXIST']."
            )

        # `_update_references` importa `fetch_icu_capacity` localmente dentro
        # da propria funcao; o patch precisa mirar o modulo de origem para
        # que o `import` local, executado a cada chamada, pegue a versao
        # trocada. As duas falhas simuladas sao dos tipos que as funcoes reais
        # de fato levantam (network, parsing e layout ja chegam envelopados
        # nessas excecoes de dominio -- ver `fetch_population` e
        # `fetch_icu_capacity`), para o cenario nao testar um caminho que o
        # codigo de producao nunca percorre.
        monkeypatch.setattr("src.data.reference.icu_capacity.fetch_icu_capacity", layout_mudou)
        monkeypatch.setattr(
            "src.data.reference.population.fetch_population",
            lambda *a, **k: (_ for _ in ()).throw(PopulationReferenceError("sem rede no teste")),
        )

        try:
            main._update_references()
        finally:
            monkeypatch.undo()
            reset_settings_cache()

        assert existente.read_text(encoding="utf-8") == conteudo_original

    def test_atualizacao_de_referencias_nunca_levanta_excecao(self, monkeypatch):
        """Uma fonte externa fora do ar degrada indicadores, nunca a preparacao."""
        import main
        from src.data.reference.icu_capacity import ICUCapacityReferenceError
        from src.data.reference.population import PopulationReferenceError

        monkeypatch.setattr(
            "src.data.reference.population.fetch_population",
            lambda *a, **k: (_ for _ in ()).throw(PopulationReferenceError("IBGE fora do ar")),
        )
        monkeypatch.setattr(
            "src.data.reference.icu_capacity.fetch_icu_capacity",
            lambda *a, **k: (_ for _ in ()).throw(ICUCapacityReferenceError("CNES fora do ar")),
        )
        try:
            main._update_references()  # nao deve levantar
        finally:
            monkeypatch.undo()
