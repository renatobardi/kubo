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


# `rejected` é NOME DE ESTADO RESERVADO (ADR-0019 §VII): a coerência decisão↔destino do gate
# deriva desta convenção, não de um mapa de nomes por template (isso seria rampa de DSL,
# invariante 3). Rejeitar ⇔ destino `rejected`; aprovar leva a qualquer OUTRO destino de gate
# (`delivered` no analysis, `done` no dev-mini). O par (from, to) ∈ snapshot.gates continua
# sendo validado à parte; esta convenção só barra a incoerência (aprovar mandando pra rejected)
# que forjaria a trilha de auditoria.
_REJECTED = "rejected"
_HUMAN_CATALOG = "humano"


# LIMIT/START não aceitam bind param neste SurrealDB; `list_flows` interpola SÓ ints
# (paginação da store, nunca entrada coletada) via .format — S608 suprimido no call site.
_LIST_FLOWS_SQL = (
    "SELECT id, template_name, question, created_at, "
    "<-belongs_to<-task.state AS task_states, "
    "snapshot.board.gates AS gate_pairs, "
    "array::distinct(<-belongs_to<-task->assigned_to->persona.catalog_name) AS cast "
    "FROM flow ORDER BY created_at DESC LIMIT {limit} START {start};"
)


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


def open_gate(
    db: Any,
    *,
    analyst_task: RecordID,
    analyst_from: str,
    analyst_to: str,
    flow: RecordID,
    human_persona: RecordID,
    gate_state: str,
) -> RecordID:
    """Abre um gate atomicamente (ADR-0018 §IV): transiciona a task da analista de
    `analyst_from` para `analyst_to` E cria/liga a task do humano em `gate_state`
    numa ÚNICA transação. Se a criação falhar, a transição REVERTE — o flow nunca
    fica em `awaiting_review` sem gate humano associado (CodeRabbit #3581867024).

    Valida o par da analista contra o snapshot congelado (invariante 4) antes da
    transação, como `transition_task` (guarda de gate + par ∈ transitions); o
    `flow` informado deve corresponder ao `belongs_to` do task. O UPDATE condicional
    (`WHERE state = $from`) fecha TOCTOU de dupla abertura. Devolve o id do task do
    humano criado."""
    rows = db.query("SELECT state, ->belongs_to->flow AS flow FROM $t;", {"t": analyst_task})
    if not rows:
        raise StateError(f"task {analyst_task} não existe")
    current = rows[0]["state"]
    if current != analyst_from:
        raise StateError(f"task em estado '{current}', from_state esperado era '{analyst_from}'")
    flow_id: RecordID | None = next(iter(rows[0].get("flow") or []), None)
    if flow_id is None:
        raise StateError(f"task {analyst_task} sem flow (belongs_to ausente)")
    if flow_id != flow:
        raise StateError("flow informado não corresponde ao flow do task da analista")
    pairs, gates = _snapshot_board(db, flow_id)
    if (analyst_from, analyst_to) in gates:
        raise StateError(
            f"transição de gate ({analyst_from}, {analyst_to}) "
            "exige decisão humana — use decide_gate"
        )
    if (analyst_from, analyst_to) not in pairs:
        raise StateError(f"transição ({analyst_from}, {analyst_to}) não está no snapshot do flow")
    gate_task = _fresh("task")
    run_transaction(
        db,
        [
            "UPDATE $at SET state = $to WHERE state = $from",
            "CREATE $gt SET state = $gate_state",
            "RELATE $gt->belongs_to->$flow",
            "RELATE $gt->assigned_to->$persona",
        ],
        {
            "at": analyst_task,
            "to": analyst_to,
            "from": analyst_from,
            "gt": gate_task,
            "gate_state": gate_state,
            "flow": flow,
            "persona": human_persona,
        },
    )
    return gate_task


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
    if decision not in ("approved", "rejected"):
        raise StateError(f"decisão '{decision}' inválida (só approved|rejected)")
    # Convenção do estado reservado (_REJECTED): rejeitar ⇔ destino `rejected`. Um par que
    # discorda (approved→rejected, ou rejected→done) é forja de auditoria — barrado aqui; o
    # par ∈ snapshot.gates é validado logo abaixo. Sem literais `delivered`/`done`.
    if (decision == _REJECTED) != (to_state == _REJECTED):
        raise StateError(
            f"decisão '{decision}' não corresponde ao estado destino '{to_state}' "
            f"(rejeição exige destino '{_REJECTED}'; aprovação, um destino não-'{_REJECTED}')"
        )
    if decision == _REJECTED and not reason:
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


