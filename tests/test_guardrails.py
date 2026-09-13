"""Testes dos seis guardrails declarados.

Cada politica de `src.guardrails.policies` tem ao menos um teste que comprova
que ela realmente bloqueia o que promete bloquear -- e outro que comprova que
nao bloqueia conteudo legitimo (falso positivo e tao ruim quanto falso negativo
neste contexto, porque leva a suprimir um relatorio correto).
"""

from __future__ import annotations

import pytest

from src.guardrails.input_guard import validate_request
from src.guardrails.output_guard import build_evidence, validate_output
from src.guardrails.pii import MASK, find_pii, scrub_text, scrub_value
from src.guardrails.policies import ALL_POLICIES, DISCLAIMER, UNCERTAINTY_STATEMENT

_EVIDENCE = build_evidence(
    {
        "case_growth_rate": {
            "metric": "case_growth_rate",
            "value": -32.39,
            "numerator": -11804,
            "denominator": 36446,
            "components": {"casos_periodo_atual": 24642},
        },
        "mortality_rate": {"metric": "mortality_rate", "value": 5.25, "numerator": 921},
    }
)


class TestPoliticasDeclaradas:
    def test_as_seis_politicas_estao_declaradas(self):
        assert len(ALL_POLICIES) == 6
        assert {policy.key for policy in ALL_POLICIES} == {
            "medical_advice",
            "sensitive_data",
            "evidence_binding",
            "no_arbitrary_sql",
            "news_never_overrides_data",
            "uncertainty",
        }

    def test_cada_politica_declara_onde_e_aplicada(self):
        assert all(policy.enforced_at and policy.description for policy in ALL_POLICIES)


class TestGuardrail1AconselhamentoMedico:
    @pytest.mark.parametrize(
        "pedido",
        [
            "Qual remedio devo tomar para gripe?",
            "Como tratar meu paciente com SRAG?",
            "Meu filho esta com febre, o que faco?",
            "Qual a dose recomendada de oseltamivir?",
        ],
    )
    def test_pedido_clinico_e_recusado_na_entrada(self, pedido):
        result = validate_request(pedido)
        assert result.allowed is False
        assert result.blocked_by == "medical_advice"
        assert "epidemiologicos agregados" in result.reason

    @pytest.mark.parametrize(
        "pedido",
        [
            "Gere o relatorio de SRAG do ultimo mes",
            "Como evoluiu a mortalidade por SRAG em SP?",
            "Qual a taxa de admissao em UTI entre hospitalizados?",
        ],
    )
    def test_pedido_epidemiologico_e_aceito(self, pedido):
        assert validate_request(pedido).allowed is True

    def test_linguagem_prescritiva_e_bloqueada_na_saida(self):
        result = validate_output(
            "Recomenda-se prescrever antiviral aos pacientes internados.", _EVIDENCE
        )
        assert result.allowed is False
        assert "medical_advice" in result.blocked_by

    def test_disclaimer_esta_definido(self):
        assert "nao constitui diagnostico" in DISCLAIMER.lower()


class TestGuardrail2DadosSensiveis:
    @pytest.mark.parametrize(
        ("texto", "tipo"),
        [
            ("CPF 123.456.789-00", "cpf"),
            ("contato joao@hospital.com.br", "email"),
            ("telefone (11) 98765-4321", "telefone"),
            ("CEP 01310-100", "cep"),
        ],
    )
    def test_identificadores_sao_detectados(self, texto, tipo):
        assert tipo in find_pii(texto)

    def test_texto_agregado_nao_dispara_falso_positivo(self):
        assert find_pii("Foram 24642 casos em 30 dias, com 5.25% de letalidade.") == []

    def test_mascaramento_substitui_o_identificador(self):
        assert scrub_text("CPF 123.456.789-00") == f"CPF {MASK}"

    def test_mascaramento_percorre_estruturas_aninhadas(self):
        scrubbed = scrub_value({"contato": ["a@b.com"], "n": 10})
        assert scrubbed["contato"] == [MASK]
        assert scrubbed["n"] == 10

    def test_saida_com_dado_pessoal_e_bloqueada(self):
        result = validate_output("Paciente de CPF 123.456.789-00 evoluiu.", _EVIDENCE)
        assert result.allowed is False
        assert "sensitive_data" in result.blocked_by


