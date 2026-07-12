"""Chunking puro de texto PT-BR para embedding (ADR-0013 §II).

Sem dependência de rede, banco ou LLM: só transformação determinística de
string. Estratégia em cascata — parágrafo → sentença → hard-split por chars —
sempre com merge guloso (greedy) e sem overlap entre chunks.
"""

import re

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    """Estima a contagem de tokens de um texto (heurística chars/4)."""
    return len(text) // 4


def chunk_text(text: str, *, max_tokens: int = 1200) -> list[str]:
    """Divide texto em chunks que cabem no limite de tokens do embedding.

    Estratégia: agrupa parágrafos por merge guloso; um parágrafo que não
    cabe sozinho é quebrado por sentença (mesma estratégia de merge); uma
    sentença que ainda não cabe é fatiada por caracteres, sem separador.
    """
    max_chars = max_tokens * 4
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if estimate_tokens(candidate) <= max_tokens:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ""
        if estimate_tokens(paragraph) <= max_tokens:
            buffer = paragraph
        else:
            chunks.extend(_split_oversized_paragraph(paragraph, max_tokens, max_chars))
    if buffer:
        chunks.append(buffer)
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """Divide texto em parágrafos por linha(s) em branco, descartando vazios."""
    parts = _PARAGRAPH_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _split_oversized_paragraph(paragraph: str, max_tokens: int, max_chars: int) -> list[str]:
    """Quebra um parágrafo maior que o teto por sentença, com merge guloso.

    Sentença que ainda excede o teto sozinha cai para hard-split por chars.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]

    chunks: list[str] = []
    buffer = ""
    for sentence in sentences:
        candidate = f"{buffer} {sentence}" if buffer else sentence
        if estimate_tokens(candidate) <= max_tokens:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ""
        if estimate_tokens(sentence) <= max_tokens:
            buffer = sentence
        else:
            chunks.extend(_hard_split(sentence, max_chars))
    if buffer:
        chunks.append(buffer)
    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Fatia texto em pedaços de max_chars, sem separador, preservando conteúdo exato."""
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
