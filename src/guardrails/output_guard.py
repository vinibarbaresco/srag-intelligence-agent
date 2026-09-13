"""Validacao da saida antes da publicacao do relatorio.

Tres verificacoes, na ordem em que sao aplicadas:

1. **Dados pessoais** -- varredura de identificadores no texto final.
2. **Linguagem clinica** -- bloqueio de conteudo prescritivo ou diagnostico.
3. **Evidencia (grounding)** -- todo numero citado na interpretacao e confrontado
   com os valores efetivamente retornados pelas tools.

A terceira e a mais importante: e ela que impede que o modelo publique um valor
que nenhuma consulta produziu. O conjunto de evidencias e montado a partir dos
retornos das tools, nao do texto do modelo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Iterable

from src.guardrails.pii import find_pii, scrub_text
from src.guardrails.policies import (
    EVIDENCE_BINDING,
    MEDICAL_ADVICE,
    PRESCRIPTIVE_OUTPUT_PATTERNS,
    SENSITIVE_DATA,
)

#: Numeros que nao carecem de lastro: ordinais de lista, dias do mes, contagens
#: de itens e anos do periodo analisado. Sem esta excecao, frases legitimas como
#: "os 4 indicadores" ou "em 2026" seriam sinalizadas como alucinacao.
_BENIGN_INTEGERS: Final[frozenset[float]] = frozenset(
    {float(value) for value in range(0, 32)} | {float(year) for year in range(2019, 2031)}
)

#: Tolerancia absoluta e relativa para casar um numero citado com a evidencia.
#: Cobre arredondamento e mudanca de casas decimais na redacao.
_ABSOLUTE_TOLERANCE: Final[float] = 0.051
_RELATIVE_TOLERANCE: Final[float] = 0.005

_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w/])-?\d{1,3}(?:\.\d{3})+(?:,\d+)?(?![\w/])"  # 1.234,5 (pt-BR)
    r"|(?<![\w/])-?\d+,\d+(?![\w/])"                      # 12,4
    r"|(?<![\w/])-?\d+(?:\.\d+)?(?![\w/])"                # 12 ou 12.4
)


@dataclass
class EvidenceSet:
    """Conjunto de valores que o relatorio tem permissao de citar.

    Construido exclusivamente a partir dos retornos das tools. Guarda tambem a
    procedencia de cada valor, para que uma violacao possa ser explicada.
    """

    values: set[float] = field(default_factory=set)
    provenance: dict[float, str] = field(default_factory=dict)

    def add(self, value: Any, origin: str) -> None:
        """Registra um valor como citavel, junto de suas variantes de redacao."""
        number = _as_float(value)
        if number is None:
            return

        for variant in _rounding_variants(number):
            self.values.add(variant)
            self.provenance.setdefault(variant, origin)

    def add_structure(self, payload: Any, origin: str) -> None:
        """Percorre recursivamente um retorno de tool registrando seus numeros."""
        if isinstance(payload, dict):
            for key, item in payload.items():
                self.add_structure(item, f"{origin}.{key}")
        elif isinstance(payload, (list, tuple)):
            for item in payload:
                self.add_structure(item, origin)
        elif isinstance(payload, bool):
            return
        else:
            self.add(payload, origin)

    def supports(self, number: float) -> bool:
        """Indica se `number` esta lastreado por alguma evidencia."""
        if number in _BENIGN_INTEGERS or abs(number) in _BENIGN_INTEGERS:
            return True
        if number in self.values or abs(number) in self.values:
            return True
        return any(_close(number, candidate) for candidate in self.values)

    def explain(self, number: float) -> str | None:
        """Devolve a origem do valor mais proximo, quando houver."""
        for candidate in (number, abs(number)):
            if candidate in self.provenance:
                return self.provenance[candidate]
        return None


@dataclass(slots=True)
class OutputValidation:
    """Resultado da validacao de uma saida textual."""

    allowed: bool
    text: str
    violations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocked_by(self) -> list[str]:
        return sorted({violation["policy"] for violation in self.violations})

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": list(self.violations),
            "blocked_by": self.blocked_by,
        }


def build_evidence(payloads: dict[str, Any]) -> EvidenceSet:
    """Monta o conjunto de evidencias a partir dos retornos das tools.

    Args:
        payloads: mapa `nome da tool -> retorno`, exatamente como registrado no
            estado do grafo.

    Returns:
        Conjunto de valores citaveis com sua procedencia.
    """
    evidence = EvidenceSet()
    for name, payload in payloads.items():
        evidence.add_structure(payload, name)
    return evidence


def validate_output(text: str, evidence: EvidenceSet) -> OutputValidation:
    """Aplica as tres verificacoes de saida ao texto informado.

    Args:
        text: interpretacao produzida pelo modelo.
        evidence: conjunto de valores lastreados pelas tools.

    Returns:
        Resultado com `allowed=False` e a lista de violacoes quando o texto nao
        pode ser publicado como esta.
    """
    violations: list[dict[str, Any]] = []

    for pii_type in find_pii(text):
        violations.append(
            {
                "policy": SENSITIVE_DATA.key,
                "type": pii_type,
                "detail": f"Identificador pessoal do tipo '{pii_type}' presente na saida.",
            }
        )

    for pattern in PRESCRIPTIVE_OUTPUT_PATTERNS:
        match = pattern.search(text)
        if match:
            violations.append(
                {
                    "policy": MEDICAL_ADVICE.key,
                    "type": "linguagem_prescritiva",
                    "detail": f"Trecho com conteudo prescritivo: {match.group(0)!r}.",
                }
            )

    for raw, number in _extract_numbers(text):
        if not evidence.supports(number):
            violations.append(
                {
                    "policy": EVIDENCE_BINDING.key,
                    "type": "valor_sem_evidencia",
                    "detail": (
                        f"O valor {raw!r} nao corresponde a nenhum resultado "
                        "retornado pelas tools."
                    ),
                }
            )

    return OutputValidation(allowed=not violations, text=text, violations=violations)


def sanitize(text: str) -> str:
    """Mascara dados pessoais em um texto que sera exibido mesmo assim."""
    return scrub_text(text)


# =============================================================================
# Utilidades internas
# =============================================================================


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rounding_variants(number: float) -> Iterable[float]:
    """Variantes de arredondamento com que um valor pode ser escrito."""
    seen: set[float] = set()
    for decimals in (0, 1, 2, 3):
        for candidate in (round(number, decimals), round(abs(number), decimals)):
            if candidate not in seen:
                seen.add(candidate)
                yield candidate


def _close(number: float, candidate: float) -> bool:
    difference = abs(number - candidate)
    if difference <= _ABSOLUTE_TOLERANCE:
        return True
    scale = max(abs(number), abs(candidate))
    return scale > 0 and difference / scale <= _RELATIVE_TOLERANCE


def _extract_numbers(text: str) -> list[tuple[str, float]]:
    """Extrai os numeros citados no texto, em notacao pt-BR ou internacional."""
    found: list[tuple[str, float]] = []
    for match in _NUMBER_PATTERN.finditer(text):
        raw = match.group(0)
        parsed = _parse_number(raw)
        if parsed is not None:
            found.append((raw, parsed))
    return found


def _parse_number(raw: str) -> float | None:
    """Converte `1.234,5` (pt-BR) ou `1234.5` (internacional) em float."""
    text = raw.strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif re.fullmatch(r"-?\d{1,3}\.\d{3}", text):
        # Sem casa decimal ambigua: "1.234" em pt-BR e milhar, nao decimal.
        text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None
