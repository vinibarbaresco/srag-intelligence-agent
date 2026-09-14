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

# Segredos nao sao PII, mas compartilham a mesma fronteira de sanitizacao dos
# logs e da auditoria. Chaves sensiveis sao mascaradas pelo nome; formatos
# comuns tambem sao reconhecidos quando aparecem dentro de texto livre.
SENSITIVE_KEY_FRAGMENTS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)

SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
)


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
    """Substitui dados pessoais e segredos reconhecidos por :data:`MASK`."""
    if not text:
        return text
    for pattern in PII_PATTERNS:
        text = pattern.regex.sub(MASK, text)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def scrub_value(value: Any) -> Any:
    """Aplica :func:`scrub_text` recursivamente em estruturas aninhadas.

    Dicionarios, listas e tuplas sao percorridos; valores nao textuais sao
    devolvidos sem alteracao.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            key: (
                MASK
                if any(fragment in str(key).lower() for fragment in SENSITIVE_KEY_FRAGMENTS)
                else scrub_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_value(item) for item in value]
    return value
