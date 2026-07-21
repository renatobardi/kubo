"""Rotas de Fluxos (ADR-0018 §V/§VI): lista de flows → board do flow (cards=tasks) →
GateSheet (contexto + decisão). As DUAS ações de escrita da UI (D38) vivem aqui e SÓ aqui:
aprovar/rejeitar um gate. Toda escrita usa `connect_rw` (kubo_rw EDITOR) por-request,
protegida por CSRF (synchronizer token) + guarda de staleness (409) + fail-fast 503 sem a
credencial. O `deliverable.content` é renderizado como TEXTO PLANO (autoescape do Jinja,
`white-space: pre-wrap`) — NUNCA markdown→HTML (untrusted no consumo, ADR-0016 §II).

Leituras usam `client.connect` (kubo_ro). Rotas SÍNCRONAS (`def`, threadpool — ADR-0014).
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.distribution.config import resolve_base_url
from kubo.errors import ConfigError, ForgeError, PromotionError, SenderError, StateError
from kubo.runtime.flow_runner import promote_gate, reject_gate, resume_gate
from kubo.store import client
from kubo.store import settings as settings_store
from kubo.store.destinations import Destination
from kubo.store.flows import (
    FlowBoardView,
    count_flows,
    flow_board,
    flow_of_task,
    list_flows,
    read_gate_context,
)

_log = structlog.get_logger(__name__)
router = APIRouter()

_PAGE_SIZE = 20
_LIST_PATH = "/flows"
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
            return RedirectResponse(_LIST_PATH, status_code=303)
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
    reason: Annotated[str, Form(max_length=2000)] = "",
) -> Response:
    """Rejeita o gate (D38) com MOTIVO obrigatório: arquiva o flow (2 tasks → rejected). CSRF +
    staleness + fail-fast 503. Motivo vazio → 400 com o board reaberto; motivo além do cap (ADR-0018
    §VI, borda pydantic) → 422 antes de qualquer escrita."""
    return _decide(request, task=task, csrf=csrf, approve=False, reason=reason)


@router.post("/gate/promote")
def promote(
    request: Request,
    task: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
    worker_name: Annotated[str, Form()] = "",
) -> Response:
    """Confirma a promoção (ADR-0021 §9) — a 3ª porta de escrita da UI, rota PRÓPRIA (a validação
    de segurança do import-oráculo não fica escondida atrás de um `if` no approve genérico). CSRF
    + staleness + fail-fast 503. `worker_name` vazio → 400 com o board reaberto."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    gate_task = _parse_task_id(task)
    if gate_task is None:
        return PlainTextResponse("id de task inválido.", status_code=400)
    if not worker_name.strip():
        return PlainTextResponse("Nome do worker é obrigatório.", status_code=400)
    try:
        with client.connect_rw() as db:
            return _apply_promotion(request, db, gate_task, worker_name=worker_name.strip())
    except ConfigError:
        _log.warning("flows.write_unavailable")
        return PlainTextResponse("Escrita indisponível por erro de configuração.", status_code=503)


