"""Rotas de Fluxos (ADR-0018 §V/§VI): lista de flows → board do flow (cards=tasks) →
GateSheet (contexto + decisão). As DUAS ações de escrita da UI (D38) vivem aqui e SÓ aqui:
aprovar/rejeitar um gate. Toda escrita usa `connect_rw` (kubo_rw EDITOR) por-request,
protegida por CSRF (synchronizer token) + guarda de staleness (409) + fail-fast 503 sem a
credencial. O `deliverable.content` é renderizado como TEXTO PLANO (autoescape do Jinja,
`white-space: pre-wrap`) — NUNCA markdown→HTML (untrusted no consumo, ADR-0016 §II).

Leituras usam `client.connect` (kubo_ro). Rotas SÍNCRONAS (`def`, threadpool — ADR-0014).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.distribution.destinations import (
    ResolvedDestination,
    load_destinations,
    resolve_base_url,
    resolve_destinations,
)
from kubo.errors import ConfigError, SenderError, StateError
from kubo.runtime.flow_runner import reject_gate, resume_gate
from kubo.store import client
from kubo.store.flows import (
    FlowBoardView,
    count_flows,
    flow_board,
    list_flows,
    read_gate_context,
)

_log = structlog.get_logger(__name__)
router = APIRouter()

_PAGE_SIZE = 20
_OWNER_DESTINATION = "owner-telegram"
_DESTINATIONS_PATH = Path(__file__).parents[3] / "destinations.yaml"
_LIST_TEMPLATE = "flows/list.html"
_BOARD_TEMPLATE = "flows/board.html"


@router.get("")
def list_page(
    request: Request,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = _PAGE_SIZE,
) -> Response:
    """Lista de Fluxos (paridade FlowsScreen): nome (pergunta), template, badge de gate, status
    derivado e elenco. Busca é 2º sacrifício do plano — não implementada."""
    size = clamp_size(size)
    start = clamp_start(start)
    with client.connect() as db:
        flows = list_flows(db, limit=size, start=start)
        total = count_flows(db)
    return templates.TemplateResponse(
        request,
        _LIST_TEMPLATE,
        {"flows": flows, "start": start, "size": size, "total": total},
    )


@router.get("/{flow_key}")
def board_page(request: Request, flow_key: str) -> Response:
    """Board de UM flow: colunas = estados do snapshot, cards = tasks. O card de gate abre o
    GateSheet (contexto + decisão). Flow inexistente → volta à lista."""
    flow = RecordID("flow", flow_key)
    with client.connect() as db:
        board = flow_board(db, flow)
        if board is None:
            return RedirectResponse("/flows", status_code=303)
        gate_ctx = _gate_context(db, board)
    return _render_board(request, board, gate_ctx)


@router.post("/gate/approve")
def approve(
    request: Request,
    task: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Aprova o gate (D38): envia o relatório e leva as 2 tasks a delivered. CSRF + staleness +
    fail-fast 503. Falha de envio deixa o gate ABERTO (at-least-once) e mostra o board com aviso."""
    return _decide(request, task=task, csrf=csrf, approve=True)


@router.post("/gate/reject")
def reject(
    request: Request,
    task: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
) -> Response:
    """Rejeita o gate (D38) com MOTIVO obrigatório: arquiva o flow (2 tasks → rejected). CSRF +
    staleness + fail-fast 503. Motivo vazio → 400 com o board reaberto."""
    return _decide(request, task=task, csrf=csrf, approve=False, reason=reason)


