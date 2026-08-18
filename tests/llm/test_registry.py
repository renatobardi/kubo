"""Tests for the model capability registry (KUBO-221 / ADR-0054 §III).

The registry encodes "does this model accept X?" as a provider fact, identical
in every tenant. Unknown models never block: they get the safe minimum so calls
keep working with `model` + `max_tokens` + `timeout`.
"""

from __future__ import annotations

import pytest

from kubo.llm.registry import get_capabilities, get_ignored_params


def test_unknown_model_returns_safe_minimal_capabilities() -> None:
    """Unknown models resolve to conservative capabilities and never fail."""
    caps = get_capabilities("provider/model-not-yet-registered")

    assert not caps.supports_temperature
    assert not caps.supports_reasoning_effort


@pytest.mark.parametrize(
    ("model", "supports_temperature", "supports_reasoning_effort"),
    [
        ("anthropic/claude-haiku-4-5", True, False),
        ("anthropic/claude-sonnet-5", False, False),
        ("anthropic/claude-opus-5", False, False),
        ("groq/llama-3.3-70b-versatile", True, False),
    ],
)
def test_known_model_caps_match_provider_facts(
    model: str, supports_temperature: bool, supports_reasoning_effort: bool
) -> None:
    """Known models are classified by explicit provider facts, not by prefix."""
    caps = get_capabilities(model)
    assert caps.supports_temperature is supports_temperature
    assert caps.supports_reasoning_effort is supports_reasoning_effort


def test_unknown_model_ignores_optional_params() -> None:
    """Unknown models safely ignore optional persona fields."""
    ignored = get_ignored_params(
        "provider/unknown", temperature=0.0, reasoning_effort="high"
    )

    assert {i["field"] for i in ignored} == {"temperature", "reasoning_effort"}
    assert all("does not support" in i["reason"].lower() for i in ignored)


def test_haiku_ignores_reasoning_but_keeps_temperature() -> None:
    """Haiku supports temperature but not reasoning effort."""
    ignored = get_ignored_params(
        "anthropic/claude-haiku-4-5", temperature=0.0, reasoning_effort="high"
    )

    assert [i["field"] for i in ignored] == ["reasoning_effort"]


def test_groq_keeps_temperature_ignores_reasoning() -> None:
    """Groq supports temperature but not reasoning effort."""
    ignored = get_ignored_params(
        "groq/llama-3.3-70b-versatile", temperature=0.0, reasoning_effort="high"
    )

    assert [i["field"] for i in ignored] == ["reasoning_effort"]


def test_unset_params_never_reported_as_ignored() -> None:
    """Only configured persona fields are reported; omitted (None) fields are ignored."""
    ignored = get_ignored_params(
        "anthropic/claude-opus-5", temperature=0.0, reasoning_effort=None
    )

    assert [i["field"] for i in ignored] == ["temperature"]
