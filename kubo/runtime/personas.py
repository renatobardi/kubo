"""Loader do catálogo `personas` (ADR-0016 §II).

Persona é dado declarativo (invariante 3): 1 YAML por persona, sem lógica. O
loader é `extra="forbid"` na borda — uma persona carrega identidade, executor,
modelo, prompt e permissões, NUNCA comportamento declarado. É a mesma máquina do
loader de integrações (`kubo/runtime/integrations.py`), o precedente do repo para
catálogo declarativo com negação por schema.

A persona é materializada POR FLOW (snapshot congelado no grafo, ADR-0016 §II):
editar `analista.yaml` não afeta um flow vivo. Este loader só valida o catálogo;
o congelamento é da store (`instantiate_flow`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kubo.errors import ConfigError, format_validation_error

# Executores suportados: `api` (LLM via LiteLLM), `cli` (adapters, 0015) e `human`
# (persona materializada que NÃO recebe task nesta fase — D33). Literal fechado:
# um executor com nome errado é rejeitado na borda, não silenciosamente aceito.
Executor = Literal["api", "cli", "human"]


class Persona(BaseModel):
    """Uma persona do catálogo (1 YAML por arquivo).

    `model` é obrigatório para executores de LLM (`api`/`cli`) e ausente para
    `human` (uma pessoa não tem modelo). `permissions` são os nomes de integração
    que a persona pode acessar — o flow runner valida `permissions ⊇
    manifest.integrations` do worker (R6, least-privilege).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    executor: Executor
    model: str | None = None
    prompt: str = ""
    permissions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _model_matches_executor(self) -> Self:
        """`human` não tem modelo; executor de LLM (`api`/`cli`) exige um modelo não-vazio
        — um agente sem modelo não tem como executar."""
        if self.executor == "human":
            if self.model:
                raise ValueError("persona com executor 'human' não deve declarar model")
        elif not self.model:
            raise ValueError(f"persona com executor '{self.executor}' exige model")
        return self


def load_persona(path: Path) -> Persona:
    """Carrega e valida um YAML de persona; erro vira ConfigError (fronteira)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"persona {path.name}: YAML não é um mapping")
    try:
        return Persona.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"persona {path.name} inválida: {format_validation_error(exc)}") from exc


def load_personas(catalog_dir: Path) -> dict[str, Persona]:
    """Carrega todas as personas de um diretório (1 YAML por item), por nome.

    Nome duplicado entre dois arquivos falha alto (ConfigError) — nunca sobrescreve
    em silêncio: o elenco de um template referencia personas por nome, e um nome
    ambíguo materializaria a persona errada num flow."""
    catalog: dict[str, Persona] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        persona = load_persona(path)
        if persona.name in catalog:
            raise ConfigError(f"persona '{persona.name}' declarada em mais de um arquivo")
        catalog[persona.name] = persona
    return catalog