def _decide(request: Request, *, task: str, csrf: str, approve: bool, reason: str = "") -> Response:
    """Núcleo comum das 2 ações de escrita: valida CSRF, resolve a credencial de escrita, checa
    staleness e delega ao runtime (resume_gate|reject_gate). Casca: nenhuma regra de gate mora
    aqui — o runtime decide, a store transaciona."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    gate_task = _parse_task_id(task)
    if gate_task is None:
        return PlainTextResponse("id de task inválido.", status_code=400)
    if not approve and not reason.strip():
        return PlainTextResponse("Motivo é obrigatório na rejeição.", status_code=400)
    try:
        with client.connect_rw() as db:
            return _apply_decision(request, db, gate_task, approve=approve, reason=reason)
    except ConfigError:  # connect_rw fail-fast (sem KUBO_RW_SURREAL_PASS) ou config de envio
        _log.warning("flows.write_unavailable")
        return PlainTextResponse(
            "Escrita indisponível (credencial kubo_rw ausente).", status_code=503
        )


def _apply_decision(
    request: Request, db: object, gate_task: RecordID, *, approve: bool, reason: str
) -> Response:
    """Com a conexão de ESCRITA aberta: staleness (409) → decisão → redirect ao board. Envio
    falho na aprovação (SenderError) reabre o board com aviso; o gate segue aberto."""
    if _task_state(db, gate_task) != "awaiting_review":
        return _reopen_board(
            request, gate_task, notice="Esta decisão já foi tomada.", status=409, db=db
        )
    try:
        if approve:
            destination, base_url = _owner_delivery()
            resume_gate(db, gate_task=gate_task, destination=destination, base_url=base_url)
        else:
            reject_gate(db, gate_task=gate_task, reason=reason)
    except SenderError:
        return _reopen_board(
            request,
            gate_task,
            notice="Falha ao enviar no Telegram; tente de novo.",
            status=502,
            db=db,
        )
    except StateError:
        return _reopen_board(
            request, gate_task, notice="Esta decisão já foi tomada.", status=409, db=db
        )
    return RedirectResponse(f"/flows/{_flow_key(db, gate_task)}", status_code=303)


def _owner_delivery() -> tuple[ResolvedDestination, str]:
    """Resolve o destino do dono (owner-telegram) + a base URL dos links, só do que a aprovação
    precisa (resolve SÓ esse destino — um e-mail sem env não pode quebrar a aprovação)."""
    raw = next(
        (d for d in load_destinations(_DESTINATIONS_PATH) if d.id == _OWNER_DESTINATION), None
    )
    if raw is None:
        raise ConfigError(f"destino '{_OWNER_DESTINATION}' não existe em destinations.yaml")
    return resolve_destinations([raw])[0], resolve_base_url()


def _gate_context(db: object, board: FlowBoardView) -> object | None:
    """Contexto do GateSheet do card de gate (se houver): prosa + fontes das arestas."""
    gate = next((c for c in board.tasks if c.is_gate), None)
    if gate is None:
        return None
    task = _parse_task_id(gate.id)
    return read_gate_context(db, task) if task is not None else None


def _render_board(
    request: Request, board: FlowBoardView, gate_ctx: object, *, notice: str = "", status: int = 200
) -> Response:
    """Renderiza o board com o token CSRF e o contexto do gate (para o GateSheet)."""
    return templates.TemplateResponse(
        request,
        _BOARD_TEMPLATE,
        {"board": board, "gate": gate_ctx, "csrf": csrf_token(request), "notice": notice},
        status_code=status,
    )


def _reopen_board(
    request: Request, gate_task: RecordID, *, notice: str, status: int, db: object | None = None
) -> Response:
    """Reabre o board do flow do gate com um aviso (staleness/erro). Usa a conexão dada (de
    escrita) ou abre uma de leitura — a re-renderização mostra o estado atual (fonte da verdade)."""
    if db is not None:
        return _reopen_with(request, db, gate_task, notice, status)
    with client.connect() as ro:
        return _reopen_with(request, ro, gate_task, notice, status)


def _reopen_with(
    request: Request, db: object, gate_task: RecordID, notice: str, status: int
) -> Response:
    """Renderiza o board do flow ao qual o gate pertence, com aviso e status dados."""
    flow = _flow_of(db, gate_task)
    if flow is None:
        return RedirectResponse("/flows", status_code=303)
    board = flow_board(db, flow)
    if board is None:
        return RedirectResponse("/flows", status_code=303)
    return _render_board(request, board, _gate_context(db, board), notice=notice, status=status)


def _parse_task_id(raw: str) -> RecordID | None:
    """`task:<key>` ou `<key>` → RecordID da tabela `task` (nunca deixa o form escolher a
    tabela — senão a escrita viraria porta para qualquer registro). Inválido → None."""
    key = raw.strip()
    if not key:
        return None
    if ":" in key:
        table, _, key = key.partition(":")
        if table != "task" or not key:
            return None
    return RecordID("task", key)


def _task_state(db: object, task: RecordID) -> str | None:
    """Estado atual de um task (staleness); None se não existe."""
    rows = db.query("SELECT VALUE state FROM $t;", {"t": task})  # type: ignore[attr-defined]
    return str(rows[0]) if rows else None


def _flow_of(db: object, task: RecordID) -> RecordID | None:
    """O flow ao qual um task pertence (belongs_to), ou None."""
    rows = db.query("SELECT VALUE (->belongs_to->flow)[0] FROM $t;", {"t": task})  # type: ignore[attr-defined]
    return rows[0] if rows and rows[0] is not None else None


def _flow_key(db: object, task: RecordID) -> str:
    """A KEY do flow do task (para o redirect `/flows/<key>`)."""
    flow = _flow_of(db, task)
    return str(flow).partition(":")[2] if flow is not None else ""
