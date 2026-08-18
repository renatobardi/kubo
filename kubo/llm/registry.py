"""Model capability registry — a fact about the provider, not a catalog.

Lives in `kubo/llm/` and answers: "does this model accept `temperature`?
`reasoning_effort`?". Unknown models get the safe minimum and never block a
call just because they are not listed.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict

_log = structlog.get_logger(__name__)


class ModelCapabilities(BaseModel):
    """Declared capabilities of a model/provider.

    `extra="forbid"` keeps the registry a dictionary of well-known facts;
    adding a new capability requires an intentional change in this module.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    supports_temperature: bool = False
    supports_reasoning_effort: bool = False


# Fact about the world: which models support which parameters. The prefix of
# the model name (e.g. `anthropic/`, `groq/`) is NOT the decision rule; the
# list is explicit to avoid inferring capability from the name and failing
# silently.
_REGISTRY: dict[str, ModelCapabilities] = {
    # Haiku 4.5 still accepts sampling params; this is the documented
    # exception in the API executor, now encoded positively by the registry.
    "anthropic/claude-haiku-4-5": ModelCapabilities(supports_temperature=True),
    # Claude 4.7/5 generation dropped sampling params; sending `temperature`
    # gets a 400 from the provider.
    "anthropic/claude-sonnet-5": ModelCapabilities(),
    "anthropic/claude-opus-5": ModelCapabilities(),
    # Groq via LiteLLM follows the standard completion sampling contract.
    "groq/llama-3.3-70b-versatile": ModelCapabilities(supports_temperature=True),
}


def get_capabilities(model: str) -> ModelCapabilities:
    """Return the capabilities of `model`, or the safe minimum if unknown.

    The safe minimum omits optional parameters; the call still works with
    `model` + `max_tokens` + `timeout`.
    """
    return _REGISTRY.get(model, ModelCapabilities())


class _LiteLLMConfig(Protocol):
    """Minimal shape of a config object accepted by `build_api_call_params`.

    This keeps the registry free of a circular import with `kubo.executors.api`.
    """

    model: str
    temperature: float
    max_tokens: int
    timeout: float
    reasoning_effort: str | None
    api_key: str | None


def _reason_for(param: str) -> str:
    """Human-readable reason for omitting a parameter."""
    if param == "temperature":
        return "model does not support sampling parameters"
    if param == "reasoning_effort":
        return "model does not support reasoning effort"
    return f"model does not support {param}"


def build_api_call_params(config: _LiteLLMConfig) -> dict[str, Any]:
    """Monta os parâmetros de chamada a partir das capacidades do modelo.

    Parâmetros opcionais (`temperature`, `reasoning_effort`) são omitidos quando o
    modelo não os aceita, com log estruturado explicando a omissão. Modelos desconhecidos
    recebem o mínimo seguro: `model`, `max_tokens`, `timeout`, `api_key`.

    Esta função vive em `kubo/llm/registry.py` porque a decisão de quais
    parâmetros chegam a uma chamada de LLM pertence ao módulo autorizado
    (`kubo/llm/`, ADR-0054 §IX).
    """
    caps = get_capabilities(config.model)
    params: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "timeout": config.timeout,
        "api_key": config.api_key or None,
        "num_retries": 0,
    }

    if caps.supports_temperature:
        params["temperature"] = config.temperature
    else:
        _log.info(
            "llm.param_omitted",
            parameter="temperature",
            model=config.model,
            reason=_reason_for("temperature"),
        )

    if config.reasoning_effort is not None:
        if caps.supports_reasoning_effort:
            params["reasoning_effort"] = config.reasoning_effort
        else:
            _log.info(
                "llm.param_omitted",
                parameter="reasoning_effort",
                model=config.model,
                reason=_reason_for("reasoning_effort"),
            )

    return params


def get_ignored_params(
    model: str, *, temperature: float | None, reasoning_effort: str | None
) -> list[dict[str, str]]:
    """Return the list of persona fields that will be ignored for `model`.

    Each entry contains the field name and a human-readable reason. Unknown
    models are treated safely: optional fields are ignored.
    """
    caps = get_capabilities(model)
    ignored: list[dict[str, str]] = []
    if temperature is not None and not caps.supports_temperature:
        ignored.append(
            {
                "field": "temperature",
                "reason": _reason_for("temperature"),
            }
        )
    if reasoning_effort is not None and not caps.supports_reasoning_effort:
        ignored.append(
            {
                "field": "reasoning_effort",
                "reason": _reason_for("reasoning_effort"),
            }
        )
    return ignored
