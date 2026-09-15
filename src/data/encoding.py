"""Deteccao do encoding dos arquivos brutos do DATASUS.

O encoding do CSV publicado **nao e estavel entre safras**. As safras antigas do
SIVEP-Gripe sao exportadas do DBF em `latin-1`; as publicacoes recentes, e o
arquivo distribuido junto ao enunciado, sao UTF-8 validos no arquivo inteiro.

Fixar a constante em `latin-1` -- como o projeto fazia -- nao quebra a carga,
porque `latin-1` decodifica qualquer byte sem erro. Esse e exatamente o
problema: um arquivo UTF-8 lido como `latin-1` produz mojibake **silencioso**.
Hoje o dano e nulo, porque toda coluna da allowlist e ASCII (datas, codigos,
siglas de UF); mas a primeira coluna de texto que entrar no schema traz o defeito
junto, sem nada falhar.

A deteccao aqui e uma decisao, nao um palpite: tenta decodificar o arquivo
**inteiro** como UTF-8 de forma incremental e so recorre a `latin-1` se houver
byte invalido. Nao ha heuristica estatistica nem amostragem -- um arquivo que
decodifica integralmente como UTF-8 e UTF-8.

O encoding escolhido e registrado na proveniencia da carga, de modo que uma
mudanca de encoding entre safras fique visivel no historico de ingestao em vez
de se manifestar como caractere corrompido num relatorio.
"""

from __future__ import annotations

import codecs
from pathlib import Path
from typing import Final

from src.observability.logging_config import get_logger

logger = get_logger(__name__)

#: Encoding preferido: se o arquivo inteiro decodifica, e este.
PREFERRED_ENCODING: Final[str] = "utf-8"

#: Encoding de recurso. `latin-1` mapeia todos os 256 bytes, portanto nunca
#: falha -- e por isso e o ultimo recurso, nunca o padrao.
FALLBACK_ENCODING: Final[str] = "latin-1"

_READ_BLOCK = 4 * 1024 * 1024


def detect_encoding(path: Path) -> str:
    """Decide o encoding de um CSV bruto lendo o arquivo inteiro.

    Args:
        path: caminho do CSV bruto.

    Returns:
        `"utf-8"` se o arquivo inteiro decodificar como UTF-8;
        `"latin-1"` caso contrario.
    """
    decoder = codecs.getincrementaldecoder(PREFERRED_ENCODING)()
    with path.open("rb") as handle:
        while block := handle.read(_READ_BLOCK):
            try:
                decoder.decode(block)
            except UnicodeDecodeError:
                logger.info(
                    "arquivo nao e UTF-8; usando fallback",
                    extra={"arquivo": path.name, "encoding": FALLBACK_ENCODING},
                )
                return FALLBACK_ENCODING
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        # Bytes truncados no fim do arquivo: sequencia UTF-8 incompleta.
        logger.info(
            "arquivo termina com sequencia UTF-8 incompleta; usando fallback",
            extra={"arquivo": path.name, "encoding": FALLBACK_ENCODING},
        )
        return FALLBACK_ENCODING

    return PREFERRED_ENCODING
