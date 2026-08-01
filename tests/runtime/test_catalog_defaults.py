"""Modelos das personas default do catálogo (KUBO-139).

Os defaults vivem em código (ADR-0042 §III) e são semeados na criação do tenant.
Aqui só se afirma QUAL modelo cada persona nasce usando — o resto do contrato do
catálogo é do `tests/store/test_catalog.py` (integração).
"""

from __future__ import annotations

from typing import Any

from kubo.runtime.catalog_defaults import DEFAULT_PERSONAS


def _persona(name: str) -> dict[str, Any]:
    """Persona default pelo nome — KeyError explícito se sumir do catálogo."""
    for persona in DEFAULT_PERSONAS:
        if persona["name"] == name:
            return persona
    raise KeyError(name)


def test_planner_usa_claude_opus() -> None:
    """A persona `planner` (agrupar capítulos em lições) nasce no Claude Opus 5."""
    assert _persona("planner")["model"] == "anthropic/claude-opus-5"


def test_tutor_usa_claude_sonnet() -> None:
    """A persona `tutor` (escrever a lição do dia) nasce no Claude Sonnet 5."""
    assert _persona("tutor")["model"] == "anthropic/claude-sonnet-5"


def test_finder_permanece_no_groq() -> None:
    """Caso de controle: a descoberta de RSS (`finder`) NÃO muda de modelo."""
    assert _persona("finder")["model"] == "groq/llama-3.3-70b-versatile"


def test_mentor_usa_claude_haiku() -> None:
    """A persona `mentor` (sumário de Material no upload, KUBO-162) nasce no Haiku 4.5."""
    assert _persona("mentor")["model"] == "anthropic/claude-haiku-4-5"
