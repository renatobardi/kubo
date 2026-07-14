"""Contrato de comportamento da store de execução: flow/task/persona/deliverable
(integração, SurrealDB real — ADR-0016 §II/§III).

O CORAÇÃO da sessão é o teste honesto do invariante 4 (R5): instancia um flow do
YAML → REESCREVE o arquivo com outra máquina de estados → recarrega o catálogo →
prova que o flow VIVO obedece o snapshot ANTIGO, nunca o catálogo. Mutação de
objeto em memória seria teatro — o teste escreve o arquivo de verdade.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from kubo.errors import ConfigError, StateError
from kubo.runtime.flow_templates import load_flow_template
from kubo.runtime.personas import load_personas
from kubo.store import client, knowledge, migrations
from kubo.store.flows import (
    create_task,
    decide_gate,
    insert_deliverable,
    instantiate_flow,
    read_gate_context,
    set_task_run,
    transition_task,
)
from kubo.store.knowledge import Chunk

pytestmark = pytest.mark.integration

_FLOWS_DB = "test_flows"

_ANALYSIS_V1 = (
    "name: analysis\nversion: 1\n"
    "board:\n  states: [created, analyzing, delivered, failed]\n"
    "  transitions:\n    - [created, analyzing]\n    - [analyzing, delivered]\n"
    "    - [analyzing, failed]\n"
    "cast: [analista, humano]\ndeliverable: report\ntriggers: [manual]\n"
)

# Mesma máquina renomeada: 'delivered' some, 'archived' entra. Um flow vivo
# instanciado da v1 NÃO pode enxergar 'archived' nem perder 'delivered'.
_ANALYSIS_V2 = (
    "name: analysis\nversion: 2\n"
    "board:\n  states: [created, analyzing, archived, failed]\n"
    "  transitions:\n    - [created, analyzing]\n    - [analyzing, archived]\n"
    "    - [analyzing, failed]\n"
    "cast: [analista, humano]\ndeliverable: report\ntriggers: [manual]\n"
)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_FLOWS_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_FLOWS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_FLOWS_DB};")


_PERSONAS = load_personas(Path(__file__).parents[2] / "catalogs" / "personas")


def _instantiate(db: Any, template_yaml: str, tmp_path: Path) -> Any:
    """Escreve `template_yaml` num arquivo, carrega, e instancia um flow dele."""
    path = tmp_path / "analysis.yaml"
    path.write_text(template_yaml, encoding="utf-8")
    template = load_flow_template(path)
    return instantiate_flow(db, template=template, personas=_PERSONAS, question="q?")


def test_instantiate_freezes_the_snapshot_and_materializes_personas(
    db: Any, tmp_path: Path
) -> None:
    """instantiate_flow congela o snapshot integral e materializa uma persona por
    membro do elenco, com config frozen + catalog_name de proveniência."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)

    snap = db.query("SELECT VALUE snapshot FROM $f;", {"f": inst.flow})[0]
    assert snap["name"] == "analysis"
    assert snap["board"]["states"] == ["created", "analyzing", "delivered", "failed"]
    assert [list(t) for t in snap["board"]["transitions"]] == [
        ["created", "analyzing"],
        ["analyzing", "delivered"],
        ["analyzing", "failed"],
    ]
    # Ambas as personas do elenco materializadas (humano incluso — D33).
    assert set(inst.personas) == {"analista", "humano"}
    analista = db.query("SELECT * FROM $p;", {"p": inst.personas["analista"]})[0]
    assert analista["catalog_name"] == "analista"
    assert analista["executor"] == "api"
    assert "telegram" in analista["permissions"]


def test_honest_invariant_4_live_flow_obeys_frozen_snapshot_not_catalog(
    db: Any, tmp_path: Path
) -> None:
    """R5, o coração: instancia da v1, transiciona created→analyzing, REESCREVE o
    YAML para a v2 (delivered vira archived), recarrega — e prova que o flow vivo:
    (a) AINDA aceita analyzing→delivered (a transição do snapshot antigo), e
    (b) REJEITA analyzing→archived (a transição só do catálogo novo)."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    transition_task(db, task, from_state="created", to_state="analyzing")

    # Reescreve o arquivo e recarrega o catálogo — o objeto vivo não pode mudar.
    reloaded = load_flow_template(_write(tmp_path, _ANALYSIS_V2))
    assert ["analyzing", "archived"] in [list(t) for t in reloaded.board.transitions]

    # (b) o catálogo novo permite archived, mas o snapshot congelado não → rejeita.
    with pytest.raises(StateError):
        transition_task(db, task, from_state="analyzing", to_state="archived")

    # (a) o snapshot congelado ainda permite delivered → aceita.
    transition_task(db, task, from_state="analyzing", to_state="delivered")
    assert db.query("SELECT VALUE state FROM $t;", {"t": task})[0] == "delivered"


def test_transition_rejects_pair_not_in_snapshot(db: Any, tmp_path: Path) -> None:
    """Uma transição fora das declaradas no snapshot (created→delivered pula analyzing)
    levanta StateError — o par é validado contra o snapshot congelado."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    with pytest.raises(StateError):
        transition_task(db, task, from_state="created", to_state="delivered")