class TestGuardrail3Evidencia:
    def test_valor_vindo_das_tools_e_aceito(self):
        result = validate_output(
            "Houve reducao de 32,39% nos casos, com 24642 na janela atual.", _EVIDENCE
        )
        assert result.allowed is True

    def test_valor_inventado_e_bloqueado(self):
        result = validate_output("A mortalidade foi de 12,7%.", _EVIDENCE)
        assert result.allowed is False
        assert "evidence_binding" in result.blocked_by

    def test_arredondamento_do_valor_real_e_tolerado(self):
        assert validate_output("Reducao de 32,4% nos casos.", _EVIDENCE).allowed is True

    def test_sinal_invertido_de_valor_negativo_e_aceito(self):
        # "queda de 32,39%" descreve corretamente o valor -32.39.
        assert validate_output("Queda de 32,39%.", _EVIDENCE).allowed is True

    def test_numeros_de_enumeracao_nao_exigem_lastro(self):
        assert validate_output(
            "Os 4 indicadores foram calculados em 2026.", _EVIDENCE
        ).allowed is True

    def test_violacao_identifica_o_valor_problematico(self):
        result = validate_output("A taxa foi de 99,9%.", _EVIDENCE)
        assert any("99,9" in violation["detail"] for violation in result.violations)


class TestGuardrail4SemSQLArbitrario:
    def test_nao_existe_tool_de_consulta_livre(self):
        from src.tools.registry import REGISTRY

        proibidos = {"execute_sql", "query", "run_query", "sql"}
        assert not proibidos & set(REGISTRY)

    def test_parametro_fora_do_dominio_e_rejeitado(self):
        from src.tools.registry import call_tool

        result = call_tool("get_case_growth_rate", {"uf": "'; DROP TABLE srag_cases;--"})
        assert "error" in result
        assert "Parametros invalidos" in result["error"]

    def test_parametro_nao_declarado_e_rejeitado(self):
        from src.tools.registry import call_tool

        result = call_tool("get_daily_cases", {"order_by": "1; DELETE FROM srag_cases"})
        assert "error" in result

    def test_tool_desconhecida_interrompe_o_fluxo(self):
        from src.tools.registry import UnknownToolError, call_tool

        with pytest.raises(UnknownToolError):
            call_tool("execute_sql", {"sql": "SELECT 1"})


class TestGuardrail5NoticiasNaoSobrescrevemDados:
    def test_noticias_ficam_em_campo_separado_do_estado(self):
        from src.agent.state import SRAGState

        annotations = SRAGState.__annotations__
        assert "external_context" in annotations
        assert "metrics" in annotations

    def test_tool_de_noticias_declara_o_limite_de_uso(self):
        from src.tools.news_tools import search_srag_news

        result = search_srag_news(query="SRAG casos recentes", top_k=1)
        assert "nao alteram" in result["usage_note"]

    def test_retorno_de_noticia_nao_contem_metrica(self):
        from src.tools.news_tools import search_srag_news

        result = search_srag_news(query="SRAG casos recentes", top_k=1)
        assert "value" not in result
        assert "metric" not in result


class TestGuardrail6Incerteza:
    def test_declaracao_de_incerteza_esta_definida(self):
        assert "Nao e possivel calcular" in UNCERTAINTY_STATEMENT

    def test_metrica_indisponivel_devolve_none_e_motivo(self, connection):
        from src.metrics.epidemiology import case_growth_rate
        from src.metrics.filters import AnalyticFilters

        result = case_growth_rate(connection, AnalyticFilters(classification=1))
        assert result.value is None
        assert result.unavailable_reason
        assert result.available is False


class TestValidacaoDeEntrada:
    def test_solicitacao_vazia_e_recusada(self):
        result = validate_request("   ")
        assert result.allowed is False
        assert result.blocked_by == "empty_request"

    def test_uf_invalida_e_recusada_antes_de_consultar_dados(self):
        result = validate_request("Relatorio de SRAG", uf="XX")
        assert result.allowed is False
        assert result.blocked_by == "invalid_filter"

    def test_recorte_por_uf_gera_aviso_de_instabilidade(self):
        result = validate_request("Relatorio de SRAG", uf="AC")
        assert result.allowed is True
        assert any("instaveis" in warning for warning in result.warnings)
