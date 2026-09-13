"""Geracao de embeddings para o Vector DB de noticias.

Dois backends, com a mesma interface:

* :class:`OpenAIEmbedder` -- usado quando ha `OPENAI_API_KEY`, com qualidade
  semantica real;
* :class:`HashingEmbedder` -- determinista, sem rede e sem credencial, baseado
  em hashing de n-gramas de caracteres.

O fallback existe para que a PoC continue executavel e testavel sem credencial,
e para que os testes nao dependam de chamada externa. O backend efetivamente
usado e registrado na trilha de auditoria e no relatorio, de modo que ninguem
confunda a qualidade de uma busca com a da outra.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from abc import ABC, abstractmethod
from typing import Final

from src.config import get_settings
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

HASHING_DIMENSIONS: Final[int] = 512
_NGRAM_SIZE: Final[int] = 4


class Embedder(ABC):
    """Interface comum dos geradores de embedding."""

    #: Rotulo do backend, propagado para auditoria e relatorio.
    backend: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Converte uma lista de textos em vetores densos normalizados."""


class HashingEmbedder(Embedder):
    """Embedding determinista por hashing de n-gramas de caracteres.

    Nao capta sinonimia como um modelo treinado, mas e estavel, reproduzivel e
    suficiente para agrupar noticias que compartilham vocabulario -- o caso de
    uso aqui, em que o corpus e pequeno e tematicamente homogeneo.
    """

    backend = "hashing-ngram-local"

    def __init__(self, dimensions: int = HASHING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        normalized = _normalize(text)
        if not normalized:
            return vector

        for token in _tokens(normalized):
            index = int.from_bytes(
                hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big"
            )
            vector[index % self.dimensions] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class OpenAIEmbedder(Embedder):
    """Embedding semantico via API da OpenAI."""

    backend = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.openai_embedding_model
        self._api_key = api_key or settings.openai_api_key
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY ausente; use HashingEmbedder.")
        self.backend = f"openai:{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        from langchain_openai import OpenAIEmbeddings

        client = OpenAIEmbeddings(model=self.model, api_key=self._api_key)
        return client.embed_documents(texts)


def get_embedder() -> Embedder:
    """Escolhe o backend de embedding conforme a disponibilidade de credencial."""
    settings = get_settings()
    if settings.llm_enabled:
        try:
            embedder = OpenAIEmbedder()
            logger.info("embedder selecionado", extra={"backend": embedder.backend})
            return embedder
        except (ValueError, ImportError) as exc:
            logger.warning(
                "embedder OpenAI indisponivel; usando fallback local",
                extra={"motivo": str(exc)},
            )

    embedder = HashingEmbedder()
    logger.info("embedder selecionado", extra={"backend": embedder.backend})
    return embedder


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9 ]+", " ", ascii_only).strip()


def _tokens(normalized: str) -> list[str]:
    """Palavras inteiras somadas a n-gramas de caracteres.

    As palavras dao precisao; os n-gramas dao tolerancia a variacoes morfologicas
    ("hospitalizacao" e "hospitalizacoes" compartilham a maior parte deles).
    """
    words = normalized.split()
    padded = f" {normalized} "
    ngrams = [
        padded[index : index + _NGRAM_SIZE]
        for index in range(len(padded) - _NGRAM_SIZE + 1)
    ]
    return words + ngrams
