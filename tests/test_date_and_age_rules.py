"""Testes das regras de data e de idade nas bordas (D-06, D-17).

Duas regras que a suite exercitava so pelo caminho feliz:

* o **fallback** `%d/%m/%Y` do parse de datas foi medido como codigo morto nas
  tres safras atuais (0 ocorrencias). Codigo morto que ninguem exercita deixa de
  funcionar sem que nada falhe -- ate a safra em que ele volta a ser necessario;
* a **idade**, cuja unica coisa persistida e a faixa etaria, tem regras de
  dominio por unidade (`TP_IDADE`) com uma divergencia deliberada do dicionario
  registrada em D-17. A divergencia so e uma decisao enquanto estiver travada
  nos dois sentidos: o que e anulado e o que **nao** e.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaning import clean_chunk
from src.data.cleaning.dates import parse_dates
from src.data.cleaning.demographics import (
    AGE_UNIT_DOMAIN,
    MAX_PLAUSIBLE_AGE,
    MIN_PLAUSIBLE_AGE,
    age_band,
    age_in_years,
)
from src.data.quality import QualityReport
from src.data.schema import ADJUSTMENT_COLUMN, AGE_BANDS, ALLOWED_COLUMNS


def _raw_chunk(**overrides) -> pd.DataFrame:
    """Bloco bruto minimo e coerente, com as colunas permitidas preenchidas."""
    base = {column: [""] for column in ALLOWED_COLUMNS}
    base.update(
        {
            "DT_SIN_PRI": ["2026-05-08"],
            "DT_DIGITA": ["2026-05-20"],
            "SG_UF_NOT": ["SP"],
            "SG_UF": ["SP"],
            "NU_IDADE_N": ["45"],
            "TP_IDADE": ["3"],
            "EVOLUCAO": ["1"],
            "DT_EVOLUCA": ["2026-05-12"],
            "CLASSI_FIN": ["5"],
            "HOSPITAL": ["2"],
            "UTI": ["2"],
            "VACINA_COV": ["1"],
            "VACINA": ["2"],
            "CS_SEXO": ["F"],
        }
    )
    base.update({key: [value] for key, value in overrides.items()})
    return pd.DataFrame(base, dtype="string")


# =============================================================================
# Parse de datas
# =============================================================================


class TestFallbackBrasileiroDeData:
    """O terceiro formato so existe para safras antigas -- e precisa funcionar."""

    def test_fallback_e_alcancado_quando_o_parse_iso_falha(self):
        """`30/04/2026` nao e ISO-8601: sem o fallback viraria `NaT`."""
        so_iso = pd.to_datetime(
            pd.Series(["30/04/2026"], dtype="string"), format="ISO8601", errors="coerce"
        )
        assert so_iso.isna().all()  # o primeiro passo realmente nao resolve

        assert parse_dates(pd.Series(["30/04/2026"], dtype="string")).iloc[0] == pd.Timestamp(
            "2026-04-30"
        )

    def test_dia_e_mes_nao_sao_trocados_quando_ambos_sao_validos(self):
        """`03/04/2026` e 3 de abril, nunca 4 de marco.

        O formato e fixado em `%d/%m/%Y` justamente para isso: sem ele o pandas
        inferiria a ordem por heuristica, e a mesma coluna poderia ser lida de
        um jeito num arquivo e de outro no seguinte.
        """
        parsed = parse_dates(pd.Series(["03/04/2026"], dtype="string"))
        assert parsed.iloc[0] == pd.Timestamp("2026-04-03")
        assert parsed.iloc[0].month == 4

    def test_coluna_inteira_no_formato_antigo_e_convertida(self):
        """Safra em que nenhum valor e ISO: o fallback e o unico caminho."""
        parsed = parse_dates(pd.Series(["01/01/2019", "31/12/2020", "29/02/2020"], dtype="string"))
        assert list(parsed) == [
            pd.Timestamp("2019-01-01"),
            pd.Timestamp("2020-12-31"),
            pd.Timestamp("2020-02-29"),
        ]

    def test_formatos_misturados_na_mesma_coluna(self):
        """Republicacao parcial pode misturar os formatos; nenhum valor pode se perder."""
        parsed = parse_dates(
            pd.Series(
                ["2026-04-30T00:00:00.000Z", "2024-12-29", "30/04/2026"],
                dtype="string",
            )
        )
        assert list(parsed) == [
            pd.Timestamp("2026-04-30"),
            pd.Timestamp("2024-12-29"),
            pd.Timestamp("2026-04-30"),
        ]

    def test_fallback_atravessa_o_pipeline_sem_marcar_ajuste(self):
        """Formato antigo e dado legivel: nao pode ser contado como data invalida."""
        report = QualityReport()
        frame = clean_chunk(
            _raw_chunk(DT_SIN_PRI="08/05/2026", DT_DIGITA="20/05/2026", DT_EVOLUCA="12/05/2026"),
            2026,
            report,
        )

        assert frame["DT_SIN_PRI"].iloc[0] == pd.Timestamp("2026-05-08")
        assert report.invalid_dates["DT_SIN_PRI"] == 0
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""
        assert bool(frame["flag_data_invalida"].iloc[0]) is False


class TestDataIlegivelVersusCelulaVazia:
    """Valor ilegivel e celula vazia terminam ambos em nulo -- e nao sao a mesma coisa."""

    @pytest.mark.parametrize("valor", ["nao-e-data", "31/02/2026", "2026-13-45", "//"])
    def test_valor_presente_mas_ilegivel_vira_nulo_com_ajuste(self, valor):
        """`31/02/2026` e o caso interessante: bem formatado, mas o dia nao existe."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(DT_EVOLUCA=valor), 2026, report)

        assert pd.isna(frame["DT_EVOLUCA"].iloc[0])
        assert report.invalid_dates["DT_EVOLUCA"] == 1
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "data_ilegivel:DT_EVOLUCA"

    def test_celula_vazia_nao_gera_ajuste_nem_conta_como_data_invalida(self):
        """A razao de existir do ajuste: o pipeline nao alterou nada aqui.

        Sem essa distincao, um campo que a fonte nunca preencheu apareceria no
        relatorio como dado que a ingestao teve de corrigir -- atribuindo ao
        pipeline uma alteracao que ele nao fez.
        """
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(EVOLUCAO="", DT_EVOLUCA=""), 2026, report)

        assert pd.isna(frame["DT_EVOLUCA"].iloc[0])
        assert report.invalid_dates["DT_EVOLUCA"] == 0
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""

    def test_espaco_em_branco_e_tratado_como_vazio(self):
        """`"   "` e ausencia na origem, nao lixo digitado."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(EVOLUCAO="", DT_EVOLUCA="   "), 2026, report)

        assert report.invalid_dates["DT_EVOLUCA"] == 0
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""

    def test_ilegivel_e_vazio_sao_distinguiveis_no_mesmo_relatorio(self):
        """Duas cargas, dois desfechos identicos no dado e distintos no registro."""
        ilegivel = QualityReport()
        clean_chunk(_raw_chunk(DT_EVOLUCA="lixo"), 2026, ilegivel)
        vazio = QualityReport()
        clean_chunk(_raw_chunk(EVOLUCAO="", DT_EVOLUCA=""), 2026, vazio)

        assert ilegivel.invalid_dates["DT_EVOLUCA"] == 1
        assert vazio.invalid_dates["DT_EVOLUCA"] == 0
        assert ilegivel.adjustments["data_ilegivel:DT_EVOLUCA"] == 1
        assert "data_ilegivel:DT_EVOLUCA" not in vazio.adjustments


# =============================================================================
# Idade
# =============================================================================


class TestFaixaEtariaNasBordas:
    """A faixa e a unica coisa persistida: um erro de borda nao e recuperavel depois."""

    @pytest.mark.parametrize(("low", "high", "label"), AGE_BANDS)
    def test_cada_borda_da_faixa_pertence_a_propria_faixa(self, low, high, label):
        """Intervalos fechados nos dois lados: nem o piso nem o teto escapam."""
        # O teto da ultima faixa (200) esta acima do limite de plausibilidade;
        # a borda utilizavel dela e o proprio teto biologico.
        topo = min(high, MAX_PLAUSIBLE_AGE)
        bandas = age_band(pd.Series([float(low), float(topo)], dtype="Float32"))
        assert list(bandas) == [label, label]

    @pytest.mark.parametrize(("low", "high", "label"), AGE_BANDS)
    def test_bordas_atravessam_o_pipeline_completo(self, low, high, label):
        """Em anos inteiros, nenhuma idade plausivel cai entre duas faixas."""
        topo = min(high, MAX_PLAUSIBLE_AGE)
        for idade in (low, topo):
            frame = clean_chunk(
                _raw_chunk(NU_IDADE_N=str(idade), TP_IDADE="3"), 2026, QualityReport()
            )
            assert frame["faixa_etaria"].iloc[0] == label, idade

    def test_faixas_declaradas_nao_deixam_buraco_em_anos_inteiros(self):
        """Uma faixa nova mal encaixada deixaria idades sem classificacao."""
        bandas = age_band(pd.Series([float(a) for a in range(0, MAX_PLAUSIBLE_AGE + 1)]))
        assert bandas.notna().all()


class TestDominioDaUnidadeDeIdade:
    """D-17: piso 0 para meses e uma divergencia declarada -- nos dois sentidos."""

    def test_zero_meses_nao_e_anulado(self):
        """954 lactentes na safra de referencia. 0 meses da 0 anos: a conversao e correta."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N="0", TP_IDADE="2"), 2026, report)

        assert frame["faixa_etaria"].iloc[0] == "0-4"
        assert report.age_unit_out_of_domain == 0
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""

    @pytest.mark.parametrize("meses", ["-9", "-1", "13", "37", "63"])
    def test_meses_fora_do_dominio_sao_anulados(self, meses):
        """Os 5 valores medidos fora do dominio: a unidade nunca e reinterpretada."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N=meses, TP_IDADE="2"), 2026, report)

        assert pd.isna(frame["faixa_etaria"].iloc[0])
        assert report.age_unit_out_of_domain == 1
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "idade_unidade_implausivel"

    def test_piso_de_meses_diverge_do_dicionario_de_proposito(self):
        """O dicionario declara 1 a 11; o piso adotado e 0, e isso esta no codigo."""
        assert AGE_UNIT_DOMAIN[2] == (0, 11)

    @pytest.mark.parametrize(("dias", "dentro"), [("0", True), ("30", True), ("31", False)])
    def test_dominio_de_dias_segue_o_dicionario(self, dias, dentro):
        """1-Dia admite 0 a 30: 31 dias ja deveria ter sido registrado como 1 mes."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N=dias, TP_IDADE="1"), 2026, report)

        assert report.age_unit_out_of_domain == (0 if dentro else 1)
        assert pd.isna(frame["faixa_etaria"].iloc[0]) is not dentro

    def test_anos_nao_tem_dominio_proprio_de_unidade(self):
        """Em anos a plausibilidade biologica ja cobre; nao ha teto duplicado."""
        assert 3 not in AGE_UNIT_DOMAIN

        report = QualityReport()
        clean_chunk(_raw_chunk(NU_IDADE_N="63", TP_IDADE="3"), 2026, report)
        assert report.age_unit_out_of_domain == 0

    def test_63_meses_nao_vira_5_anos(self):
        """A conversao silenciosa produziria 5,25 anos -- plausivel e indetectavel depois."""
        assert age_in_years(pd.Series(["63"]), pd.Series([2])).iloc[0] == pytest.approx(5.25)

        frame = clean_chunk(_raw_chunk(NU_IDADE_N="63", TP_IDADE="2"), 2026, QualityReport())
        # Nao e "5-11" nem faixa nenhuma: o registro sai sem classificacao.
        assert pd.isna(frame["faixa_etaria"].iloc[0])


