"""Store do modelo de EXECUÇÃO da spec §2.3: flow, task, persona, deliverable.

Porta única (invariante 2) para as tabelas de execução, separada de `knowledge.py`
(já > 1000 linhas) por coesão — mesmo invariante, arquivo distinto. O `snapshot` do
flow é a FONTE das transições: `transition_task` valida contra ele, NUNCA contra o
catálogo vivo (invariante 4). Escrita que precisa ser um fato único é atômica via
`run_transaction`; a instanciação NÃO é transacionada — um flow meio-formado por
crash é órfão inofensivo, re-execução = novo flow (ADR-0016 §III/§IV).
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from surrealdb import RecordID

from kubo.errors import ConfigError, StateError
from kubo.runtime.flow_templates import FlowTemplate
from kubo.runtime.personas import Persona
from kubo.store.transaction import run_transaction


def _fresh(table: str) -> RecordID:
    """Record ID novo para um evento de execução (flow/task/persona/deliverable):
    cada instanciação é distinta (mesmo idioma de `knowledge._fresh`)."""
    return RecordID(table, secrets.token_hex(16))


@dataclass(frozen=True)
class InstantiatedFlow:
    """Resultado de `instantiate_flow`: o flow criado + o mapa catalog-name →
    RecordID das personas materializadas (o flow runner usa o id da analista para
    `create_task`; usa a config in-memory já congelada para montar o worker)."""

    flow: RecordID
    personas: dict[str, RecordID]


def instantiate_flow(
    db: Any, *, template: FlowTemplate, personas: Mapping[str, Persona], question: str
) -> InstantiatedFlow:
    """Instancia um flow: congela o snapshot INTEGRAL do template e materializa uma
    persona (snapshot congelado) por membro do elenco. Bookkeeping GENÉRICO — não
    decide quem recebe task (isso é comportamento do FLOW_REGISTRY, ADR-0016 §IV).

    Não transacionado: cada CREATE é independente; um crash no meio deixa um flow
    órfão (sem todas as personas) — inofensivo, re-execução = novo flow. Elenco que
    referencia persona ausente do catálogo falha alto (ConfigError). O snapshot vai
    em `mode="json"` (tuplas de transição viram arrays, serializáveis pelo SDK)."""
    flow = _create(
        db,
        "flow",
        {
            "template_name": template.name,
            "template_version": template.version,
            "question": question,
            "snapshot": template.model_dump(mode="json"),
        },
    )
    materialized: dict[str, RecordID] = {}
    for name in template.cast:
        persona = personas.get(name)
        if persona is None:
            raise ConfigError(f"elenco referencia persona '{name}' ausente do catálogo")
        materialized[name] = _create(
            db,
            "persona",
            {
                "name": persona.name,
                "executor": persona.executor,
                "model": persona.model,
                "prompt": persona.prompt,
                "permissions": list(persona.permissions),
                "catalog_name": persona.name,
            },
        )
    return InstantiatedFlow(flow=flow, personas=materialized)


def _create(db: Any, table: str, data: dict[str, Any]) -> RecordID:
    """CREATE de um registro a partir de um dict, devolvendo o id gerado. `table` é
    literal INTERNO da store (nunca entrada externa) — interpolação segura."""
    rows = db.query(f"CREATE {table} CONTENT $data;", {"data": data})  # noqa: S608
    return rows[0]["id"]


def create_task(db: Any, *, flow: RecordID, persona: RecordID, state: str) -> RecordID:
    """Cria um task com estado inicial e liga `belongs_to->flow` + `assigned_to->persona`
    numa transação atômica (o task e suas arestas de proveniência são um fato único).

    Chamado pelo código do FLOW_REGISTRY (não por `instantiate_flow`): QUEM ganha task
    e em que estado é comportamento do template, não bookkeeping genérico (ADR-0016 §IV)."""
    task = _fresh("task")
    run_transaction(
        db,
        [
            "CREATE $t SET state = $state",
            "RELATE $t->belongs_to->$flow",
            "RELATE $t->assigned_to->$persona",
        ],
        {"t": task, "state": state, "flow": flow, "persona": persona},
    )
    return task


def _pairs(raw: Any) -> set[tuple[str, str]]:
    """Coage a lista de pares `[[from, to], ...]` de um snapshot (dado não-tipado do banco)
    a um conjunto de tuplas. `raw` NONE/ausente (snapshot antigo sem a chave) → conjunto
    vazio; `len == 2` guarda o índice duplo contra registro malformado."""
    items: list[list[str]] = cast("list[list[str]]", raw) if raw else []
    return {(t[0], t[1]) for t in items if len(t) == 2}


def _snapshot_board(db: Any, flow: RecordID) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Lê `(transitions, gates)` do `flow.snapshot` congelado (invariante 4) como conjuntos
    de pares. Fonte única para `transition_task` e `decide_gate` — a validação de estado
    nunca lê o catálogo vivo. Snapshot sem `gates` (flow `analysis` legado) → gates vazio."""
    rows = db.query(
        "SELECT snapshot.board.transitions AS transitions, snapshot.board.gates AS gates FROM $f;",
        {"f": flow},
    )
    row: dict[str, Any] = rows[0] if rows else {}
    return _pairs(row.get("transitions")), _pairs(row.get("gates"))


