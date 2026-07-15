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
    count_flows,
    create_task,
    decide_gate,
    flow_board,
    flow_gates,
    insert_deliverable,
    instantiate_flow,
    list_flows,
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


def test_insert_deliverable_pr_stores_typed_url_and_number(db: Any, tmp_path: Path) -> None:
    """insert_deliverable(kind="pr") grava url/number como campos ESTRUTURAIS (ADR-0019 §VI,
    migration 0007): a ref vem da API (E3), separada do `content` (resumo untrusted do agente,
    E4). Prova que os campos option<...> aceitam o PR e ficam ausentes fora dele."""
    inst = _instantiate(db, _ANALYSIS_V1, tmp_path)
    task = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")

    deliverable = insert_deliverable(
        db,
        flow=inst.flow,
        task=task,
        kind="pr",
        content="resumo do agente",
        consulted=[],
        pr_url="https://github.com/owner/kubo-forge/pull/7",
        pr_number=7,
    )

    body = db.query("SELECT kind, content, pr_url, pr_number FROM $d;", {"d": deliverable})[0]
    assert body["kind"] == "pr"
    assert body["content"] == "resumo do agente"
    assert body["pr_url"] == "https://github.com/owner/kubo-forge/pull/7"
    assert body["pr_number"] == 7


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
    assert ctx.counterpart_task == analyst
    assert ctx.gate_task == gate
    assert ctx.question == "q?"
    assert ctx.content == "A análise."
    assert {s.id for s in ctx.sources} == {str(d1), str(d2)}
    assert {s.title for s in ctx.sources} == {"Rust ownership", "GC tradeoffs"}


def test_read_gate_context_none_for_orphan_task(db: Any) -> None:
    """Um task sem flow (belongs_to ausente) → None, nunca crash (registro órfão/manual)."""
    orphan = db.query("CREATE task SET state = 'awaiting_review';")[0]["id"]
    assert read_gate_context(db, orphan) is None


def test_read_gate_context_rejects_non_gate_task(db: Any, tmp_path: Path) -> None:
    """Só a task do HUMANO em awaiting_review resolve um gate (ADR-0018 §I): passar a task da
    ANALISTA (forja pela borda HTTP) → None, senão `decide_gate` atualizaria a task errada e o
    gate real ficaria aberto após os efeitos externos."""
    _inst, analyst, _gate = _review_flow(db, tmp_path)
    assert read_gate_context(db, analyst) is None  # analista não é o gate


def test_decide_gate_rejects_contradictory_decision(db: Any, tmp_path: Path) -> None:
    """A decisão não pode contradizer o estado destino (ADR-0018 §IV): approved→rejected (ou
    qualquer par fora de {approved→delivered, rejected→rejected}) levanta StateError, sem
    corromper a trilha de auditoria."""
    _inst, analyst, gate = _review_flow(db, tmp_path)
    with pytest.raises(StateError):
        decide_gate(
            db, analyst_task=analyst, gate_task=gate, to_state="rejected", decision="approved"
        )
    assert db.query("SELECT VALUE state FROM $t;", {"t": gate})[0] == "awaiting_review"