def task_state(db: Any, task: RecordID) -> str | None:
    """Estado atual de um task; `None` se não existe (staleness — invariante 2: a leitura é da
    store, não da rota)."""
    rows = db.query("SELECT VALUE state FROM $t;", {"t": task})
    return str(rows[0]) if rows else None


def flow_of_task(db: Any, task: RecordID) -> RecordID | None:
    """O flow ao qual um task pertence (`belongs_to`), ou `None` (invariante 2)."""
    rows = db.query("SELECT VALUE (->belongs_to->flow)[0] FROM $t;", {"t": task})
    return rows[0] if rows and rows[0] is not None else None


def template_of_task(db: Any, task: RecordID) -> str | None:
    """O `template_name` do flow ao qual um task pertence — binding gate→comportamento keyed
    pelo nome (E4). `None` se o task/flow não resolve (invariante 2)."""
    rows = db.query("SELECT VALUE (->belongs_to->flow.template_name)[0] FROM $t;", {"t": task})
    name = rows[0] if rows else None
    return str(name) if isinstance(name, str) else None


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
    """Tudo que uma decisão de gate precisa, lido do grafo numa passada (ADR-0018 §I/§V,
    ADR-0019 §VII): os dois tasks (para `decide_gate`), a pergunta do flow, a PROSA untrusted do
    deliverable e o que mais o consumidor precisa por KIND. Read-model frozen (DTO): o consumidor
    (UI/behavior) ramifica por `deliverable_kind` — `report` traz `sources` (consults); `pr` traz
    `pr_url`/`pr_number` ESTRUTURAIS (E3, vindos da API, nunca do `content`). Um só tipo com
    campos opcionais é honesto enquanto há dois kinds; um terceiro conjunto de campos o dividiria.

    `counterpart_task` é a task NÃO-HUMANA do flow (a analista no report, a dev no PR) — a
    contraparte que `decide_gate` transiciona junto com o gate."""

    flow: RecordID
    counterpart_task: RecordID
    gate_task: RecordID
    question: str
    content: str
    deliverable_kind: str
    sources: list[GateSource]
    pr_url: str | None = None
    pr_number: int | None = None


def _first_title(titles: Any) -> str | None:
    """Primeiro título de uma projeção `->derived_from->item.title` (um distilled pode derivar
    de vários itens; o painel mostra um). None se vazio/ausente."""
    if isinstance(titles, list):
        items = cast("list[Any]", titles)
        return next((str(t) for t in items if t), None)
    return str(titles) if titles else None


def read_gate_context(db: Any, gate_task: RecordID) -> GateContext | None:
    """Reúne o contexto de um gate a partir do task do gate (ADR-0018 §I/§V, ADR-0019 §VII): o
    flow, a pergunta, a task NÃO-HUMANA (contraparte, para transicionar junto), o deliverable
    (kind + prosa untrusted + ref PR estrutural) e — no report — as fontes consultadas.

    `None` a menos que `gate_task` seja de fato um GATE HUMANO ABERTO: persona `humano` E estado
    que é um `from` de algum par em `snapshot.gates` (GENÉRICO — `awaiting_review` no report,
    `review` no dev). A validação fecha a forja pela borda HTTP (o id vem do form): sem ela,
    passar a contraparte a resolveria como o próprio gate (`counterpart == gate_task`) e
    `decide_gate` atualizaria o mesmo registro duas vezes, deixando o gate real aberto após os
    efeitos externos. A contraparte é exigida NÃO-humana, distinta do gate, e ÚNICA — duas
    tasks não-humanas (template ambíguo) falham alto (StateError), nunca "pega a primeira"."""
    head = db.query(
        "SELECT VALUE {"
        "flow: (->belongs_to->flow)[0], "
        "question: (->belongs_to->flow.question)[0], "
        "content: (->belongs_to->flow->produces->deliverable.content)[0], "
        "kind: (->belongs_to->flow->produces->deliverable.kind)[0], "
        "pr_url: (->belongs_to->flow->produces->deliverable.pr_url)[0], "
        "pr_number: (->belongs_to->flow->produces->deliverable.pr_number)[0], "
        "gates: (->belongs_to->flow.snapshot.board.gates)[0], "
        "state: state, "
        "persona: (->assigned_to->persona.catalog_name)[0]"
        "} FROM $g;",
        {"g": gate_task},
    )
    row: dict[str, Any] = head[0] if head else {}
    flow = row.get("flow")
    gate_from = {pair[0] for pair in _pairs(row.get("gates"))}
    if flow is None or row.get("persona") != _HUMAN_CATALOG or row.get("state") not in gate_from:
        return None
    counterpart = db.query(
        "SELECT VALUE id FROM $flow<-belongs_to<-task "
        "WHERE (->assigned_to->persona.catalog_name)[0] != $human AND id != $g;",
        {"flow": flow, "g": gate_task, "human": _HUMAN_CATALOG},
    )
    if not counterpart:
        return None
    if len(counterpart) > 1:
        raise StateError("flow com múltiplas tasks não-humanas — gate ambíguo")
    counterpart_task: RecordID = counterpart[0]
    src_rows = db.query(
        "SELECT id, ->derived_from->item.title AS titles FROM $a->consults->distilled;",
        {"a": counterpart_task},
    )
    sources = [GateSource(id=str(r["id"]), title=_first_title(r.get("titles"))) for r in src_rows]
    pr_number = row.get("pr_number")
    return GateContext(
        flow=flow,
        counterpart_task=counterpart_task,
        gate_task=gate_task,
        question=str(row.get("question") or ""),
        content=str(row.get("content") or ""),
        deliverable_kind=str(row.get("kind") or ""),
        sources=sources,
        pr_url=str(row["pr_url"]) if row.get("pr_url") else None,
        pr_number=int(pr_number) if pr_number is not None else None,
    )