def transition_task(db: Any, task: RecordID, *, from_state: str, to_state: str) -> None:
    """Transiciona um task de `from_state` para `to_state`, validando SEMPRE contra o
    `flow.snapshot` congelado — nunca contra o catálogo (invariante 4, teste honesto R5).

    Levanta `StateError` se o task não existe, se o estado atual não é `from_state`
    (guarda contra dupla-transição), se o par é uma travessia de GATE (§II — exige decisão
    humana, só `decide_gate` passa), ou se o par não está nas transições do snapshot.
    Sucesso grava `task.state = to_state`."""
    rows = db.query("SELECT state, ->belongs_to->flow AS flow FROM $t;", {"t": task})
    if not rows:
        raise StateError(f"task {task} não existe")
    current = rows[0]["state"]
    if current != from_state:
        raise StateError(f"task em estado '{current}', from_state esperado era '{from_state}'")
    flow_id: RecordID | None = next(iter(rows[0].get("flow") or []), None)
    if flow_id is None:
        raise StateError(f"task {task} sem flow (belongs_to ausente)")
    pairs, gates = _snapshot_board(db, flow_id)
    # Guarda de gate ANTES da validação genérica (ADR-0018 §II): um par gated dá o erro
    # específico "use decide_gate", não "fora do snapshot". `transition_task` NUNCA
    # atravessa um gate — não tem como portar contexto de decisão, então é sempre erro.
    if (from_state, to_state) in gates:
        raise StateError(
            f"transição de gate ({from_state}, {to_state}) exige decisão humana — use decide_gate"
        )
    if (from_state, to_state) not in pairs:
        raise StateError(f"transição ({from_state}, {to_state}) não está no snapshot do flow")
    db.query("UPDATE $t SET state = $to;", {"t": task, "to": to_state})


def _task_state_and_flow(db: Any, task: RecordID) -> tuple[str, RecordID]:
    """Estado atual + flow de um task; StateError se o task ou a aresta `belongs_to` falta."""
    rows = db.query("SELECT state, ->belongs_to->flow AS flow FROM $t;", {"t": task})
    if not rows:
        raise StateError(f"task {task} não existe")
    flow_id: RecordID | None = next(iter(rows[0].get("flow") or []), None)
    if flow_id is None:
        raise StateError(f"task {task} sem flow (belongs_to ausente)")
    return rows[0]["state"], flow_id


def decide_gate(
    db: Any,
    *,
    analyst_task: RecordID,
    gate_task: RecordID,
    to_state: str,
    decision: str,
    reason: str | None = None,
) -> None:
    """Decide um gate: transiciona a task da analista E a task do gate JUNTAS para `to_state`
    (`delivered`|`rejected`) numa ÚNICA transação, gravando a decisão na task do gate
    (ADR-0018 §IV). É a ÚNICA porta que atravessa um par gated — `transition_task` recusa.

    Valida (StateError, antes da transação, para erro legível): motivo obrigatório na
    rejeição; ambas as tasks no MESMO estado de partida e no MESMO flow; e o par
    `(from, to)` ∈ gates do snapshot congelado. O UPDATE é condicional (`WHERE state =
    $from`): uma corrida double-decide (duas abas passando o pré-check) degrada para no-op
    total, nunca para decisão sobrescrita nem board incoerente (TOCTOU residual nomeado)."""
    reason = (reason or "").strip()
    if decision == "rejected" and not reason:
        raise StateError("rejeição de gate exige motivo")

    gate_from, gate_flow = _task_state_and_flow(db, gate_task)
    analyst_from, analyst_flow = _task_state_and_flow(db, analyst_task)
    if analyst_flow != gate_flow:
        raise StateError("tasks do gate pertencem a flows distintos")
    if analyst_from != gate_from:
        raise StateError(
            f"tasks do gate em estados divergentes (analista '{analyst_from}', gate '{gate_from}')"
        )

    _, gates = _snapshot_board(db, gate_flow)
    if (gate_from, to_state) not in gates:
        raise StateError(f"({gate_from}, {to_state}) não é uma transição de gate do snapshot")

    run_transaction(
        db,
        [
            "UPDATE $at SET state = $to WHERE state = $from",
            "UPDATE $gt SET state = $to, decision = $dec, reason = $rsn, "
            "decided_at = time::now() WHERE state = $from",
        ],
        {
            "at": analyst_task,
            "gt": gate_task,
            "to": to_state,
            "from": gate_from,
            "dec": decision,
            "rsn": reason or None,
        },
    )


