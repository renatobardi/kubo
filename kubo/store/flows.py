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


def transition_task(db: Any, task: RecordID, *, from_state: str, to_state: str) -> None:
    """Transiciona um task de `from_state` para `to_state`, validando SEMPRE contra o
    `flow.snapshot` congelado — nunca contra o catálogo (invariante 4, teste honesto R5).

    Levanta `StateError` se o task não existe, se o estado atual não é `from_state`
    (guarda contra dupla-transição), ou se o par `(from, to)` não está nas transições
    do snapshot. Sucesso grava `task.state = to_state`."""
    rows = db.query("SELECT state, ->belongs_to->flow AS flow FROM $t;", {"t": task})
    if not rows:
        raise StateError(f"task {task} não existe")
    current = rows[0]["state"]
    if current != from_state:
        raise StateError(f"task em estado '{current}', from_state esperado era '{from_state}'")
    flow_id: RecordID | None = next(iter(rows[0].get("flow") or []), None)
    if flow_id is None:
        raise StateError(f"task {task} sem flow (belongs_to ausente)")
    transitions = db.query("SELECT VALUE snapshot.board.transitions FROM $f;", {"f": flow_id})
    # `first` pode ser NONE se o snapshot não tiver board.transitions (registro malformado/
    # manual): SurrealDB devolve `[None]`. `if first` cobre lista vazia E None → pairs vazio →
    # a transição não casa → StateError (não um TypeError cru). `len(t) == 2` guarda o índice
    # duplo: o snapshot nasce de um FlowTemplate validado, mas ler do banco é dado não-tipado.
    first = transitions[0] if transitions else None
    raw: list[list[str]] = cast("list[list[str]]", first) if first else []
    pairs = {(t[0], t[1]) for t in raw if len(t) == 2}
    if (from_state, to_state) not in pairs:
        raise StateError(f"transição ({from_state}, {to_state}) não está no snapshot do flow")
    db.query("UPDATE $t SET state = $to;", {"t": task, "to": to_state})


def set_task_run(db: Any, task: RecordID, run: RecordID) -> None:
    """Grava `task.run` apontando para o run que executou o task (auditoria — liga
    o bookkeeping de flow ao ÚNICO mecanismo de execução, `run_worker`)."""
    db.query("UPDATE $t SET run = $run;", {"t": task, "run": run})


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