def _apply_promotion(
    request: Request, db: object, gate_task: RecordID, *, worker_name: str
) -> Response:
    """Com a conexão de ESCRITA aberta: staleness (409) → `promote_gate` → redirect ao board.
    `PromotionError` (PR não mesclado / worker fora do registry — E10) reabre o board com aviso
    LEGÍVEL do próprio erro; o gate segue aberto (at-least-once, o dono relê e reclica).

    `ConfigError` do `promote_gate` (token read-only ausente — env do sandbox, ADR-0021 §9) é
    capturado AQUI, distinto do `ConfigError` de `connect_rw` na rota: aquele é "escrita
    indisponível", este é "falta o token de LEITURA" — mensagens diferentes evitam depurar no
    escuro (achado do advisor antes do smoke)."""
    if read_gate_context(db, gate_task) is None:
        return _reopen_board(
            request, gate_task, notice="Esta decisão já foi tomada.", status=409, db=db
        )
    try:
        promote_gate(db, gate_task=gate_task, worker_name=worker_name)
    except PromotionError as exc:
        return _reopen_board(request, gate_task, notice=str(exc), status=422, db=db)
    except ForgeError:
        _log.warning("flows.promote_forge_unavailable")
        return _reopen_board(
            request,
            gate_task,
            notice="Não foi possível consultar o GitHub. Tente novamente.",
            status=502,
            db=db,
        )
    except ConfigError:
        _log.warning("flows.promote_config_unavailable")
        return _reopen_board(
            request,
            gate_task,
            notice="Confirmação indisponível: falta configuração (token read-only do GitHub "
            "ou coordenadas do sandbox).",
            status=503,
            db=db,
        )
    except StateError:
        return _reopen_board(
            request, gate_task, notice="Esta decisão já foi tomada.", status=409, db=db
        )
    return RedirectResponse(f"/flows/{_flow_key(db, gate_task)}", status_code=303)


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
    except ConfigError:  # connect_rw sem KUBO_RW_SURREAL_PASS OU destino/base_url do envio
        # Mensagem genérica: o ConfigError pode vir de connect_rw (credencial) OU de
        # _owner_delivery/resume_gate (destino/base_url) — não afirma uma causa específica.
        _log.warning("flows.write_unavailable")
        return PlainTextResponse("Escrita indisponível por erro de configuração.", status_code=503)


def _apply_decision(
    request: Request, db: object, gate_task: RecordID, *, approve: bool, reason: str
) -> Response:
    """Com a conexão de ESCRITA aberta: staleness (409) → decisão → redirect ao board. Efeito
    externo falho (SenderError no envio do analysis, ForgeError no close do PR do dev) reabre o
    board com aviso; o gate segue aberto (at-least-once)."""
    # Staleness GENÉRICO (não o literal `awaiting_review`): read_gate_context é o oráculo de
    # "gate humano ABERTO" — None se já decidido/inválido. Roda ANTES do efeito externo, então
    # um gate dev já resolvido não dispara um close de PR à toa.
    if read_gate_context(db, gate_task) is None:
        return _reopen_board(
            request, gate_task, notice="Esta decisão já foi tomada.", status=409, db=db
        )
    try:
        if approve:
            destination, base_url = _owner_delivery(db)
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
    except ForgeError:
        return _reopen_board(
            request,
            gate_task,
            notice="Falha ao fechar o PR no GitHub; tente de novo.",
            status=502,
            db=db,
        )
    except StateError:
        return _reopen_board(
            request, gate_task, notice="Esta decisão já foi tomada.", status=409, db=db
        )
    return RedirectResponse(f"/flows/{_flow_key(db, gate_task)}", status_code=303)


def _owner_delivery(db: Any) -> tuple[Destination, str]:
    """Resolve o destino padrão nas configurações + a base URL para links.

    Aprovação ignora `distribution_paused` (ação explícita do dono, ADR-0028 §6).
    Um destino arquivado/dangling/NONE falha com uma mensagem apontando para Configurações."""
    settings = settings_store.get_settings(db)
    if settings is None:
        raise ConfigError("configurações não encontradas — configure o destino padrão")
    destination = settings_store.resolve_default_destination(db, settings)
    return destination, resolve_base_url()


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
    flow = flow_of_task(db, gate_task)
    if flow is None:
        return RedirectResponse(_LIST_PATH, status_code=303)
    board = flow_board(db, flow)
    if board is None:
        return RedirectResponse(_LIST_PATH, status_code=303)
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


def _flow_key(db: object, task: RecordID) -> str:
    """A KEY do flow do task (para o redirect `/flows/<key>`), via a store (invariante 2)."""
    flow = flow_of_task(db, task)
    return str(flow).partition(":")[2] if flow is not None else ""
