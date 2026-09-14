"""Golden set de avaliacao dos guardrails.

Um conjunto fixo de pedidos (entrada) e de textos de saida com o veredito
esperado. Diferente dos testes unitarios, o golden set mede o comportamento
**agregado**: taxa de bloqueio indevido (falso positivo) e de aprovacao
indevida (falso negativo). Qualquer mudanca em prompt, regex ou politica que
altere um veredito aparece aqui, caso a caso, antes de chegar ao usuario.

Os arquivos em `tests/golden/*.jsonl` sao a fonte; para acrescentar um caso,
acrescente uma linha. Um caso e aceito no conjunto quando a expectativa e
inequivoca -- casos ambiguos ficam fora, para nao congelar uma decisao
discutivel como verdade.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.guardrails.input_guard import validate_request
from src.guardrails.output_guard import build_evidence, validate_output

GOLDEN_DIR = Path(__file__).parent / "golden"

#: Evidencia fixa usada nos casos de saida: os valores reais de uma execucao.
EVIDENCE = build_evidence(
    {
        "mortality_rate": {
            "metric": "mortality_rate",
            "value": 5.25,
            "numerator": 921,
            "denominator": 17535,
        },
        "case_growth_rate": {
            "metric": "case_growth_rate",
            "value": -32.39,
            "numerator": -11804,
            "denominator": 36446,
        },
    }
)


def _load(name: str) -> list[dict]:
    path = GOLDEN_DIR / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


INPUT_CASES = _load("entrada.jsonl")
OUTPUT_CASES = _load("saida.jsonl")


def _input_verdict(case: dict) -> tuple[str, str | None]:
    result = validate_request(case["pedido"])
    return ("aceito" if result.allowed else "recusado"), result.blocked_by


def _output_verdict(case: dict) -> tuple[str, list[str]]:
    result = validate_output(case["texto"], EVIDENCE)
    return ("aprovado" if result.allowed else "bloqueado"), result.blocked_by


class TestGoldenEntrada:
    @pytest.mark.parametrize("case", INPUT_CASES, ids=[case["id"] for case in INPUT_CASES])
    def test_caso(self, case):
        verdict, blocked_by = _input_verdict(case)
        assert verdict == case["esperado"], f"{case['pedido']!r} -> {blocked_by}"
        if case["guardrail"]:
            assert blocked_by == case["guardrail"]


class TestGoldenSaida:
    @pytest.mark.parametrize("case", OUTPUT_CASES, ids=[case["id"] for case in OUTPUT_CASES])
    def test_caso(self, case):
        verdict, blocked_by = _output_verdict(case)
        assert verdict == case["esperado"], f"{case['texto']!r} -> {blocked_by}"
        if case["guardrail"]:
            assert case["guardrail"] in blocked_by


class TestTaxasAgregadas:
    """Metricas do conjunto inteiro, impressas com `pytest -rA -k Taxas`."""

    def test_taxas_de_erro_sao_zero(self, capsys):
        rows = []
        for case in INPUT_CASES:
            verdict, _ = _input_verdict(case)
            rows.append(("entrada", case["esperado"], verdict))
        for case in OUTPUT_CASES:
            verdict, _ = _output_verdict(case)
            rows.append(("saida", case["esperado"], verdict))

        def rate(kind: str, expected: str, got: str) -> tuple[int, int]:
            universe = [row for row in rows if row[0] == kind and row[1] == expected]
            wrong = [row for row in universe if row[2] == got]
            return len(wrong), len(universe)

        fp_in, n_ok_in = rate("entrada", "aceito", "recusado")
        fn_in, n_bad_in = rate("entrada", "recusado", "aceito")
        fp_out, n_ok_out = rate("saida", "aprovado", "bloqueado")
        fn_out, n_bad_out = rate("saida", "bloqueado", "aprovado")

        with capsys.disabled():
            print(
                "\n[golden set] entrada: bloqueio indevido "
                f"{fp_in}/{n_ok_in}, aprovacao indevida {fn_in}/{n_bad_in} | "
                f"saida: bloqueio indevido {fp_out}/{n_ok_out}, aprovacao indevida "
                f"{fn_out}/{n_bad_out} | total {len(rows)} casos"
            )

        assert (fp_in, fn_in, fp_out, fn_out) == (0, 0, 0, 0)

    def test_conjunto_tem_cobertura_minima(self):
        assert len(INPUT_CASES) >= 30
        assert len(OUTPUT_CASES) >= 30
        assert {case["guardrail"] for case in OUTPUT_CASES if case["guardrail"]} == {
            "evidence_binding",
            "medical_advice",
            "sensitive_data",
        }
