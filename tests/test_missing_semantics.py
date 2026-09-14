"""Testes da regra que atravessa o projeto inteiro: missing nunca vira negativa.

A regra em uma frase: ausencia de informacao **nao** e "Nao". Um campo vazio ou
com codigo de ausencia nao pode produzir `True` numa derivada booleana (ficaria
no numerador) nem `False` numa derivada de "informado" que seja usada como
denominador (ficaria contado como respondente negativo).

O defeito que estes testes impedem e assimetrico e silencioso: ler ausencia como
"Nao" nao derruba nenhum calculo -- apenas infla o denominador e deprime toda
taxa, de forma proporcional ao quanto o campo esta vazio. Em `EVOLUCAO`, que na
safra de referencia tem dezenas de milhares de casos em aberto, isso cortaria a
mortalidade pela metade sem nenhum sinal de erro.

Cobre tambem a identidade de completude
`total = validos + ignorados + ausentes + fora_do_dominio`, inclusive nas
colunas que **nao** possuem o codigo 9 no dicionario (D-08).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.cleaning import clean_chunk
from src.data.quality import QualityReport
from src.data.schema import (
    ALLOWED_COLUMNS,
    CATEGORICAL_COLUMNS,
    CODE_LABELS,
    missing_codes_for,
)

#: Para cada variavel: as derivadas booleanas que ela alimenta e a derivada que
#: serve de denominador. Ausencia nao pode entrar em nenhuma das duas.
_NUMERADOR_E_DENOMINADOR: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "EVOLUCAO": (("eh_obito_srag",), ("caso_encerrado",)),
    "UTI": (("teve_admissao_uti", "estadia_uti_utilizavel"), ("uti_informado",)),
    "SUPORT_VEN": (
        ("foi_ventilado", "ventilacao_invasiva", "ventilacao_nao_invasiva"),
        ("ventilacao_informada",),
    ),
    "HOSPITAL": (("foi_hospitalizado",), ("hospitalizacao_informada",)),
    "VACINA_COV": (("vacinado_covid",), ("vacina_covid_informada",)),
    "VACINA": (("vacinado_influenza",), ("vacina_influenza_informada",)),
    "NOSOCOMIAL": (("caso_nosocomial",), ()),
    "CRITERIO": (("etiologia_laboratorial",), ("etiologia_criterio_informado",)),
}


def _raw_chunk(**overrides) -> pd.DataFrame:
    """Bloco bruto minimo com as colunas permitidas preenchidas."""
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
            "CRITERIO": ["1"],
            "HOSPITAL": ["1"],
            "UTI": ["2"],
            "SUPORT_VEN": ["3"],
            "NOSOCOMIAL": ["2"],
            "VACINA_COV": ["1"],
            "VACINA": ["2"],
            "CS_SEXO": ["F"],
        }
    )
    base.update({key: [value] for key, value in overrides.items()})
    return pd.DataFrame(base, dtype="string")


class TestAusenciaNuncaViraNegativa:
    """Um teste por variavel, nos dois jeitos de estar ausente: vazio e codigo 9."""

    @pytest.mark.parametrize("coluna", sorted(_NUMERADOR_E_DENOMINADOR))
    @pytest.mark.parametrize("valor", ["", "9"])
    def test_ausente_fica_fora_do_numerador_e_do_denominador(self, coluna, valor):
        numeradores, denominadores = _NUMERADOR_E_DENOMINADOR[coluna]
        frame = clean_chunk(_raw_chunk(**{coluna: valor}), 2026, QualityReport())

        for derivada in numeradores:
            assert bool(frame[derivada].iloc[0]) is False, f"{coluna}={valor!r} -> {derivada}"
        for derivada in denominadores:
            assert bool(frame[derivada].iloc[0]) is False, f"{coluna}={valor!r} -> {derivada}"

    @pytest.mark.parametrize("coluna", sorted(_NUMERADOR_E_DENOMINADOR))
    def test_codigo_9_e_preservado_e_nao_convertido(self, coluna):
        """O 9 nao e apagado nem trocado por 2: preservar e o que torna auditavel."""
        frame = clean_chunk(_raw_chunk(**{coluna: "9"}), 2026, QualityReport())
        assert frame[coluna].iloc[0] == 9

    @pytest.mark.parametrize("coluna", sorted(_NUMERADOR_E_DENOMINADOR))
    def test_valor_negativo_declarado_entra_no_denominador(self, coluna):
        """Contraprova: "Nao" respondido e informacao e precisa entrar no denominador.

        Sem este par, um pipeline que zerasse toda derivada booleana passaria
        no teste acima -- e zeraria tambem todos os indicadores.
        """
        # SUPORT_VEN nao e Sim/Nao/Ignorado: o "Nao" dele e o codigo 3 (D-02).
        negativo = "3" if coluna == "SUPORT_VEN" else "2"
        _, denominadores = _NUMERADOR_E_DENOMINADOR[coluna]
        frame = clean_chunk(_raw_chunk(**{coluna: negativo}), 2026, QualityReport())

        for derivada in denominadores:
            assert bool(frame[derivada].iloc[0]) is True, f"{coluna}={negativo} -> {derivada}"

    def test_classi_fin_ausente_nao_recebe_grupo_etiologico(self):
        """CLASSI_FIN nao produz booleano: a ausencia tem de aparecer no rotulo (D-08)."""
        from src.data.cleaning.derived import _ETIOLOGIC_GROUPS

        vazio = clean_chunk(_raw_chunk(CLASSI_FIN=""), 2026, QualityReport())
        nove = clean_chunk(_raw_chunk(CLASSI_FIN="9"), 2026, QualityReport())

        assert vazio["grupo_etiologico"].iloc[0] == "nao_encerrado"
        assert nove["grupo_etiologico"].iloc[0] == "fora_do_dominio"
        # Nenhum dos dois pode se disfarcar de um grupo etiologico real.
        assert vazio["grupo_etiologico"].iloc[0] not in _ETIOLOGIC_GROUPS.values()
        assert nove["grupo_etiologico"].iloc[0] not in _ETIOLOGIC_GROUPS.values()

    @pytest.mark.parametrize("valor", ["", "9"])
    def test_evolucao_ausente_nao_conta_como_cura(self, valor):
        """`status_caso` e rotulo, nao booleano -- e a mesma regra vale."""
        frame = clean_chunk(_raw_chunk(EVOLUCAO=valor, DT_EVOLUCA=""), 2026, QualityReport())
        assert frame["status_caso"].iloc[0] != "cura"
        assert frame["status_caso"].iloc[0] in {"em_aberto", "desfecho_ignorado"}

    @pytest.mark.parametrize("valor", ["", "9", "7"])
    def test_uti_sem_admissao_declarada_nao_gera_estadia_utilizavel(self, valor):
        """Mesmo com data de entrada preenchida, sem UTI=1 nao ha admissao comprovada."""
        frame = clean_chunk(_raw_chunk(UTI=valor, DT_ENTUTI="2026-05-09"), 2026, QualityReport())
        assert bool(frame["estadia_uti_utilizavel"].iloc[0]) is False

    @pytest.mark.parametrize("valor", ["", "9"])
    def test_ausencia_nao_e_contada_como_fora_do_dominio(self, valor):
        """Ausencia e codigo desconhecido sao categorias distintas do relatorio."""
        report = QualityReport()
        clean_chunk(_raw_chunk(UTI=valor), 2026, report)
        assert report.out_of_domain_counts["UTI"] == 0


class TestIdentidadeDeCompletude:
    """`total = validos + ignorados + ausentes + fora_do_dominio`, em toda coluna."""

    @staticmethod
    def _report_com_todos_os_casos(coluna: str) -> QualityReport:
        """Uma carga por categoria: valido, ausencia declarada, vazio e desconhecido."""
        dominio = sorted(CODE_LABELS.get(coluna) or {})
        ausencia = sorted(missing_codes_for(coluna))
        validos = [c for c in dominio if c not in ausencia]
        # 97 nao existe em nenhum dominio declarado no dicionario.
        valores = [str(c) for c in validos] + [str(c) for c in ausencia] + ["", "97"]

        report = QualityReport()
        for valor in valores:
            clean_chunk(_raw_chunk(**{coluna: valor}), 2026, report)
        return report

    @pytest.mark.parametrize("coluna", [c for c in CATEGORICAL_COLUMNS if c != "CS_SEXO"])
    def test_identidade_confere_em_toda_coluna_categorica(self, coluna):
        completude = self._report_com_todos_os_casos(coluna).code_completeness()[coluna]

        assert completude["identidade_confere"] is True
        assert (
            completude["validos"]
            + completude["ignorados"]
            + completude["ausentes"]
            + completude["fora_do_dominio"]
            == completude["total"]
        )
        # Nenhuma categoria pode ser negativa: um valor contado duas vezes
        # (por exemplo, o 9 como ignorado E como fora do dominio) apareceria
        # aqui como `validos` negativo, com a identidade ainda "conferindo".
        assert all(
            completude[chave] >= 0
            for chave in ("validos", "ignorados", "ausentes", "fora_do_dominio")
        )

    @pytest.mark.parametrize("coluna", ["CLASSI_FIN", "CRITERIO"])
    def test_colunas_sem_codigo_9_nao_o_contam_como_ignorado(self, coluna):
        """D-08: o dominio declarado nao tem 9, entao um 9 ali e codigo inexistente."""
        assert missing_codes_for(coluna) == frozenset()

        report = QualityReport()
        clean_chunk(_raw_chunk(**{coluna: "9"}), 2026, report)

        assert report.ignored_code_counts[coluna] == 0
        assert report.out_of_domain_counts[coluna] == 1
        assert report.code_completeness()[coluna]["identidade_confere"] is True

    def test_codigo_ilegivel_e_contado_como_ausente_e_nao_duplica_a_identidade(self):
        """Um "X" vira nulo: entra em `ausentes`, nunca em `fora_do_dominio`."""
        report = QualityReport()
        clean_chunk(_raw_chunk(UTI="X"), 2026, report)

        completude = report.code_completeness()["UTI"]
        assert completude["ausentes"] == 1
        assert completude["fora_do_dominio"] == 0
        assert completude["identidade_confere"] is True

    def test_toda_coluna_categorica_lida_aparece_na_completude(self):
        """Uma coluna que escapasse da contagem nao teria identidade a conferir."""
        report = QualityReport()
        clean_chunk(_raw_chunk(), 2026, report)

        esperadas = {c for c in CATEGORICAL_COLUMNS if c != "CS_SEXO"}
        assert set(report.code_completeness()) == esperadas

    def test_identidade_publicada_no_relatorio_de_qualidade(self):
        report = QualityReport()
        for valor in ("1", "2", "9", "97", ""):
            clean_chunk(_raw_chunk(UTI=valor), 2026, report)

        publicado = report.to_dict(source_files=["INFLUD26.csv"])["rules"][
            "completude_por_coluna_categorica"
        ]
        assert all(coluna["identidade_confere"] for coluna in publicado.values())
        assert publicado["UTI"]["total"] == 5
