"""Model capability registry — a fact about the provider, not a catalog.

Lives in `kubo/llm/` and answers: "does this model accept `temperature`?
`reasoning_effort`?". Unknown models get the safe minimum and never block a
call just because they are not listed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
                "reason": "model does not support sampling parameters",
            }
        )
    if reasoning_effort is not None and not caps.supports_reasoning_effort:
        ignored.append(
            {
                "field": "reasoning_effort",
                "reason": "model does not support reasoning effort",
            }
        )
    return ignored
