"""Central resolver for LLM configuration.

Given a scoped session and a persona name, returns a frozen `ApiExecutorConfig`
ready for the call. No other module should build an LLM call on its own
(ADR-0054 §II/§IX).
"""

from __future__ import annotations

from kubo.errors import ConfigError
from kubo.executors.api import ApiExecutorConfig
from kubo.runtime.personas import resolve_persona
from kubo.store.scoped import ScopedStore


def resolve_api_config(
    session: ScopedStore,
    persona_name: str,
) -> ApiExecutorConfig:
    """Resolve a catalog persona to a frozen `ApiExecutorConfig`.

    Raises:
        ConfigError: if the persona does not exist, is not `api`, or has no model.
    """
    persona = resolve_persona(session, session.tenant_id, session.user_id, persona_name)
    if persona.executor != "api":
        raise ConfigError(
            f"persona '{persona_name}' has executor '{persona.executor}', expected 'api'"
        )
    if not persona.model:
        raise ConfigError(f"persona '{persona_name}' has no model")

    return ApiExecutorConfig(
        model=persona.model,
        max_tokens=persona.max_tokens,
        temperature=persona.temperature,
        timeout=persona.timeout,
        api_key=None,
    )