def set_task_run(db: Any, task: RecordID, run: RecordID) -> None:
    """Grava `task.run` apontando para o run que executou o task (auditoria — liga
    o bookkeeping de flow ao ÚNICO mecanismo de execução, `run_worker`)."""
    db.query("UPDATE $t SET run = $run;", {"t": task, "run": run})


@dataclass(frozen=True)
class GateSource:
    """Uma fonte que a análise consultou: id (`distilled:<key>`) + título (via
    `derived_from->item`). Satisfaz o Protocol `SourceView` do worker (id + title) — o mesmo
    render de Telegram serve o run e a aprovação. A ORDEM não é significativa: `consults` é
    conjunto, o ranking do retrieval se perde (cosmético, ADR-0018 §V)."""

    id: str
    title: str | None


@dataclass(frozen=True)
class GateContext:
    """Tudo que uma decisão de gate precisa, lido do grafo numa passada (ADR-0018 §I/§V): os
    dois tasks (para `decide_gate`), a pergunta do flow, a PROSA do deliverable e as fontes
    consultadas. Uma leitura, dois consumidores: o painel de gate (UI) e o envio na aprovação."""

    flow: RecordID
    analyst_task: RecordID
    gate_task: RecordID
    question: str
    content: str
    sources: list[GateSource]


def _first_title(titles: Any) -> str | None:
    """Primeiro título de uma projeção `->derived_from->item.title` (um distilled pode derivar
    de vários itens; o painel mostra um). None se vazio/ausente."""
    if isinstance(titles, list):
        items = cast("list[Any]", titles)
        return next((str(t) for t in items if t), None)
    return str(titles) if titles else None


def read_gate_context(db: Any, gate_task: RecordID) -> GateContext | None:
    """Reúne o contexto de um gate a partir do task do gate (ADR-0018 §I/§V): o flow, a
    pergunta, o task da analista (para transicionar junto), a prosa do deliverable e as fontes
    consultadas (id+título). `None` se o gate não resolve um flow (registro órfão/inexistente).

    Fonte única do painel de gate e do re-render de Telegram na aprovação — a apresentação se
    computa no USO, o grafo guarda só o fato (prosa + arestas `consults`)."""
    head = db.query(
        "SELECT VALUE {"
        "flow: (->belongs_to->flow)[0], "
        "question: (->belongs_to->flow.question)[0], "
        "content: (->belongs_to->flow->produces->deliverable.content)[0]"
        "} FROM $g;",
        {"g": gate_task},
    )
    row: dict[str, Any] = head[0] if head else {}
    flow = row.get("flow")
    if flow is None:
        return None
    analyst = db.query(
        "SELECT VALUE id FROM $flow<-belongs_to<-task "
        "WHERE ->assigned_to->persona.catalog_name CONTAINS 'analista';",
        {"flow": flow},
    )
    analyst_task: RecordID | None = analyst[0] if analyst else None
    if analyst_task is None:
        return None
    src_rows = db.query(
        "SELECT id, ->derived_from->item.title AS titles FROM $a->consults->distilled;",
        {"a": analyst_task},
    )
    sources = [GateSource(id=str(r["id"]), title=_first_title(r.get("titles"))) for r in src_rows]
    return GateContext(
        flow=flow,
        analyst_task=analyst_task,
        gate_task=gate_task,
        question=str(row.get("question") or ""),
        content=str(row.get("content") or ""),
        sources=sources,
    )


def insert_deliverable(
    db: Any,
    *,
    flow: RecordID,
    task: RecordID,
    kind: str,
    content: str,
    consulted: Sequence[RecordID],
) -> RecordID:
    """Grava o deliverable e sua proveniência numa ÚNICA transação atômica: o registro
    (`kind` + `content` markdown), a aresta `flow->produces->deliverable`, e uma aresta
    `task->consults->distilled` por fonte recuperada (ADR-0016 §II/§III).

    `consulted` vem do RETRIEVAL (nunca da saída do LLM — regra das citações, §VI): a
    proveniência não é forjável por injeção. O deliverable NÃO ganha chunks/embedding —
    fica fora do acervo buscável por design (E2)."""
    deliverable = _fresh("deliverable")
    stmts = [
        "CREATE $d SET kind = $kind, content = $content",
        "RELATE $f->produces->$d",
    ]
    params: dict[str, Any] = {
        "d": deliverable,
        "kind": kind,
        "content": content,
        "f": flow,
        "t": task,
    }
    for i, distilled in enumerate(consulted):
        stmts.append(f"RELATE $t->consults->$c{i}")
        params[f"c{i}"] = distilled
    run_transaction(db, stmts, params)
    return deliverable