@dataclass(frozen=True)
class FlowListRow:
    """Uma linha da lista de Fluxos (UI, paridade FlowsScreen): a pergunta como nome, o
    template, o status DERIVADO dos tasks (ADR-0016 §II — flow não tem máquina própria),
    se há gate aberto, o elenco ativo (glifos) e a contagem de tasks abertas."""

    id: str
    question: str
    template: str
    status: str
    gate_open: bool
    cast: list[str]
    tasks_open: int
    created_at: str


@dataclass(frozen=True)
class FlowTaskCard:
    """Um card do board (cards = TASKS, não flows — ADR-0018 §V): estado, persona (glifo) e se
    é o GATE aguardando decisão (task do humano em awaiting_review → ring âmbar + botões)."""

    id: str
    state: str
    persona: str
    is_gate: bool


@dataclass(frozen=True)
class FlowBoardView:
    """O board de UM flow: a pergunta, o template, as COLUNAS (estados do snapshot congelado) e
    os cards (tasks). `None` de `flow_board` = flow inexistente."""

    id: str
    question: str
    template: str
    states: list[str]
    tasks: list[FlowTaskCard]


# `done` (dev-mini) e `delivered` (analysis) são os terminais de SUCESSO; `rejected`/`failed`,
# os de falha. União literal barata (rótulos são cosméticos, ADR-0019 §VII); a detecção de gate
# aberto NÃO é literal — deriva do snapshot.
_SUCCESS_TERMINAL = frozenset({"delivered", "done"})
_TERMINAL = _SUCCESS_TERMINAL | frozenset({"rejected", "failed"})


def _flow_status(states: list[str], gate_from: set[str]) -> str:
    """Status DERIVADO dos estados dos tasks (o flow não tem máquina própria, ADR-0016 §II).
    Precedência: gate aberto > falhou > rejeitado > entregue > rodando. `gate_from` vem do
    snapshot (os `from` dos pares de gate) — a "espera de gate" não hardcoda `awaiting_review`."""
    if any(s in gate_from for s in states):
        return "aguardando"
    if "failed" in states:
        return "falhou"
    if "rejected" in states:
        return "rejeitado"
    if states and all(s in _SUCCESS_TERMINAL for s in states):
        return "entregue"
    return "rodando"


