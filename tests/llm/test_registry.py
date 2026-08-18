"""Tests for the model capability registry (KUBO-221 / ADR-0054 §III).

The registry encodes "does this model accept X?" as a provider fact, identical
in every tenant. Unknown models never block: they get the safe minimum so calls
keep working with `model` + `max_tokens` + `timeout`.
"""

from __future__ import annotations

import pytest

from kubo.llm.registry import get_capabilities


def test_unknown_model_returns_safe_minimal_capabilities() -> None:
    """Unknown models resolve to conservative capabilities and never fail."""
    caps = get_capabilities("provider/model-not-yet-registered")

    assert not caps.supports_temperature
    assert not caps.supports_reasoning_effort


@pytest.mark.parametrize(
    ("model", "supports_temperature"),
    [
        ("anthropic/claude-haiku-4-5", True),
        ("anthropic/claude-sonnet-5", False),
        ("anthropic/claude-opus-5", False),
        ("groq/llama-3.3-70b-versatile", True),
    ],
)
def test_known_model_caps_match_provider_facts(model: str, supports_temperature: bool) -> None:
    """Known models are classified by explicit provider facts, not by prefix."""
    caps = get_capabilities(model)
    assert caps.supports_temperature is supports_temperature