class TestPlausibilidadeDaIdade:
    @pytest.mark.parametrize("idade", ["-1", "-141"])
    def test_idade_negativa_e_anulada(self, idade):
        """Idade negativa nao cai em faixa nenhuma; anular e registrar e o unico caminho."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N=idade, TP_IDADE="3"), 2026, report)

        assert pd.isna(frame["faixa_etaria"].iloc[0])
        assert report.age_out_of_range == 1
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == "idade_anulada"

    @pytest.mark.parametrize(("idade", "anulada"), [("120", False), ("121", True), ("141", True)])
    def test_teto_biologico_de_120_anos(self, idade, anulada):
        """O dicionario valida ate 150; o teto adotado e 120, e a divergencia e declarada."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N=idade, TP_IDADE="3"), 2026, report)

        assert report.age_out_of_range == (1 if anulada else 0)
        assert pd.isna(frame["faixa_etaria"].iloc[0]) is anulada

    def test_limites_declarados_sao_os_usados(self):
        assert (MIN_PLAUSIBLE_AGE, MAX_PLAUSIBLE_AGE) == (0, 120)

    def test_tp_idade_ausente_nao_vira_idade_em_anos(self):
        """Sem unidade, `45` poderia ser 45 dias, 45 meses ou 45 anos.

        Adivinhar "anos" (o caso mais frequente) transformaria um recem-nascido
        em adulto sem nada falhar. A idade fica nula e a faixa, ausente.
        """
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N="45", TP_IDADE=""), 2026, report)

        assert pd.isna(frame["faixa_etaria"].iloc[0])
        # Nao houve alteracao de valor: nao havia valor derivavel para alterar.
        assert report.age_out_of_range == 0
        assert report.age_unit_out_of_domain == 0

    def test_tp_idade_fora_do_dominio_nao_vira_idade(self):
        """`TP_IDADE = 4` nao existe no dicionario: sem unidade conhecida, sem idade."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(NU_IDADE_N="45", TP_IDADE="4"), 2026, report)

        assert pd.isna(frame["faixa_etaria"].iloc[0])
        assert report.out_of_domain_counts["TP_IDADE"] == 1

    def test_idade_ausente_com_unidade_presente_fica_sem_faixa(self):
        frame = clean_chunk(_raw_chunk(NU_IDADE_N="", TP_IDADE="3"), 2026, QualityReport())
        assert pd.isna(frame["faixa_etaria"].iloc[0])


class TestIdadeExataNaoChegaAoAnalitico:
    """Minimizacao: a idade exata cumpre seu papel e para na camada de tratamento."""

    @pytest.mark.parametrize(("valor", "unidade"), [("45", "3"), ("6", "2"), ("10", "1"), ("", "")])
    def test_colunas_de_idade_exata_sao_descartadas(self, valor, unidade):
        frame = clean_chunk(_raw_chunk(NU_IDADE_N=valor, TP_IDADE=unidade), 2026, QualityReport())
        assert not {"idade_anos", "NU_IDADE_N", "TP_IDADE"} & set(frame.columns)

    def test_nenhuma_coluna_persistida_permite_reconstruir_a_idade(self):
        """Duas idades distintas na mesma faixa produzem registros indistinguiveis."""
        quarenta = clean_chunk(_raw_chunk(NU_IDADE_N="40", TP_IDADE="3"), 2026, QualityReport())
        quarenta_nove = clean_chunk(
            _raw_chunk(NU_IDADE_N="49", TP_IDADE="3"), 2026, QualityReport()
        )

        assert quarenta["faixa_etaria"].iloc[0] == quarenta_nove["faixa_etaria"].iloc[0] == "40-49"
        pd.testing.assert_frame_equal(quarenta, quarenta_nove)

    def test_faixa_etaria_e_a_unica_coluna_demografica_de_idade(self):
        from src.data.schema import ALL_DERIVED_COLUMNS

        derivadas_de_idade = [c for c in ALL_DERIVED_COLUMNS if "idade" in c or "IDADE" in c]
        assert derivadas_de_idade == []
        assert "faixa_etaria" in ALL_DERIVED_COLUMNS