def list_flows(db: Any, *, limit: int, start: int) -> list[FlowListRow]:
    """Lista os flows mais recentes primeiro, com status/gate/elenco derivados dos tasks
    (ADR-0018 §V). Busca é 2º sacrifício do plano — não implementada aqui. `limit`/`start` são
    ints internos (paginação da store), interpolados como literais."""
    rows = db.query(_LIST_FLOWS_SQL.format(limit=int(limit), start=int(start)))  # noqa: S608
    result: list[FlowListRow] = []
    for r in rows:
        states = [str(s) for s in _as_list(r.get("task_states"))]
        gate_from = {pair[0] for pair in _pairs(r.get("gate_pairs"))}
        result.append(
            FlowListRow(
                id=str(r["id"]),
                question=str(r.get("question") or ""),
                template=str(r.get("template_name") or ""),
                status=_flow_status(states, gate_from),
                gate_open=any(s in gate_from for s in states),
                cast=[str(c) for c in _as_list(r.get("cast"))],
                tasks_open=sum(1 for s in states if s not in _TERMINAL),
                created_at=str(r.get("created_at") or ""),
            )
        )
    return result


def count_flows(db: Any) -> int:
    """Total de flows (paginação da lista). `count()` com GROUP ALL; 0 quando vazio."""
    rows = db.query("SELECT count() FROM flow GROUP ALL;")
    return rows[0]["count"] if rows else 0


def flow_board(db: Any, flow: RecordID) -> FlowBoardView | None:
    """O board de um flow: pergunta, template, COLUNAS (estados do snapshot) e os cards (tasks
    com estado + persona + flag de gate). `None` se o flow não existe."""
    head = db.query(
        "SELECT question, template_name, snapshot.board.states AS states, "
        "snapshot.board.gates AS gates FROM $f;",
        {"f": flow},
    )
    if not head:
        return None
    row: dict[str, Any] = head[0]
    gate_from = {pair[0] for pair in _pairs(row.get("gates"))}
    task_rows = db.query(
        "SELECT id, state, created_at, (->assigned_to->persona.catalog_name)[0] AS persona "
        "FROM $f<-belongs_to<-task ORDER BY created_at;",  # created_at na projeção: quirk v3
        {"f": flow},
    )
    cards = [
        FlowTaskCard(
            id=str(t["id"]),
            state=str(t["state"]),
            persona=str(t.get("persona") or ""),
            # is_gate GENÉRICO: humano num estado gate-from do snapshot (não o literal
            # `awaiting_review`) — serve report e dev-mini (`review`) sem nome fixo.
            is_gate=t.get("persona") == _HUMAN and str(t.get("state")) in gate_from,
        )
        for t in task_rows
    ]
    return FlowBoardView(
        id=str(flow),
        question=str(row.get("question") or ""),
        template=str(row.get("template_name") or ""),
        states=[str(s) for s in _as_list(row.get("states"))],
        tasks=cards,
    )


def _as_list(value: Any) -> list[Any]:
    """Coage o valor de uma projeção (Any do SDK) a lista — [] se ausente/None."""
    if value is None:
        return []
    return cast("list[Any]", value) if isinstance(value, list) else [value]


_HUMAN = "humano"


def insert_deliverable(
    db: Any,
    *,
    flow: RecordID,
    task: RecordID,
    kind: str,
    content: str,
    consulted: Sequence[RecordID],
    pr_url: str | None = None,
    pr_number: int | None = None,
) -> RecordID:
    """Grava o deliverable e sua proveniência numa ÚNICA transação atômica: o registro
    (`kind` + `content` markdown), a aresta `flow->produces->deliverable`, e uma aresta
    `task->consults->distilled` por fonte recuperada (ADR-0016 §II/§III).

    `consulted` vem do RETRIEVAL (nunca da saída do LLM — regra das citações, §VI): a
    proveniência não é forjável por injeção. O deliverable NÃO ganha chunks/embedding —
    fica fora do acervo buscável por design (E2).

    `pr_url`/`pr_number` (ADR-0019 §VI): a ref ESTRUTURAL do deliverable `kind="pr"`, vinda
    da API do GitHub (E3) — campos `option<...>` (migration 0007) só preenchidos para PR; o
    report não os carrega. `content` segue sendo o resumo untrusted do agente (E4)."""
    deliverable = _fresh("deliverable")
    create = "CREATE $d SET kind = $kind, content = $content"
    params: dict[str, Any] = {
        "d": deliverable,
        "kind": kind,
        "content": content,
        "f": flow,
        "t": task,
    }
    if pr_url is not None:
        create += ", pr_url = $pr_url, pr_number = $pr_number"
        params["pr_url"] = pr_url
        params["pr_number"] = pr_number
    stmts = [create, "RELATE $f->produces->$d"]
    for i, distilled in enumerate(consulted):
        stmts.append(f"RELATE $t->consults->$c{i}")
        params[f"c{i}"] = distilled
    run_transaction(db, stmts, params)
    return deliverable
