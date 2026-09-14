"""Bordas da semana epidemiologica do Ministerio da Saude (D-14).

A semana do MS comeca no **domingo**, e a SE 1 e a primeira semana iniciada em
domingo com ao menos 4 dias no ano novo. Nao e a semana ISO. A diferenca so
aparece na virada do ano -- e e exatamente ali que ela muda o numero e, as
vezes, o proprio ano atribuido ao dia.

Os testes existentes fixam datas especificas. O que falta, e o que esta aqui,
sao as propriedades que uma tabela de casos nao alcanca: que a regra vale para
toda virada de ano da serie, que anos de 53 semanas sao tratados sem enumeracao,
e que a reconciliacao com `SEM_PRI` e estritamente uma contagem -- nenhum dos
dois lados corrige ou preenche o outro.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaning import clean_chunk
from src.data.cleaning.epiweek import epidemiological_week
from src.data.quality import QualityReport
from src.data.schema import ADJUSTMENT_COLUMN, ALLOWED_COLUMNS

#: Anos da serie SIVEP-Gripe publicada neste dataset, mais folga adiante.
_ANOS = range(2019, 2031)

#: Anos epidemiologicos de 53 semanas no intervalo acima, calculados pela regra
#: (a SE 1 contem 4 de janeiro): 2020 e 2025 aparecem nas medicoes da fonte.
_ANOS_DE_53_SEMANAS = {2020, 2025}


def _raw_chunk(**overrides) -> pd.DataFrame:
    base = {coluna: [""] for coluna in ALLOWED_COLUMNS}
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
    base.update({chave: [valor] for chave, valor in overrides.items()})
    return pd.DataFrame(base, dtype="string")


def _domingo_anterior(dia: pd.Timestamp) -> pd.Timestamp:
    """Domingo em que comeca a semana que contem `dia` (0 = segunda, 6 = domingo)."""
    return dia - pd.Timedelta(days=(dia.dayofweek + 1) % 7)


def _semanas(datas: list[str]) -> pd.DataFrame:
    return epidemiological_week(pd.Series(pd.to_datetime(datas)))


class TestPropriedadesDaSemanaEpidemiologica:
    """Propriedades sobre a serie inteira, nao sobre datas escolhidas a dedo."""

    @pytest.mark.parametrize("ano", _ANOS)
    def test_todo_dia_do_ano_recebe_uma_semana_entre_1_e_53(self, ano):
        dias = pd.date_range(f"{ano}-01-01", f"{ano}-12-31", freq="D")
        semanas = epidemiological_week(pd.Series(dias))

        assert semanas["semana_epi_num"].notna().all()
        assert semanas["semana_epi_num"].between(1, 53).all()
        assert semanas["semana_epi_ano"].between(ano - 1, ano + 1).all()

    @pytest.mark.parametrize("ano", _ANOS)
    def test_a_semana_sempre_comeca_no_domingo(self, ano):
        """Se a regra virasse ISO, o inicio migraria para a segunda-feira."""
        dias = pd.date_range(f"{ano}-01-01", f"{ano}-12-31", freq="D")
        semanas = epidemiological_week(pd.Series(dias))
        rotulo = (
            semanas["semana_epi_ano"].astype("string")
            + "-"
            + semanas["semana_epi_num"].astype("string").str.zfill(2)
        )

        # O primeiro dia de cada semana epidemiologica presente na serie: todos
        # tem de ser domingo (`dayofweek == 6`), exceto o 1 de janeiro, que pode
        # cair no meio da primeira semana observada.
        primeiros = pd.Series(dias).groupby(rotulo.values).min()
        meio_de_semana = primeiros[primeiros == pd.Timestamp(f"{ano}-01-01")]
        domingos = primeiros.drop(meio_de_semana.index)
        assert (domingos.dt.dayofweek == 6).all()

    @pytest.mark.parametrize("ano", _ANOS)
    def test_a_numeracao_e_contigua_dentro_do_ano_epidemiologico(self, ano):
        """Um buraco na numeracao deslocaria toda a serie a partir dali."""
        dias = pd.date_range(f"{ano - 1}-06-01", f"{ano}-12-31", freq="D")
        semanas = epidemiological_week(pd.Series(dias))
        do_ano = sorted(
            int(n) for n in semanas.loc[semanas["semana_epi_ano"] == ano, "semana_epi_num"].unique()
        )

        assert do_ano[0] == 1
        assert do_ano == list(range(1, do_ano[-1] + 1))

    @pytest.mark.parametrize("ano", _ANOS)
    def test_anos_de_53_semanas_sao_reconhecidos_sem_enumeracao(self, ano):
        """A regra "a SE 1 contem 4 de janeiro" resolve sozinha os anos longos."""
        dias = pd.date_range(f"{ano - 1}-06-01", f"{ano + 1}-06-30", freq="D")
        semanas = epidemiological_week(pd.Series(dias))
        ultima = int(semanas.loc[semanas["semana_epi_ano"] == ano, "semana_epi_num"].max())

        assert ultima == (53 if ano in _ANOS_DE_53_SEMANAS else 52)

    @pytest.mark.parametrize("ano", _ANOS)
    def test_a_se_1_contem_sempre_o_dia_4_de_janeiro(self, ano):
        """Formulacao equivalente a "ao menos 4 dias no ano novo", e a que o codigo usa."""
        semanas = _semanas([f"{ano}-01-04"])
        assert int(semanas["semana_epi_ano"].iloc[0]) == ano
        assert int(semanas["semana_epi_num"].iloc[0]) == 1


class TestViradaDeAno:
    """31 de dezembro e 1 de janeiro nem sempre pertencem ao ano do calendario."""

    @pytest.mark.parametrize("ano", _ANOS)
    def test_31_de_dezembro_e_1_de_janeiro_seguinte_podem_ser_a_mesma_semana(self, ano):
        """Se caem na mesma semana, tem de receber o mesmo rotulo -- nos dois campos."""
        fim = _semanas([f"{ano}-12-31"]).iloc[0]
        inicio = _semanas([f"{ano + 1}-01-01"]).iloc[0]

        mesma_semana = pd.Timestamp(f"{ano}-12-31").dayofweek != 5  # sabado fecha a semana
        if mesma_semana:
            assert (fim["semana_epi_ano"], fim["semana_epi_num"]) == (
                inicio["semana_epi_ano"],
                inicio["semana_epi_num"],
            )

    @pytest.mark.parametrize("ano", _ANOS)
    def test_31_de_dezembro_pertence_ao_proprio_ano_ou_ao_seguinte(self, ano):
        """A virada e assimetrica, e o teste declara qual dos dois lados ocorre.

        Quando a semana iniciada em domingo tem 4 ou mais dias no ano novo, ela
        ja e a SE 1 do ano seguinte -- e o 31 de dezembro cai dentro dela. E o
        caso de 2024: 31/12/2024 e SE 1 de 2025.
        """
        dia = pd.Timestamp(f"{ano}-12-31")
        # Caminho independente do implementado: a implementacao decide pelo ano
        # da quarta-feira da semana; aqui a decisao vem da definicao escrita --
        # a SE 1 e a semana que contem o dia 4 de janeiro.
        inicio = _domingo_anterior(dia)
        e_se1_do_ano_seguinte = (
            inicio <= pd.Timestamp(f"{ano + 1}-01-04") <= inicio + pd.Timedelta(days=6)
        )

        semana = _semanas([f"{ano}-12-31"]).iloc[0]
        assert int(semana["semana_epi_ano"]) == (ano + 1 if e_se1_do_ano_seguinte else ano)

    @pytest.mark.parametrize("ano", _ANOS)
    def test_1_de_janeiro_nunca_recebe_semana_do_ano_anterior_ao_anterior(self, ano):
        semana = _semanas([f"{ano}-01-01"]).iloc[0]
        assert int(semana["semana_epi_ano"]) in (ano - 1, ano)

    def test_rotulo_ordenavel_atravessa_a_virada_em_ordem_cronologica(self):
        """A ordenacao lexicografica do rotulo tem de coincidir com a do tempo."""
        dias = pd.date_range("2025-12-01", "2026-02-01", freq="D")
        semanas = epidemiological_week(pd.Series(dias))
        rotulos = (
            semanas["semana_epi_ano"].astype("string")
            + "-"
            + semanas["semana_epi_num"].astype("string").str.zfill(2)
        )

        assert list(rotulos) == sorted(rotulos)

    def test_ano_civil_e_ano_epidemiologico_sao_campos_distintos(self):
        """1 de janeiro de 2026 e SE 53 de 2025: confundir os dois esvaziaria uma semana."""
        frame = clean_chunk(
            _raw_chunk(DT_SIN_PRI="2026-01-01", DT_DIGITA="2026-02-01", DT_EVOLUCA=""),
            2026,
            QualityReport(),
        )

        assert frame["semana_epi"].iloc[0] == "2025-53"
        assert int(frame["ano_sintomas"].iloc[0]) == 2026
        assert frame["mes_sintomas"].iloc[0] == "2026-01"


class TestReconciliacaoComSemPri:
    """`SEM_PRI` e contada contra a derivada; nenhuma das duas corrige a outra."""

    def test_divergencia_nao_altera_a_derivada_nem_a_publicada(self):
        report = QualityReport()
        frame = clean_chunk(
            _raw_chunk(DT_SIN_PRI="2025-12-31", DT_DIGITA="2026-01-10", SEM_PRI="01"),
            2025,
            report,
        )

        # A derivada manda: 31/12/2025 e a SE 53 de 2025.
        assert frame["semana_epi"].iloc[0] == "2025-53"
        assert int(frame["semana_epi_num"].iloc[0]) == 53
        # A publicada fica exatamente como veio.
        assert frame["SEM_PRI"].iloc[0] == "01"
        # E a divergencia e so contagem: nada foi alterado, entao nao ha ajuste.
        assert report.epiweek_compared == 1
        assert report.epiweek_mismatch == 1
        assert frame[ADJUSTMENT_COLUMN].iloc[0] == ""

    def test_sem_pri_nao_preenche_a_derivada_quando_a_data_falta(self):
        """Sem `DT_SIN_PRI` nao ha semana: preencher com `SEM_PRI` misturaria criterios."""
        report = QualityReport()
        frame = clean_chunk(_raw_chunk(DT_SIN_PRI="", DT_EVOLUCA="", SEM_PRI="18"), 2026, report)

        assert pd.isna(frame["semana_epi"].iloc[0])
        assert pd.isna(frame["semana_epi_num"].iloc[0])
        assert frame["SEM_PRI"].iloc[0] == "18"
        # Sem derivada nao ha o que reconciliar: o par nao entra no denominador.
        assert report.epiweek_compared == 0
        assert report.epiweek_mismatch == 0
        # E o registro perde o eixo temporal, que e outra coisa.
        assert bool(frame["flag_data_invalida"].iloc[0]) is True

    @pytest.mark.parametrize("publicada", ["202452", "0", "54", "99", "abc"])
    def test_formato_incomparavel_de_sem_pri_nao_vira_divergencia(self, publicada):
        """Safras publicam "AAAASS" ou lixo: contar isso daria 100% de erro aparente."""
        report = QualityReport()
        clean_chunk(
            _raw_chunk(DT_SIN_PRI="2024-12-29", DT_DIGITA="2025-01-10", SEM_PRI=publicada),
            2024,
            report,
        )

        assert report.epiweek_compared == 0
        assert report.epiweek_mismatch == 0

    def test_percentual_de_divergencia_usa_o_denominador_correto(self):
        """Duas comparaveis, uma divergente: 50%, nao 33% sobre os tres registros."""
        report = QualityReport()
        clean_chunk(_raw_chunk(DT_SIN_PRI="2024-12-29", SEM_PRI="01"), 2024, report)
        clean_chunk(_raw_chunk(DT_SIN_PRI="2024-12-29", SEM_PRI="52"), 2024, report)
        clean_chunk(_raw_chunk(DT_SIN_PRI="2024-12-29", SEM_PRI=""), 2024, report)

        publicado = report.to_dict(source_files=["x.csv"])["rules"]["semana_epidemiologica"]
        assert publicado["registros_comparados_com_SEM_PRI"] == 2
        assert publicado["divergencias"] == 1
        assert publicado["percentual"] == pytest.approx(50.0)
