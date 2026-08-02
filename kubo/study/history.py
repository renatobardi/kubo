"""Janela deslizante com resumo para histórico de chat (KUBO-168, ADR-0047 Emenda 4).

Substitui a truncagem simples por uma janela deslizante: os turnos recentes
ficam inteiros, os antigos são resumidos pelo `Summarizer.summarize_conversation`.
Se o resumo falha, fallback para truncamento (turnos antigos saem).

O histórico é uma lista de (role, content). A função devolve uma lista do
mesmo tipo — o primeiro elemento pode ser ("system", "Resumo da conversa
anterior: ...") quando há resumo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

import structlog

_log = structlog.get_logger(__name__)

# Teto padrão da janela recente (turnos que ficam inteiros).
DEFAULT_WINDOW_CHARS = 20_000


def sliding_window_history(
    history: Sequence[tuple[str, str]],
    summarizer_factory: Callable[[], _SummarizerLike],
    *,
    window_chars: int = DEFAULT_WINDOW_CHARS,
) -> list[tuple[str, str]]:
    """Aplica janela deslizante com resumo aos turnos antigos.

    `summarizer_factory` é um callback lazy — só é chamado quando o histórico
    excede a janela e precisa de resumo. Evita instanciar o summarizer (com
    conexão DB + resolve_persona) quando o histórico é curto.

    Se o histórico cabe na janela, devolve intacto. Se excede, resume os
    turnos antigos em um turno ("system", "Resumo da conversa anterior: ...")
    e mantém os recentes inteiros. Se o resumo falha (None), fallback para
    truncamento — os turnos antigos saem, os recentes ficam.
    """
    if not history:
        return []
    full_text = _format_turns(history)
    if len(full_text) <= window_chars:
        return list(history)
    # Divide em antigos (para resumir) e recentes (para manter).
    recent, old = _split_at_window(history, window_chars)
    if not old:
        return recent
    old_text = _format_turns(old)
    summarizer = summarizer_factory()
    summary = summarizer.summarize_conversation(old_text)
    if summary:
        return [("system", f"Resumo da conversa anterior: {summary}"), *recent]
    _log.warning("study.history.summary_failed", old_turns=len(old), recent=len(recent))
    return recent


def _format_turns(history: Sequence[tuple[str, str]]) -> str:
    """Formata turnos como texto (role: content)."""
    return "\n".join(f"{role}: {content}" for role, content in history)


def _split_at_window(
    history: Sequence[tuple[str, str]], window_chars: int
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Divide histórico em (recentes, antigos) a partir do fim.

    Acumula turnos do fim até preencher `window_chars`. O que sobra é antigo.
    """
    recent: list[tuple[str, str]] = []
    total = 0
    for role, content in reversed(history):
        turn_len = len(f"{role}: {content}") + 1  # +1 = newline
        if total + turn_len > window_chars:
            break
        recent.insert(0, (role, content))
        total += turn_len
    split_idx = len(history) - len(recent)
    old = list(history[:split_idx])
    return recent, old


class _SummarizerLike(Protocol):
    """Protocol estrutural para o summarizer (duck typing)."""

    def summarize_conversation(self, transcript: str) -> str | None: ...
