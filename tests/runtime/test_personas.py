"""Loader do catálogo `personas` (unit, ADR-0016 §II).

Persona é dado declarativo (invariante 3): 1 YAML por persona, `extra="forbid"`
na borda. Cobre o schema (name/executor/model/prompt/permissions), a rejeição de
campo espúrio, executor fora do conjunto, e a indexação por nome com duplicata
falhando alto. Sem SurrealDB — tudo unit. O catálogo real (`analista`, `humano`)
é carregado como prova de que os arquivos versionados validam.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kubo.errors import ConfigError
from kubo.runtime.personas import Persona, load_persona, load_personas

_CATALOG = Path(__file__).parents[2] / "catalogs" / "personas"


def test_load_analista_from_real_catalog() -> None:
    """O `analista.yaml` versionado carrega como Persona com executor api + modelo."""
    persona = load_persona(_CATALOG / "analista.yaml")

    assert persona.name == "analista"
    assert persona.executor == "api"
    assert persona.model
    assert persona.prompt.strip()
    assert "telegram" in persona.permissions


def test_load_humano_is_a_human_without_task_machinery() -> None:
    """O `humano.yaml` carrega com executor human, sem modelo — persona sem LLM."""
    persona = load_persona(_CATALOG / "humano.yaml")

    assert persona.name == "humano"
    assert persona.executor == "human"
    assert persona.model is None


def test_load_personas_indexes_by_name() -> None:
    """load_personas devolve {name: Persona}; o catálogo real tem analista e humano."""
    catalog = load_personas(_CATALOG)

    assert {"analista", "humano"} <= set(catalog)
    assert isinstance(catalog["analista"], Persona)


def test_persona_rejects_extra_field(tmp_path: Path) -> None:
    """Campo fora do schema é rejeitado na borda (`extra="forbid"`) — uma persona não
    carrega comportamento declarado, só identidade + permissões."""
    (tmp_path / "x.yaml").write_text(
        "name: x\nexecutor: api\nmodel: m\nprompt: p\non_enter: hack\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_persona(tmp_path / "x.yaml")


def test_persona_rejects_unknown_executor(tmp_path: Path) -> None:
    """executor fora de {api, cli, human} é rejeitado (Literal fechado)."""
    (tmp_path / "x.yaml").write_text("name: x\nexecutor: magic\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_persona(tmp_path / "x.yaml")


def test_load_personas_rejects_duplicate_name(tmp_path: Path) -> None:
    """Dois YAMLs com o mesmo `name` falham alto — o catálogo deve ser inequívoco."""
    (tmp_path / "a.yaml").write_text("name: dup\nexecutor: human\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("name: dup\nexecutor: api\nmodel: m\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="dup"):
        load_personas(tmp_path)


def test_load_persona_rejects_non_mapping(tmp_path: Path) -> None:
    """YAML que não é um mapping (lista, escalar) falha como ConfigError, não crash."""
    (tmp_path / "x.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_persona(tmp_path / "x.yaml")