def test_transition_rejects_wrong_from_state(db: Any, tmp_path: Path) -> None:
    """from_state que não bate com o estado atual do task levanta StateError (guarda
    contra dupla-transição / chamada com premissa errada)."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    with pytest.raises(StateError):
        transition_task(db, task, from_state="analyzing", to_state="delivered")


def test_instantiate_rejects_cast_persona_absent_from_catalog(db: Any, tmp_path: Path) -> None:
    """Elenco que referencia persona inexistente no catálogo falha alto — não
    materializa um flow meio-formado."""
    bad = _ANALYSIS_V1.replace("cast: [analista, humano]", "cast: [analista, fantasma]")
    with pytest.raises(ConfigError, match="fantasma"):
        _instantiate(db, bad, tmp_path)


def test_create_task_wires_belongs_to_and_assigned_to(db: Any, tmp_path: Path) -> None:
    """create_task cria o task com estado inicial e liga belongs_to->flow e
    assigned_to->persona (as arestas que a proveniência exige)."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")

    flow_of = db.query("SELECT VALUE ->belongs_to->flow FROM $t;", {"t": task})[0]
    assert flow_of[0] == inst.flow
    persona_of = db.query("SELECT VALUE ->assigned_to->persona FROM $t;", {"t": task})[0]
    assert persona_of[0] == inst.personas["analista"]


def test_set_task_run_links_task_to_run(db: Any, tmp_path: Path) -> None:
    """set_task_run grava task.run apontando para o run que a executou (auditoria)."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    run = knowledge.start_run(db, worker="analista")
    set_task_run(db, task, run)
    assert db.query("SELECT VALUE run FROM $t;", {"t": task})[0] == run


def test_insert_deliverable_wires_produces_and_consults(db: Any, tmp_path: Path) -> None:
    """insert_deliverable grava o deliverable (kind report + markdown), a aresta
    flow->produces->deliverable e uma aresta task->consults->distilled por fonte
    recuperada — tudo atômico."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    d1 = db.query("CREATE distilled SET summary = 'a';")[0]["id"]
    d2 = db.query("CREATE distilled SET summary = 'b';")[0]["id"]

    deliverable = insert_deliverable(
        db, flow=inst.flow, task=task, kind="report", content="# Relatório", consulted=[d1, d2]
    )

    body = db.query("SELECT kind, content FROM $d;", {"d": deliverable})[0]
    assert body["kind"] == "report"
    assert body["content"] == "# Relatório"
    produced = db.query("SELECT VALUE ->produces->deliverable FROM $f;", {"f": inst.flow})[0]
    assert deliverable in produced
    consulted = db.query("SELECT VALUE ->consults->distilled FROM $t;", {"t": task})[0]
    assert {str(x) for x in consulted} == {str(d1), str(d2)}


_ANALYSIS_REVIEW = (
    "name: analysis-review\nversion: 1\n"
    "board:\n"
    "  states: [created, analyzing, awaiting_review, delivered, rejected, failed]\n"
    "  transitions:\n"
    "    - [created, analyzing]\n    - [analyzing, awaiting_review]\n"
    "    - [awaiting_review, delivered]\n    - [awaiting_review, rejected]\n"
    "    - [analyzing, failed]\n"
    "  gates:\n    - [awaiting_review, delivered]\n    - [awaiting_review, rejected]\n"
    "cast: [analista, humano]\ndeliverable: report\ntriggers: [manual]\n"
)


def _review_flow(db: Any, tmp_path: Path) -> tuple[Any, Any, Any]:
    """Instancia um flow `analysis-review` e o leva até o gate aberto: a task da analista
    em `awaiting_review` + a task do gate (do humano) nascida em `awaiting_review`. Devolve
    (inst, analyst_task, gate_task) — o estado de partida das decisões de gate."""
    inst = _instantiate(db, _ANALYSIS_REVIEW, tmp_path)
    analyst = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    transition_task(db, analyst, from_state="created", to_state="analyzing")
    transition_task(db, analyst, from_state="analyzing", to_state="awaiting_review")
    gate = create_task(db, flow=inst.flow, persona=inst.personas["humano"], state="awaiting_review")
    return inst, analyst, gate


def test_transition_task_refuses_gate_pair(db: Any, tmp_path: Path) -> None:
    """A guarda de gate (ADR-0018 §II): `transition_task` NUNCA atravessa um par gated —
    levanta StateError apontando `decide_gate`, mesmo o par estando nas transições."""
    _, analyst, _ = _review_flow(db, tmp_path)
    with pytest.raises(StateError, match="decide_gate"):
        transition_task(db, analyst, from_state="awaiting_review", to_state="delivered")


