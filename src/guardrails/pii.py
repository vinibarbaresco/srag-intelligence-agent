"""Deteccao e mascaramento de dados pessoais.

Usado em dois pontos distintos do sistema:

* antes de gravar qualquer evento de auditoria (para que o proprio log nao vire
  um vazamento de dados);
* antes de entregar qualquer texto ao usuario (Guardrail 2 -- Sensitive Data).

As expressoes cobrem os identificadores brasileiros que poderiam aparecer em
campos de texto livre do SIVEP-Gripe caso o contrato de colunas fosse violado.
"""

from __future__ import annotations

import re
from typing import Any, Final, NamedTuple

MASK: Final[str] = "[REDACTED]"


class PIIPattern(NamedTuple):
    """Padrao de identificador pessoal reconhecido pelo guardrail."""

    name: str
    regex: re.Pattern[str]
    description: str


PII_PATTERNS: Final[tuple[PIIPattern, ...]] = (
    PIIPattern(
        "cpf",
        re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
        "Cadastro de Pessoa Fisica",
    ),
    PIIPattern(
        "cns",
        re.compile(r"\b[1-9]\d{2}[ .]?\d{4}[ .]?\d{4}[ .]?\d{4}\b"),
        "Cartao Nacional de Saude",
    ),
    PIIPattern(
        "email",
        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
        "endereco de e-mail",
    ),
    PIIPattern(
        "telefone",
        re.compile(r"(?<!\d)(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)"),
        "telefone brasileiro",
    ),
    PIIPattern(
        "cep",
        re.compile(r"\b\d{5}-\d{3}\b"),
        "codigo de enderecamento postal",
    ),
    PIIPattern(
        "data_nascimento",
        re.compile(r"\bnascid[oa]\s+em\s+\d{2}/\d{2}/\d{4}\b", re.IGNORECASE),
        "data de nascimento em texto livre",
    ),
)


def find_pii(text: str) -> list[str]:
    """Lista os tipos de dado pessoal encontrados em `text`.

    Args:
        text: conteudo a inspecionar.

    Returns:
        Nomes dos padroes encontrados, sem repeticao e em ordem estavel.
    """
    if not text:
        return []
    return [pattern.name for pattern in PII_PATTERNS if pattern.regex.search(text)]


def scrub_text(text: str) -> str:
    """Substitui todo dado pessoal reconhecido por :data:`MASK`."""
    if not text:
        return text
    for pattern in PII_PATTERNS:
        text = pattern.regex.sub(MASK, text)
    return text


def scrub_value(value: Any) -> Any:
    """Aplica :func:`scrub_text` recursivamente em estruturas aninhadas.

    Dicionarios, listas e tuplas sao percorridos; valores nao textuais sao
    devolvidos sem alteracao.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(item) for item in value]
    return value
