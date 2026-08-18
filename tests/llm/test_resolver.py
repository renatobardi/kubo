"""Tests for the central LLM resolver (KUBO-221 / ADR-0054 §II).

The resolver translates a catalog persona into a frozen `ApiExecutorConfig`.
Unit tests patch the persona lookup; integration tests exercise the real store.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from kubo.errors import ConfigError
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