def test_open_gate_is_atomic_rolls_back_analyst_on_failure(
    db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """open_gate transiciona a analista E cria a task humana numa ÚNICA transação: se a criação
    falha no meio, a transição da analista reverte — o flow nunca fica em `awaiting_review` sem
    gate humano associado (CodeRabbit #3581867024, ADR-0018 §IV). Com chamadas separadas
    (transition_task + create_task) a transição commitaria antes da falha, deixando o flow órfão."""
    from kubo.errors import StoreError
    from kubo.store import flows as flows_mod

    inst = _instantiate(db, _ANALYSIS_REVIEW, tmp_path)
    analyst = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    transition_task(db, analyst, from_state="created", to_state="analyzing")
    assert db.query("SELECT VALUE state FROM $t;", {"t": analyst})[0] == "analyzing"

    def boom(*_args: Any, **_kw: Any) -> None:
        raise StoreError("transação revertida: simulação de falha na criação do gate")

    monkeypatch.setattr(flows_mod, "run_transaction", boom)

    with pytest.raises(StoreError, match="simulação"):
        flows_mod.open_gate(
            db,
            analyst_task=analyst,
            analyst_from="analyzing",
            analyst_to="awaiting_review",
            flow=inst.flow,
            human_persona=inst.personas["humano"],
            gate_state="awaiting_review",
        )
    # A transição da analista REVERTEU: segue em `analyzing`, não em `awaiting_review`.
    assert db.query("SELECT VALUE state FROM $t;", {"t": analyst})[0] == "analyzing"
    # Nenhuma task do humano foi criada (rollback integral).
    human_tasks = db.query(
        "SELECT VALUE id FROM $flow<-belongs_to<-task "
        "WHERE ->assigned_to->persona.catalog_name CONTAINS $human;",
        {"flow": inst.flow, "human": "humano"},
    )
    assert human_tasks == []


def test_open_gate_validates_analyst_transition_against_snapshot(db: Any, tmp_path: Path) -> None:
    """open_gate valida o par da analista contra o snapshot congelado (invariante 4): um par fora
    das transições declaradas levanta StateError antes de tocar o banco, como `transition_task`."""
    from kubo.store import flows as flows_mod

    inst = _instantiate(db, _ANALYSIS_REVIEW, tmp_path)
    analyst = create_task(db, flow=inst.flow, persona=inst.personas["analista"], state="created")
    with pytest.raises(StateError):
        flows_mod.open_gate(
            db,
            analyst_task=analyst,
            analyst_from="created",
            analyst_to="delivered",  # par não está nas transições do snapshot
            flow=inst.flow,
            human_persona=inst.personas["humano"],
            gate_state="awaiting_review",
        )


def test_list_flows_derives_status_gate_and_cast(db: Any, tmp_path: Path) -> None:
    """list_flows deriva o status dos tasks (ADR-0016 §II), marca gate aberto e junta o elenco
    ativo. Um flow no gate → status 'aguardando', gate_open, cast {analista, humano}."""
    inst, _analyst, _gate = _review_flow(db, tmp_path)

    rows = list_flows(db, limit=20, start=0)

    assert count_flows(db) == 1
    assert len(rows) == 1
    row = rows[0]
    assert row.id == str(inst.flow)
    assert row.question == "q?"
    assert row.template == "analysis-review"
    assert row.status == "aguardando"
    assert row.gate_open is True
    assert set(row.cast) == {"analista", "humano"}
    assert row.tasks_open == 2  # ambas em awaiting_review (não-terminal)


def test_list_flows_delivered_status(db: Any, tmp_path: Path) -> None:
    """Após aprovar, as 2 tasks vão a delivered → status 'entregue', sem gate aberto."""
    _, analyst, gate = _review_flow(db, tmp_path)
    decide_gate(db, analyst_task=analyst, gate_task=gate, to_state="delivered", decision="approved")

    row = list_flows(db, limit=20, start=0)[0]
    assert row.status == "entregue"
    assert row.gate_open is False
    assert row.tasks_open == 0


def test_flow_board_columns_are_snapshot_states_cards_are_tasks(db: Any, tmp_path: Path) -> None:
    """flow_board devolve as COLUNAS = estados do snapshot e os CARDS = tasks; a task do humano
    em awaiting_review é marcada como gate (ring âmbar + botões na UI)."""
    inst, analyst, gate = _review_flow(db, tmp_path)

    board = flow_board(db, inst.flow)

    assert board is not None
    assert board.id == str(inst.flow)
    assert board.template == "analysis-review"
    assert board.states == [
        "created",
        "analyzing",
        "awaiting_review",
        "delivered",
        "rejected",
        "failed",
    ]
    by_id = {c.id: c for c in board.tasks}
    assert by_id[str(analyst)].persona == "analista"
    assert by_id[str(analyst)].is_gate is False
    assert by_id[str(gate)].persona == "humano"
    assert by_id[str(gate)].is_gate is True  # humano + awaiting_review = card de gate


def test_flow_board_none_for_missing_flow(db: Any) -> None:
    """flow_board de um flow inexistente → None (nunca crash)."""
    from surrealdb import RecordID

    assert flow_board(db, RecordID("flow", "does-not-exist")) is None


def _write(tmp_path: Path, body: str) -> Path:
    """Escreve `body` em analysis.yaml no tmp e devolve o caminho (reescrita do R5)."""
    path = tmp_path / "analysis.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# dev-mini (ADR-0019): board com gate `review`→`done`/`rejected`, elenco dev+humano, deliverable
# `pr`. Prova que a maquinaria de gate é GENÉRICA — lê o snapshot, não nomes fixos do analysis.
_DEV_MINI = (
    "name: dev-mini\nversion: 1\n"
    "board:\n"
    "  states: [created, implementing, review, done, rejected, failed]\n"
    "  transitions:\n"
    "    - [created, implementing]\n    - [implementing, review]\n"
    "    - [implementing, failed]\n"
    "    - [review, done]\n    - [review, rejected]\n"
    "  gates:\n    - [review, done]\n    - [review, rejected]\n"
    "cast: [dev, humano]\ndeliverable: pr\ntriggers: [manual]\nbudget_usd: 5.0\n"
)


def _dev_flow(db: Any, tmp_path: Path) -> tuple[Any, Any, Any]:
    """Instancia um flow `dev-mini` levado ao gate: a task dev em `review` + a task do gate
    (humano) nascida em `review`. Devolve (inst, dev_task, gate_task)."""
    path = tmp_path / "dev-mini.yaml"
    path.write_text(_DEV_MINI, encoding="utf-8")
    template = load_flow_template(path)
    inst = instantiate_flow(db, template=template, personas=_PERSONAS, question="add hello()")
    dev = create_task(db, flow=inst.flow, persona=inst.personas["dev"], state="created")
    transition_task(db, dev, from_state="created", to_state="implementing")
    transition_task(db, dev, from_state="implementing", to_state="review")
    gate = create_task(db, flow=inst.flow, persona=inst.personas["humano"], state="review")
    return inst, dev, gate


def test_decide_gate_approves_non_delivered_target_by_convention(db: Any, tmp_path: Path) -> None:
    """A coerência decisão↔destino deriva do snapshot, não de literais: aprovar um gate cujo
    destino de sucesso é `done` (não `delivered`) move as 2 tasks a `done` — a maquinaria não
    hardcoda o nome do estado terminal (só `rejected` é reservado, ADR-0019 §VII)."""
    _inst, dev, gate = _dev_flow(db, tmp_path)
    decide_gate(db, analyst_task=dev, gate_task=gate, to_state="done", decision="approved")
    assert db.query("SELECT VALUE state FROM $t;", {"t": dev})[0] == "done"
    g = db.query("SELECT state, decision FROM $t;", {"t": gate})[0]
    assert g["state"] == "done"
    assert g["decision"] == "approved"


def test_decide_gate_rejects_approved_to_reject_state_dev(db: Any, tmp_path: Path) -> None:
    """A convenção `rejected`: aprovar com destino `rejected` é contraditório (approved ⇔
    não-rejected), StateError — sem corromper a auditoria, também no dev-mini."""
    _inst, dev, gate = _dev_flow(db, tmp_path)
    with pytest.raises(StateError):
        decide_gate(db, analyst_task=dev, gate_task=gate, to_state="rejected", decision="approved")
    assert db.query("SELECT VALUE state FROM $t;", {"t": gate})[0] == "review"


def test_read_gate_context_dev_pr_deliverable(db: Any, tmp_path: Path) -> None:
    """read_gate_context num gate dev: estado gate-from GENÉRICO (`review`), a task não-humana
    (dev) como counterpart, e o deliverable `kind=pr` com pr_url/pr_number ESTRUTURAIS (E3) +
    content = resumo untrusted (E4). `sources` vazio (PR não tem consults)."""
    inst, dev, gate = _dev_flow(db, tmp_path)
    insert_deliverable(
        db,
        flow=inst.flow,
        task=dev,
        kind="pr",
        content="resumo do agente",
        consulted=[],
        pr_url="https://github.com/owner/kubo-forge/pull/7",
        pr_number=7,
    )

    ctx = read_gate_context(db, gate)

    assert ctx is not None
    assert ctx.flow == inst.flow
    assert ctx.counterpart_task == dev
    assert ctx.gate_task == gate
    assert ctx.question == "add hello()"
    assert ctx.content == "resumo do agente"
    assert ctx.deliverable_kind == "pr"
    assert ctx.pr_url == "https://github.com/owner/kubo-forge/pull/7"
    assert ctx.pr_number == 7
    assert ctx.sources == []


def test_read_gate_context_report_sets_kind_and_null_pr(db: Any, tmp_path: Path) -> None:
    """No gate de report o deliverable_kind é `report` e os campos PR ficam None — o consumidor
    (UI/behavior) ramifica por kind sem adivinhar."""
    inst, analyst, gate = _review_flow(db, tmp_path)
    d1 = _seed_distilled(db, "Rust ownership")
    insert_deliverable(
        db, flow=inst.flow, task=analyst, kind="report", content="A análise.", consulted=[d1]
    )

    ctx = read_gate_context(db, gate)

    assert ctx is not None
    assert ctx.deliverable_kind == "report"
    assert ctx.pr_url is None
    assert ctx.pr_number is None
    assert ctx.counterpart_task == analyst


def test_read_gate_context_requires_exactly_one_counterpart(db: Any, tmp_path: Path) -> None:
    """Se o flow tiver DUAS tasks não-humanas, read_gate_context falha alto (StateError) em vez
    de escolher a primeira em silêncio — um template futuro ambíguo deve quebrar, não decidir."""
    inst, _dev, gate = _dev_flow(db, tmp_path)
    # segunda task não-humana no mesmo flow (dev extra) — ambiguidade
    create_task(db, flow=inst.flow, persona=inst.personas["dev"], state="implementing")
    with pytest.raises(StateError):
        read_gate_context(db, gate)


def test_flow_board_marks_dev_gate_from_snapshot(db: Any, tmp_path: Path) -> None:
    """is_gate é derivado do snapshot: a task do humano em `review` (gate-from de dev-mini) é
    marcada como gate, mesmo o estado NÃO se chamando `awaiting_review`."""
    _inst, dev, gate = _dev_flow(db, tmp_path)
    board = flow_board(db, _inst.flow)
    assert board is not None
    by_id = {c.id: c for c in board.tasks}
    assert by_id[str(gate)].is_gate is True  # humano + review (gate-from) = card de gate
    assert by_id[str(dev)].is_gate is False  # dev em review NÃO é gate (não é humano)


def test_list_flows_dev_gate_open_and_status(db: Any, tmp_path: Path) -> None:
    """list_flows marca gate_open para o dev-mini parado em `review` (gate-from do snapshot) —
    senão o dono não veria o gate na lista. Status derivado = 'aguardando'."""
    _inst, _dev, _gate = _dev_flow(db, tmp_path)
    row = list_flows(db, limit=20, start=0)[0]
    assert row.gate_open is True
    assert row.status == "aguardando"
    assert set(row.cast) == {"dev", "humano"}


def test_list_flows_dev_done_status(db: Any, tmp_path: Path) -> None:
    """Flow dev-mini v1 LEGADO (snapshot com `done` terminal): aprovar o gate (→`done`) dá
    'entregue', sem gate aberto — prova a compatibilidade retroativa (invariante 4): a
    terminal-ness deriva do snapshot ANTIGO, não do catálogo v2 (onde `done` abre promoção)."""
    _inst, dev, gate = _dev_flow(db, tmp_path)
    decide_gate(db, analyst_task=dev, gate_task=gate, to_state="done", decision="approved")
    row = list_flows(db, limit=20, start=0)[0]
    assert row.status == "entregue"
    assert row.gate_open is False
    assert row.tasks_open == 0


# dev-mini v2 (ADR-0021): `done` NÃO é terminal — abre o gate de promoção `[done, promoted]`.
_DEV_MINI_V2 = (
    "name: dev-mini\nversion: 2\n"
    "board:\n"
    "  states: [created, implementing, review, done, promoted, rejected, failed]\n"
    "  transitions:\n"
    "    - [created, implementing]\n    - [implementing, review]\n"
    "    - [implementing, failed]\n"
    "    - [review, done]\n    - [review, rejected]\n"
    "    - [done, promoted]\n"
    "  gates:\n    - [review, done]\n    - [review, rejected]\n    - [done, promoted]\n"
    "cast: [dev, humano]\ndeliverable: pr\ntriggers: [manual]\nbudget_usd: 5.0\n"
)


def _dev_flow_v2(db: Any, tmp_path: Path) -> tuple[Any, Any, Any]:
    """Instancia um flow `dev-mini` v2 levado ao gate `review`: (inst, dev_task, review_gate)."""
    path = tmp_path / "dev-mini.yaml"
    path.write_text(_DEV_MINI_V2, encoding="utf-8")
    template = load_flow_template(path)
    inst = instantiate_flow(db, template=template, personas=_PERSONAS, question="add worker X")
    dev = create_task(db, flow=inst.flow, persona=inst.personas["dev"], state="created")
    transition_task(db, dev, from_state="created", to_state="implementing")
    transition_task(db, dev, from_state="implementing", to_state="review")
    gate = create_task(db, flow=inst.flow, persona=inst.personas["humano"], state="review")
    return inst, dev, gate


def _open_human_gates(db: Any, flow: Any, state: str) -> list[Any]:
    """Tasks humanas ABERTAS (sem decisão) num estado — o gate real vs a task decidida parada."""
    return db.query(
        "SELECT VALUE id FROM $f<-belongs_to<-task WHERE state = $s AND decision IS NONE "
        "AND (->assigned_to->persona.catalog_name)[0] = 'humano';",
        {"f": flow, "s": state},
    )


def test_dev_v2_approve_auto_opens_promotion_gate(db: Any, tmp_path: Path) -> None:
    """v2: aprovar `review→done` NÃO termina o flow — a MESMA decisão cria a próxima task humana
    (o gate de promoção) em `done`, sem decisão. `done` tem saída (→promoted) no snapshot. A
    persona da nova task deriva do próprio gate task (humano), sem parâmetro."""
    inst, dev, review_gate = _dev_flow_v2(db, tmp_path)
    next_gate = decide_gate(
        db, analyst_task=dev, gate_task=review_gate, to_state="done", decision="approved"
    )
    assert next_gate is not None
    assert db.query("SELECT VALUE state FROM $t;", {"t": dev})[0] == "done"
    rg = db.query("SELECT state, decision FROM $t;", {"t": review_gate})[0]
    assert rg == {"state": "done", "decision": "approved"}
    ng = db.query(
        "SELECT state, decision, (->assigned_to->persona.catalog_name)[0] AS p FROM $t;",
        {"t": next_gate},
    )[0]
    assert ng["state"] == "done"
    assert ng["decision"] is None
    assert ng["p"] == "humano"
    assert _open_human_gates(db, inst.flow, "done") == [next_gate]


def test_decide_gate_no_auto_open_when_target_is_terminal(db: Any, tmp_path: Path) -> None:
    """Rejeitar (`review→rejected`, terminal) NÃO abre gate nenhum — o auto-open só dispara
    quando o destino é um estado gate-from do snapshot."""
    _inst, dev, review_gate = _dev_flow_v2(db, tmp_path)
    result = decide_gate(
        db,
        analyst_task=dev,
        gate_task=review_gate,
        to_state="rejected",
        decision="rejected",
        reason="escopo",
    )
    assert result is None


def test_decide_gate_stale_second_decide_keeps_single_promotion(db: Any, tmp_path: Path) -> None:
    """Corrida double-decide: a 2ª decisão (gate já em `done`) falha alto (StateError) e NÃO
    cria um 2º gate de promoção — auto-open condicionado ao UPDATE do gate mover a linha."""
    inst, dev, review_gate = _dev_flow_v2(db, tmp_path)
    decide_gate(db, analyst_task=dev, gate_task=review_gate, to_state="done", decision="approved")
    with pytest.raises(StateError):
        decide_gate(
            db, analyst_task=dev, gate_task=review_gate, to_state="done", decision="approved"
        )
    assert len(_open_human_gates(db, inst.flow, "done")) == 1


def test_read_gate_context_ignores_decided_gate(db: Any, tmp_path: Path) -> None:
    """A task de gate `review` JÁ DECIDIDA fica parada em `done` (gate-from de [done,promoted])
    mas NÃO é gate aberto — read_gate_context retorna None nela; só o gate de promoção resolve,
    com `gate_state='done'` e a dev como counterpart (única não-humana)."""
    inst, dev, review_gate = _dev_flow_v2(db, tmp_path)
    promo = decide_gate(
        db, analyst_task=dev, gate_task=review_gate, to_state="done", decision="approved"
    )
    assert promo is not None
    assert read_gate_context(db, review_gate) is None
    ctx = read_gate_context(db, promo)
    assert ctx is not None
    assert ctx.gate_state == "done"
    assert ctx.counterpart_task == dev


def test_read_gate_context_exposes_gate_state(db: Any, tmp_path: Path) -> None:
    """gate_state expõe o estado gate-from da decisão (para o behavior validar o par antes de
    qualquer I/O externo — trap c: rejeitar um gate de promoção não pode tocar o PR mesclado)."""
    _inst, _dev, review_gate = _dev_flow_v2(db, tmp_path)
    ctx = read_gate_context(db, review_gate)
    assert ctx is not None
    assert ctx.gate_state == "review"


def test_flow_board_v2_promotion_open_and_review_decided(db: Any, tmp_path: Path) -> None:
    """is_gate distingue o gate ABERTO do DECIDIDO: a promoção (humano, done, sem decisão) é gate;
    a review-human decidida (done, com decisão) NÃO; a dev (não-humana) NÃO."""
    inst, dev, review_gate = _dev_flow_v2(db, tmp_path)
    promo = decide_gate(
        db, analyst_task=dev, gate_task=review_gate, to_state="done", decision="approved"
    )
    board = flow_board(db, inst.flow)
    assert board is not None
    by_id = {c.id: c for c in board.tasks}
    assert by_id[str(promo)].is_gate is True
    assert by_id[str(review_gate)].is_gate is False
    assert by_id[str(dev)].is_gate is False


def test_list_flows_v2_awaiting_promotion_then_promoted(db: Any, tmp_path: Path) -> None:
    """Status derivado no v2: após aprovar review→done, 'aguardando' (promoção pendente); após
    confirmar done→promoted, 'entregue', sem gate aberto e tasks_open=0 — a review-human decidida
    parada em `done` (não-terminal) conta como FECHADA (closed = terminal ∨ decidida)."""
    inst, dev, review_gate = _dev_flow_v2(db, tmp_path)
    promo = decide_gate(
        db, analyst_task=dev, gate_task=review_gate, to_state="done", decision="approved"
    )
    assert promo is not None
    row = list_flows(db, limit=20, start=0)[0]
    assert row.gate_open is True
    assert row.status == "aguardando"

    decide_gate(db, analyst_task=dev, gate_task=promo, to_state="promoted", decision="approved")
    row = list_flows(db, limit=20, start=0)[0]
    assert row.status == "entregue"
    assert row.gate_open is False
    assert row.tasks_open == 0


def test_flow_gates_reads_snapshot_pairs(db: Any, tmp_path: Path) -> None:
    """flow_gates expõe os pares de gate do snapshot congelado (para o behavior validar o par
    pretendido antes de I/O externo). `[done, rejected]` NÃO é gate do dev-mini v2."""
    inst, _dev, _review_gate = _dev_flow_v2(db, tmp_path)
    gates = flow_gates(db, inst.flow)
    assert ("review", "done") in gates
    assert ("done", "promoted") in gates
    assert ("done", "rejected") not in gates
