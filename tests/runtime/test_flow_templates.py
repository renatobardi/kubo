"""Loader do catálogo `flow_templates` (unit, ADR-0016 §I).

O template é DADO, nunca DSL: `extra="forbid"` em cada nível é o mecanismo que
rejeita a lista negativa (verbos, condicionais, herança, dotted-paths). Cobre o
schema (board com states+transitions, cast, deliverable, triggers), a validação
de que todo endpoint de transição está em `states`, e a rejeição de cada categoria
proibida. Sem SurrealDB — tudo unit. O `analysis.yaml` versionado é a prova.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kubo.errors import ConfigError
from kubo.runtime.flow_templates import FlowTemplate, load_flow_template, load_flow_templates

_CATALOG = Path(__file__).parents[2] / "catalogs" / "flow_templates"


def test_load_analysis_from_real_catalog() -> None:
    """O `analysis.yaml` versionado carrega com a forma fixada do plano 0013."""
    template = load_flow_template(_CATALOG / "analysis.yaml")

    assert template.name == "analysis"
    assert template.version == 1
    assert template.board.states == ["created", "analyzing", "delivered", "failed"]
    assert ["created", "analyzing"] in [list(t) for t in template.board.transitions]
    assert template.cast == ["analista", "humano"]
    assert template.deliverable == "report"
    assert template.triggers == ["manual"]


def test_load_flow_templates_indexes_by_name() -> None:
    """load_flow_templates devolve {name: FlowTemplate}; o catálogo real tem analysis."""
    catalog = load_flow_templates(_CATALOG)

    assert "analysis" in catalog
    assert isinstance(catalog["analysis"], FlowTemplate)


def _write(tmp_path: Path, body: str) -> Path:
    """Escreve um template mínimo válido, com `body` extra concatenado (para injetar
    campos proibidos), e devolve o caminho."""
    base = (
        "name: t\nversion: 1\n"
        "board:\n  states: [a, b]\n  transitions: [[a, b]]\n"
        "cast: [analista]\ndeliverable: report\ntriggers: [manual]\n"
    )
    path = tmp_path / "t.yaml"
    path.write_text(base + body, encoding="utf-8")
    return path


def test_rejects_verb_on_state(tmp_path: Path) -> None:
    """Verbo por estado (`on_enter`/`actions`/`steps`/`run`) = workflow engine → rejeitado.
    Aqui via campo espúrio no topo, que `extra="forbid"` barra."""
    path = _write(tmp_path, "on_enter: do_stuff\n")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_rejects_conditional(tmp_path: Path) -> None:
    """Condicional (`when`/`if`) — quem decide transicionar é o runtime, não o YAML."""
    path = _write(tmp_path, "when: something\n")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_rejects_inheritance(tmp_path: Path) -> None:
    """Herança/composição (`extends`) — repetição em catálogo é feature, não dívida."""
    path = _write(tmp_path, "extends: base\n")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_rejects_dotted_path_handler(tmp_path: Path) -> None:
    """Dotted-path (`handler: kubo.workers...`) = registry dinâmico = DSL disfarçada."""
    path = _write(tmp_path, "handler: kubo.workers.analyst\n")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_rejects_transition_endpoint_not_in_states(tmp_path: Path) -> None:
    """Toda ponta de transição deve estar em `states` — um destino fantasma é config quebrada."""
    body = (
        "name: t\nversion: 1\n"
        "board:\n  states: [a, b]\n  transitions: [[a, c]]\n"
        "cast: [analista]\ndeliverable: report\ntriggers: [manual]\n"
    )
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_rejects_board_extra_field(tmp_path: Path) -> None:
    """`extra="forbid"` vale DENTRO do board também: um verbo aninhado no board é barrado."""
    body = (
        "name: t\nversion: 1\n"
        "board:\n  states: [a, b]\n  transitions: [[a, b]]\n  on_enter: x\n"
        "cast: [analista]\ndeliverable: report\ntriggers: [manual]\n"
    )
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_analysis_has_no_gates(tmp_path: Path) -> None:
    """`gates` tem default vazio (ADR-0018 §II): o `analysis` legado não declara gate,
    e um snapshot antigo sem a chave deve re-hidratar — logo o campo nunca é required."""
    template = load_flow_template(_CATALOG / "analysis.yaml")
    assert template.board.gates == []


def test_load_analysis_review_from_real_catalog() -> None:
    """O `analysis-review.yaml` versionado declara os DOIS pares gated (entrega E
    rejeição — motivo obrigatório na rejeição exige o par [awaiting_review, rejected])."""
    template = load_flow_template(_CATALOG / "analysis-review.yaml")

    assert template.name == "analysis-review"
    assert "awaiting_review" in template.board.states
    assert "rejected" in template.board.states
    gates = {tuple(g) for g in template.board.gates}
    assert gates == {("awaiting_review", "delivered"), ("awaiting_review", "rejected")}


def test_rejects_gate_not_in_transitions(tmp_path: Path) -> None:
    """`gates ⊆ transitions` (ADR-0018 §II): um par de gate fora das transições é config
    quebrada — o gate marcaria uma travessia que o board nem permite."""
    body = (
        "name: t\nversion: 1\n"
        "board:\n  states: [a, b]\n  transitions: [[a, b]]\n  gates: [[b, a]]\n"
        "cast: [analista]\ndeliverable: report\ntriggers: [manual]\n"
    )
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_flow_template(path)


def test_load_flow_templates_rejects_duplicate_name(tmp_path: Path) -> None:
    """Dois templates com o mesmo `name` falham alto — o binding do FLOW_REGISTRY é por nome."""
    _write(tmp_path, "")
    (tmp_path / "u.yaml").write_text(
        "name: t\nversion: 2\nboard:\n  states: [a]\n  transitions: []\n"
        "cast: [analista]\ndeliverable: report\ntriggers: [manual]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="'t'"):
        load_flow_templates(tmp_path)
