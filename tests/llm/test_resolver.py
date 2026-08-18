"""Tests for the central LLM resolver (KUBO-221 / ADR-0054 §II).

The resolver translates a catalog persona into a frozen `ApiExecutorConfig`.
Unit tests patch the persona lookup; integration tests exercise the real store.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from kubo.errors import ConfigError
from kubo.executors.api import ApiExecutor
from kubo.llm.resolver import resolve_api_config
from kubo.runtime.personas import Persona


def _session(tenant_id: Any = None, user_id: Any = None) -> Any:
    """Minimal session stand-in; resolver only uses tenant_id/user_id for logging."""
    return MagicMock(tenant_id=tenant_id or MagicMock(), user_id=user_id or MagicMock())


def test_resolve_api_config_uses_persona_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolver maps persona LLM fields to ApiExecutorConfig."""
    persona = Persona(
        name="distiller",
        executor="api",
        model="anthropic/claude-haiku-4-5",
        max_tokens=16384,
        temperature=0.0,
        timeout=60.0,
    )
    monkeypatch.setattr(
        "kubo.llm.resolver.resolve_persona",
        lambda _session, _tenant, _user, name: persona,
    )

    config = resolve_api_config(_session(), "distiller")

    assert config.model == "anthropic/claude-haiku-4-5"
    assert config.max_tokens == 16384
    assert config.temperature == 0.0
    assert config.timeout == 60.0
    assert config.api_key is None


def test_resolve_api_config_returns_frozen_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolved config is immutable for the lifetime of a request/run."""
    persona = Persona(
        name="distiller",
        executor="api",
        model="anthropic/claude-haiku-4-5",
    )
    monkeypatch.setattr(
        "kubo.llm.resolver.resolve_persona",
        lambda _session, _tenant, _user, name: persona,
    )

    config = resolve_api_config(_session(), "distiller")

    with pytest.raises(ValidationError):
        config.max_tokens = 8192


def test_resolve_api_config_rejects_non_api_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only `api` personas can be resolved to an API executor config."""
    persona = Persona(
        name="humano",
        executor="human",
        model=None,
    )
    monkeypatch.setattr(
        "kubo.llm.resolver.resolve_persona",
        lambda _session, _tenant, _user, name: persona,
    )

    with pytest.raises(ConfigError, match="expected 'api'"):
        resolve_api_config(_session(), "humano")


def test_resolve_api_config_rejects_persona_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """An `api` persona without a model is not resolvable."""
    persona = Persona(
        name="broken",
        executor="api",
        model="anthropic/claude-haiku-4-5",
    )
    persona.model = None  # simulate a stored row with an empty model
    monkeypatch.setattr(
        "kubo.llm.resolver.resolve_persona",
        lambda _session, _tenant, _user, name: persona,
    )

    with pytest.raises(ConfigError, match="has no model"):
        resolve_api_config(_session(), "broken")


def test_distiller_persona_reaches_litellm_with_legacy_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolved default distiller persona reaches litellm.completion with legacy constants.

    Regression/boundary test for KUBO-221: after splitting the distiller into three
    personas, the `distiller-distill` call must keep the same model and parameters
    the old single `distiller` persona used (anthropic/claude-haiku-4-5, 16384 tokens,
    temperature=0, timeout=60). This is the LiteLLM wire boundary.
    """
    from surrealdb import RecordID

    from kubo.runtime.personas import DEFAULT_PERSONAS

    # Simulate a tenant with no catalog override: resolver falls back to the code default.
    monkeypatch.setattr(
        "kubo.runtime.personas.load_personas",
        lambda _db, _tenant, _user: {},
    )

    tenant_id = RecordID("tenant", "t1")
    user_id = RecordID("user", "u1")
    session = MagicMock(tenant_id=tenant_id, user_id=user_id)
    config = resolve_api_config(session, "distiller-distill")

    # Legacy values preserved in the resolved config.
    assert config.model == "anthropic/claude-haiku-4-5"
    assert config.max_tokens == 16384
    assert config.temperature == 0.0
    assert config.timeout == 60.0

    # Sanity: these are the values stored in the code default.
    default = next(p for p in DEFAULT_PERSONAS if p["name"] == "distiller-distill")
    assert config.model == default["model"]
    assert config.max_tokens == default["max_tokens"]
    assert config.temperature == default["temperature"]
    assert config.timeout == default["timeout"]

    # Wire boundary: litellm.completion receives the resolved parameters.
    calls: list[dict[str, Any]] = []

    def _fake_completion(**kwargs: Any) -> MagicMock:
        calls.append(kwargs)
        return MagicMock(choices=[MagicMock(message=MagicMock(content="{}"))])

    monkeypatch.setattr("kubo.executors.api.litellm.completion", _fake_completion)

    class _Out(BaseModel):
        pass

    ApiExecutor(config, max_attempts=1).complete("Instruct.", "Content.", _Out)

    assert len(calls) == 1
    assert calls[0]["model"] == "anthropic/claude-haiku-4-5"
    assert calls[0]["max_tokens"] == 16384
    assert calls[0]["timeout"] == 60.0
    assert calls[0]["temperature"] == 0.0
