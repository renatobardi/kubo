"""Loader do catálogo `flow_templates` (ADR-0016 §I).

O template declara FORMA (estados, transições, elenco, deliverable, gatilho);
o que acontece em cada estado é código Python keyed pelo nome (FLOW_REGISTRY).
**Template é DADO, nunca DSL** (invariante 3): `extra="forbid"` em CADA nível é o
mecanismo que rejeita a lista negativa — verbos (`on_enter`/`actions`/`steps`/
`run`), condicionais (`when`/`if`), herança (`extends`), dotted-paths
(`handler: ...`) são todos campos fora do schema, barrados na borda.

Instanciar um flow congela uma cópia deste template no grafo (`flow.snapshot`,
ADR-0016 §II) — este loader só valida o catálogo; o congelamento é da store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from kubo.errors import ConfigError, format_validation_error


class Board(BaseModel):
    """A máquina de estados do template: os estados e as transições permitidas.

    `transitions` é lista de pares `(from, to)` — a tipagem `tuple[str, str]`
    rejeita na borda uma transição que não seja exatamente um par. Um campo extra
    aqui (ex.: `on_enter` aninhado) é barrado por `extra="forbid"`: comportamento
    por estado é a DSL proibida (§I)."""

    model_config = ConfigDict(extra="forbid")

    states: list[str]
    transitions: list[tuple[str, str]]

    @model_validator(mode="after")
    def _endpoints_in_states(self) -> Self:
        """Toda ponta de transição deve estar em `states` — um destino fantasma é
        config quebrada que só apareceria como transição impossível em runtime."""
        known = set(self.states)
        for src, dst in self.transitions:
            if src not in known or dst not in known:
                raise ValueError(f"transição [{src}, {dst}] referencia estado fora de states")
        return self


class FlowTemplate(BaseModel):
    """Um template de flow do catálogo (1 YAML por arquivo).

    Enumera fatos (forma), nunca comportamento. `deliverable` é o KIND do artefato
    produzido (`report`); `cast` são nomes de persona (resolvidos no catálogo de
    personas na instanciação); `triggers` são gatilhos declarados (`manual` nesta
    fase). `extra="forbid"` fecha a lista negativa no nível do template."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int
    board: Board
    cast: list[str]
    deliverable: str
    triggers: list[str]


def load_flow_template(path: Path) -> FlowTemplate:
    """Carrega e valida um YAML de template; erro vira ConfigError (fronteira)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"flow template {path.name}: YAML não é um mapping")
    try:
        return FlowTemplate.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            f"flow template {path.name} inválido: {format_validation_error(exc)}"
        ) from exc


def load_flow_templates(catalog_dir: Path) -> dict[str, FlowTemplate]:
    """Carrega todos os templates de um diretório (1 YAML por item), por nome.

    Nome duplicado falha alto (ConfigError): o binding template→comportamento do
    FLOW_REGISTRY é por nome, e um nome ambíguo instanciaria a forma errada."""
    catalog: dict[str, FlowTemplate] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        template = load_flow_template(path)
        if template.name in catalog:
            raise ConfigError(f"flow template '{template.name}' declarado em mais de um arquivo")
        catalog[template.name] = template
    return catalog