def test_decide_gate_approves_both_tasks_atomically(db: Any, tmp_path: Path) -> None:
    """Aprovar move AS DUAS tasks (analista + gate) para `delivered` numa transação e grava
    a decisão na task do gate (decision/decided_at). Lê de volta — o footgun do no-op
    silencioso só morre lendo o estado persistido (ADR-0018 §I/§IV)."""
    _, analyst, gate = _review_flow(db, tmp_path)
    decide_gate(db, analyst_task=analyst, gate_task=gate, to_state="delivered", decision="approved")
    assert db.query("SELECT VALUE state FROM $t;", {"t": analyst})[0] == "delivered"
    g = db.query("SELECT state, decision, reason, decided_at FROM $t;", {"t": gate})[0]
    assert g["state"] == "delivered"
    assert g["decision"] == "approved"
    assert g["decided_at"] is not None


def test_decide_gate_rejects_with_reason(db: Any, tmp_path: Path) -> None:
    """Rejeitar move as duas tasks para `rejected` e grava o motivo obrigatório na task
    do gate — a decisão vira registro no grafo, não só efeito de UI."""
    _, analyst, gate = _review_flow(db, tmp_path)
    decide_gate(
        db,
        analyst_task=analyst,
        gate_task=gate,
        to_state="rejected",
        decision="rejected",
        reason="fontes fracas",
    )
    assert db.query("SELECT VALUE state FROM $t;", {"t": analyst})[0] == "rejected"
    g = db.query("SELECT state, decision, reason FROM $t;", {"t": gate})[0]
    assert g["state"] == "rejected"
    assert g["decision"] == "rejected"
    assert g["reason"] == "fontes fracas"


def test_decide_gate_rejection_requires_reason(db: Any, tmp_path: Path) -> None:
    """Motivo obrigatório na rejeição (nunca cortável): rejeitar sem motivo (ou só espaços)
    levanta StateError e NÃO transiciona nada."""
    _, analyst, gate = _review_flow(db, tmp_path)
    with pytest.raises(StateError):
        decide_gate(
            db,
            analyst_task=analyst,
            gate_task=gate,
            to_state="rejected",
            decision="rejected",
            reason="   ",
        )
    assert db.query("SELECT VALUE state FROM $t;", {"t": analyst})[0] == "awaiting_review"


def _seed_distilled(db: Any, title: str) -> Any:
    """Semeia um distilled com um item titulado (via derived_from) — para read_gate_context
    resolver o título da fonte."""
    src = knowledge.upsert_source(db, kind="rss", canonical=f"src::{title}")
    item = knowledge.upsert_item(
        db, source=src, external_id=f"ext::{title}", content="x", title=title
    )
    chunk = Chunk(text="s", seq=0, embedding=[0.1] * 768, model="m", dim=768, task_type="X")
    return knowledge.insert_distilled(db, item=item, summary="s", chunks=[chunk])


def test_read_gate_context_gathers_flow_deliverable_and_sources(db: Any, tmp_path: Path) -> None:
    """read_gate_context (ADR-0018 §I/§V): a partir do task do gate reúne flow, pergunta, o
    task da analista, a PROSA do deliverable e as fontes consultadas (id+título) — a leitura
    única que serve o painel de gate E o re-render do Telegram na aprovação."""
    inst, analyst, gate = _review_flow(db, tmp_path)
    d1 = _seed_distilled(db, "Rust ownership")
    d2 = _seed_distilled(db, "GC tradeoffs")
    insert_deliverable(
        db, flow=inst.flow, task=analyst, kind="report", content="A análise.", consulted=[d1, d2]
    )

    ctx = read_gate_context(db, gate)

    assert ctx is not None
    assert ctx.flow == inst.flow
    assert ctx.analyst_task == analyst
    assert ctx.gate_task == gate
    assert ctx.question == "q?"
    assert ctx.content == "A análise."
    assert {s.id for s in ctx.sources} == {str(d1), str(d2)}
    assert {s.title for s in ctx.sources} == {"Rust ownership", "GC tradeoffs"}


def test_read_gate_context_none_for_orphan_task(db: Any) -> None:
    """Um task sem flow (belongs_to ausente) → None, nunca crash (registro órfão/manual)."""
    orphan = db.query("CREATE task SET state = 'awaiting_review';")[0]["id"]
    assert read_gate_context(db, orphan) is None


def _write(tmp_path: Path, body: str) -> Path:
    """Escreve `body` em analysis.yaml no tmp e devolve o caminho (reescrita do R5)."""
    path = tmp_path / "analysis.yaml"
    path.write_text(body, encoding="utf-8")
    return path
