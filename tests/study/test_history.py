"""KUBO-168 — Janela deslizante com resumo para histórico de chat (ADR-0047 Emenda 4).

Unit puro: o summarizer é mockado. A janela deslizante resume os turnos
antigos em vez de truncá-los — preserva o contexto sem estourar o prompt.
"""

from __future__ import annotations

from kubo.study.history import sliding_window_history

# Teto da janela recente (turnos que ficam inteiros).
_WINDOW_CHARS = 4_000


def _history(n: int, *, chars_per_turn: int = 100) -> list[tuple[str, str]]:
    """Gera n turnos de `chars_per_turn` caracteres cada."""
    return [
        ("user" if i % 2 == 0 else "assistant", f"Turno {i}: " + "x" * (chars_per_turn - 10))
        for i in range(n)
    ]


class _FakeSummarizer:
    """Fake que devolve um resumo fixo ou None (simula falha de LLM)."""

    def __init__(self, summary: str | None = "Resumo da conversa anterior.") -> None:
        self._summary = summary
        self.calls: list[str] = []

    def summarize_conversation(self, transcript: str) -> str | None:
        self.calls.append(transcript)
        return self._summary


def test_short_history_passes_through() -> None:
    """Histórico curto (cabe na janela) devolve intacto — sem resumir."""
    history = _history(5)  # 500 chars total, bem abaixo da janela
    summarizer = _FakeSummarizer()
    factory_calls = 0

    def _factory() -> _FakeSummarizer:
        nonlocal factory_calls
        factory_calls += 1
        return summarizer

    result = sliding_window_history(history, _factory, window_chars=_WINDOW_CHARS)
    assert result == history
    assert summarizer.calls == []  # não chamou o summarizer
    assert factory_calls == 0  # factory não invocada (histórico curto)


def test_long_history_summarizes_old_turns() -> None:
    """Histórico longo: turnos antigos viram resumo, recentes ficam inteiros."""
    history = _history(100)  # 10_000 chars, bem acima da janela de 4_000
    summarizer = _FakeSummarizer(summary="Dono quer estudar agentes.")

    result = sliding_window_history(history, lambda: summarizer, window_chars=_WINDOW_CHARS)
    # O primeiro turno é o resumo.
    assert result[0] == ("system", "Resumo da conversa anterior: Dono quer estudar agentes.")
    # Os turnos recentes estão inteiros (não truncados).
    assert len(result) > 1
    # O resumo foi chamado com os turnos antigos.
    assert len(summarizer.calls) == 1
    assert "Turno 0" in summarizer.calls[0]


def test_long_history_fallback_on_summary_failure() -> None:
    """Se o resumo falha (None), fallback para truncamento (turnos antigos saem)."""
    history = _history(100)
    summarizer = _FakeSummarizer(summary=None)

    result = sliding_window_history(history, lambda: summarizer, window_chars=_WINDOW_CHARS)
    # Sem resumo, os turnos recentes ficam (truncado do início).
    assert len(result) < len(history)
    # Os turnos mais recentes estão presentes.
    assert result[-1] == history[-1]


def test_empty_history_returns_empty() -> None:
    """Histórico vazio devolve vazio."""
    summarizer = _FakeSummarizer()
    result = sliding_window_history([], lambda: summarizer, window_chars=_WINDOW_CHARS)
    assert result == []
