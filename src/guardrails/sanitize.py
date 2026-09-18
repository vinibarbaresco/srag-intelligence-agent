"""Saneamento de texto externo e tecnico antes de publicar ou de dar ao modelo.

Duas fronteiras distintas, atendidas aqui porque a tecnica e a mesma:

1. **Detalhe tecnico -> relatorio publico.** Uma falha de infraestrutura
   (Vector DB ocupado, feed fora do ar) produz mensagens com caminho de
   arquivo, PID, numero de porta e, as vezes, traceback. Publicar isso no
   relatorio e ruim por tres motivos independentes: vaza topologia interna,
   polui um documento destinado a profissionais de saude, e -- o efeito que de
   fato quebrava a execucao -- injeta numeros sem lastro no texto, que o
   guardrail de evidencia entao bloqueia, derrubando ate a redacao
   deterministica. :func:`public_reason` reduz a mensagem ao que o leitor
   precisa saber; o detalhe integral vai para a trilha de auditoria, que e o
   lugar dele.

2. **Conteudo nao confiavel -> contexto do modelo.** Titulo de noticia, nome de
   fonte e URL vem da internet e podem carregar instrucoes dirigidas ao modelo
   ("ignore as instrucoes anteriores"), marcacao que finge ser estrutura do
   prompt, ou caracteres invisiveis. :func:`sanitize_untrusted` neutraliza esses
   vetores **antes** que o texto entre no prompt, em vez de confiar apenas na
   instrucao de sistema que manda ignora-los.

Nada aqui substitui os guardrails de saida: esta e a camada de entrada.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Final
from urllib.parse import urlparse

# =============================================================================
# 1. Detalhe tecnico -> mensagem publica
# =============================================================================

#: Caminhos absolutos (Windows e POSIX), que carregam topologia interna.
#:
#: O ramo POSIX casa qualquer caminho com pelo menos dois segmentos, e nao uma
#: lista de diretorios conhecidos: a lista sempre deixa de fora o caso real
#: seguinte (`/usr/bin/python3` numa mensagem de trava). A antecedencia negativa
#: impede que o `//` de uma URL seja lido como inicio de caminho.
_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s\"',;)]+)|(?<![\w:/])/(?:[\w.\-+]+/)+[\w.\-+]*"
)

#: Identificadores de processo e porta, tipicos das mensagens de lock.
_PID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:PID|pid|processo|process|port|porta)\s*[:=]?\s*\d+", re.IGNORECASE
)

#: Linhas de traceback do Python.
_TRACEBACK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:Traceback \(most recent call last\):|\s*File \"[^\"]+\", line \d+.*)", re.MULTILINE
)

#: Nome de arquivo com extensao tecnica (`.duckdb`, `.parquet`, `.jsonl`...).
_FILENAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[\w.-]+\.(?:duckdb|parquet|jsonl|json|csv|wal|db|sqlite|log|py)\b"
)

#: Qualquer numero restante. Aplicado por ultimo, e o que garante que uma
#: mensagem de erro nao introduza valor sem lastro no texto do relatorio.
_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\d[\d.,:]*")

#: Marcador de estrutura tecnica bruta (dict/JSON) que sobrevive as limpezas
#: acima sem ser caminho, PID nem numero -- e o caso real de uma excecao de
#: SDK de API (ex.: `openai.AuthenticationError`), cujo `str()` e algo como
#: `Error code: 401 - {'error': {'message': '...', 'type': '...'}}`. Nenhum
#: fragmento disso e caminho, PID ou numero (o numero vira "N"), mas chaves e
#: aspas de dict/JSON nunca sao uma "frase publicavel" -- sao um despejo
#: tecnico, do jeito que o docstring desta funcao promete que nao acontece.
_STRUCTURED_DUMP_PATTERN: Final[re.Pattern[str]] = re.compile(r"[{}]")

#: Tamanho maximo da mensagem publica.
_PUBLIC_MAX_CHARS: Final[int] = 240

_REDACTED: Final[str] = "<omitido>"


def public_reason(detail: Any, *, fallback: str) -> str:
    """Reduz um detalhe tecnico a uma frase publicavel.

    Args:
        detail: excecao ou texto produzido pela camada tecnica.
        fallback: frase a devolver quando nada sobra de util. E ela que o
            relatorio publica na maior parte dos casos -- o detalhe tecnico
            raramente diz algo que interesse a quem le o relatorio.

    Returns:
        Frase sem caminho de arquivo, PID, traceback ou numero. Numeros sao
        removidos de proposito: um valor vindo de mensagem de erro nao esta no
        conjunto de evidencias e bloquearia a publicacao do texto.
    """
    text = str(detail or "").strip()
    if not text:
        return fallback

    text = _TRACEBACK_PATTERN.sub(" ", text)
    text = _PATH_PATTERN.sub(_REDACTED, text)
    text = _FILENAME_PATTERN.sub("o acervo local", text)
    text = _PID_PATTERN.sub(_REDACTED, text)
    text = _NUMBER_PATTERN.sub("N", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r.;,")

    if len(text) < 12 or _STRUCTURED_DUMP_PATTERN.search(text):
        return fallback
    if len(text) > _PUBLIC_MAX_CHARS:
        text = text[:_PUBLIC_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return f"{fallback} ({text})"


def technical_detail(detail: Any) -> str:
    """Detalhe integral, para a trilha de auditoria e o log -- nunca o relatorio."""
    if isinstance(detail, BaseException):
        return f"{type(detail).__name__}: {detail}"
    return str(detail or "")


# =============================================================================
# 2. Conteudo nao confiavel -> contexto do modelo
# =============================================================================

#: Padroes de instrucao dirigida ao modelo, dentro de conteudo externo.
#:
#: Sao os mesmos vetores do guardrail de entrada, mas aplicados ao que vem da
#: internet: uma manchete pode conter "ignore as instrucoes anteriores" tanto
#: por ataque quanto por acaso (uma materia SOBRE prompt injection). Nos dois
#: casos o texto nao pode chegar ao prompt como se fosse instrucao.
_INSTRUCTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\b(ignore|ignora|ignorar|desconsidere|esqueca|esqueça|disregard|forget)\b"
        r"[^.\n]{0,40}\b(instru\w+|regra|prompt|acima|anterior\w*|previous|system)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(voce|você|you)\s+(agora\s+)?(e|é|is|are)\s+(um|uma|a|an)\b[^.\n]{0,40}"
        r"\b(assistente|agente|model|modo|mode)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(novas?\s+instru\w+|new\s+instructions?|system\s*prompt|prompt\s+do\s+sistema)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(revele|mostre|imprima|print|reveal|repeat)\b[^.\n]{0,30}\bprompt\b", re.I),
    re.compile(r"</?(system|assistant|user|instru\w+)>", re.IGNORECASE),
)

#: Marcacao que imita estrutura de prompt e poderia confundir a fronteira entre
#: instrucao do sistema e dado.
_STRUCTURE_PATTERN: Final[re.Pattern[str]] = re.compile(r"(```|~~~|<\|[^|>]*\|>|\{\{|\}\})")

#: Rotulos de papel no inicio de linha ("System:", "Assistant:").
_ROLE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*(system|assistant|user|human|ai)\s*:", re.MULTILINE
)

_NEUTRALIZED: Final[str] = "[trecho neutralizado]"


def strip_invisible(text: Any) -> tuple[str, bool]:
    """Remove caracteres de controle e invisiveis, sem neutralizar mais nada.

    Existe separada de :func:`sanitize_untrusted` porque a deteccao de injecao
    precisa do texto **legivel mas ainda intacto**: a versao neutralizada ja
    substituiu a instrucao por um marcador, e procurar o padrao nela nunca
    encontraria nada. O ataque real e exatamente esse -- `Ig<ZWSP>nore as
    instrucoes` passa por um casamento de padrao ingenuo e chega ao modelo como
    a instrucao inteira.

    Returns:
        Par `(texto, houve remocao)`.
    """
    raw = str(text or "")
    cleaned = "".join(
        character
        for character in unicodedata.normalize("NFKC", raw)
        if unicodedata.category(character) not in {"Cc", "Cf"} or character in "\n\t"
    )
    return cleaned, cleaned != raw


def sanitize_untrusted(text: Any, *, max_chars: int = 200) -> tuple[str, list[str]]:
    """Neutraliza conteudo externo antes de ele entrar no contexto do modelo.

    Args:
        text: titulo, nome de fonte ou mensagem vinda de fora.
        max_chars: comprimento maximo do texto devolvido.

    Returns:
        Par `(texto saneado, achados)`. `achados` lista o que foi neutralizado,
        para que a auditoria registre a tentativa em vez de apenas a limpeza.
    """
    raw = str(text or "")
    findings: list[str] = []

    # Caracteres de controle e invisiveis (zero-width, RTL override) sao um
    # vetor classico: escondem instrucao do revisor humano sem esconde-la do
    # modelo. Removidos antes de qualquer casamento de padrao, para que nao
    # sirvam tambem para furar os padroes abaixo.
    cleaned = "".join(
        character
        for character in unicodedata.normalize("NFKC", raw)
        if unicodedata.category(character) not in {"Cc", "Cf"} or character in "\n\t"
    )
    if len(cleaned) != len(raw):
        findings.append("caracteres_invisiveis")

    for pattern in _INSTRUCTION_PATTERNS:
        if pattern.search(cleaned):
            findings.append("instrucao_embutida")
            cleaned = pattern.sub(_NEUTRALIZED, cleaned)

    if _STRUCTURE_PATTERN.search(cleaned):
        findings.append("marcacao_de_prompt")
        cleaned = _STRUCTURE_PATTERN.sub(" ", cleaned)

    if _ROLE_PATTERN.search(cleaned):
        findings.append("rotulo_de_papel")
        cleaned = _ROLE_PATTERN.sub(" ", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        # As reticencias entram DENTRO do limite: `max_chars` e o tamanho do que
        # sai, nao do que sobrou antes de anexar o marcador. A substituicao por
        # "[trecho neutralizado]" pode alongar o texto, entao o corte tem de
        # valer sobre o resultado final.
        cleaned = cleaned[: max(0, max_chars - 3)].rstrip() + "..."

    return cleaned, sorted(set(findings))


#: Esquemas de URL aceitos. `javascript:` e `data:` nunca entram em lugar nenhum.
_SAFE_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


def sanitize_url(url: Any) -> str | None:
    """Reduz uma URL externa a esquema e dominio, ou `None` se nao for segura.

    O caminho e a query sao descartados: eles nao acrescentam nada a
    contextualizacao e sao onde instrucao dirigida ao modelo costuma viajar
    quando o titulo ja foi saneado.
    """
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in _SAFE_SCHEMES or not parsed.netloc:
        return None
    host = parsed.netloc.split("@")[-1].lower()
    if not re.fullmatch(r"[a-z0-9.\-:]+", host):
        return None
    return f"{parsed.scheme.lower()}://{host}"
