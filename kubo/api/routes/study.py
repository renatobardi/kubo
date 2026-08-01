"""Rotas de Estudos (ADR-0047, KUBO-161): Tema container de N Materiais.

O Tema nasce vazio (`draft`) e o dono adiciona Materiais dentro dele. Esta fatia
(KUBO-161) entrega só o esqueleto: criar Tema vazio, listar, tela de draft e
rename inline. Upload de Materiais (KUBO-162), conversa com mentor (KUBO-163),
plano (KUBO-164/165) e ativação (KUBO-166) vêm nas fatias seguintes.

Rotas SÍNCRONAS (store bloqueante), leitura no molde de `entities.py` e escrita no
molde ADR-0018 de `settings.py`. Dado pessoal: toda rota resolve a sessão e opera
com `tenant_id`/`user_id` do contexto.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.routing import APIRoute
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.api.session import SessionContext, resolve_session
from kubo.errors import ConfigError, MembershipRequiredError, StoreError
from kubo.store import client
from kubo.store import study as study_store

_log = structlog.get_logger(__name__)

_NOT_A_MEMBER = "Estudos é pessoal: sua conta não pertence a este workspace."


class _MembershipAwareRoute(APIRoute):
    """Traduz `MembershipRequiredError` em 403 para TODA rota deste router.

    Um ponto de tradução em vez de N try/except — e no router do módulo, não em
    `app.add_exception_handler`, que é GLOBAL e mudaria as rotas que hoje deixam a
    exceção subir de propósito.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Envelopa o handler original com a recusa legível."""
        handler = super().get_route_handler()

        async def _translated(request: Request) -> Response:
            try:
                return await handler(request)
            except MembershipRequiredError:
                _log.warning("study.membership_required", path=_log_key(request.url.path))
                return PlainTextResponse(_NOT_A_MEMBER, status_code=403)

        return _translated


router = APIRouter(route_class=_MembershipAwareRoute)

_TOPICS_LIST_TEMPLATE = "study/topics.html"
_TOPIC_TEMPLATE = "study/topic.html"
_TOPIC_NOT_FOUND_TEMPLATE = "study/topic_not_found.html"
_TOPICS_ROUTE = "/study/topics"
_TOPIC_TABLE = "topic"

_DENIED = "Acesso negado."
_CSRF_INVALID = "CSRF inválido — recarregue a página."
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_EMPTY_TITLE = "O nome do estudo não pode ser vazio."

_MAX_LOG_KEY = 64

# Rótulos de estado do Tema (ADR-0047 §3) — a lista e o detalhe usam os mesmos.
_STATE_LABELS: dict[str, str] = {
    "draft": "Rascunho",
    "planning": "Planejando",
    "scheduled": "Agendado",
    "running": "Em andamento",
    "archived": "Arquivado",
}


def _log_key(raw: str) -> str:
    """Chave da URL pronta para virar CAMPO DE LOG: aparada e truncada."""
    return raw.strip()[:_MAX_LOG_KEY]


def _session_of(request: Request) -> SessionContext | None:
    """Só a sessão — sem leitura de lista, que as escritas não usam."""
    with client.connect() as db:
        return resolve_session(request, db)


def _topic_of(db: Any, key: str, ctx: SessionContext) -> study_store.Topic | None:
    """Tema do usuário pela chave da URL; None quando não existe ou é de outro."""
    topic_key = key.strip()
    if not topic_key:
        return None
    return study_store.get_topic(
        db,
        topic_id=RecordID(_TOPIC_TABLE, topic_key),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )


def _topic_missing(request: Request, key: str) -> Response:
    """404 da tela do tema — tema de outro usuário é INEXISTENTE, não 'negado'."""
    return templates.TemplateResponse(
        request, _TOPIC_NOT_FOUND_TEMPLATE, {"raw": key}, status_code=404
    )


def _topic_url(topic_id: RecordID) -> str:
    """URL da tela do tema."""
    return f"{_TOPICS_ROUTE}/{topic_id.id}"


@router.get("/topics")
def list_topics_page(request: Request) -> Response:
    """Lista os temas do usuário com nome e estado."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        topics = study_store.list_topics(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    rows = [{"topic": t, "state_label": _STATE_LABELS.get(t.state, t.state)} for t in topics]
    return templates.TemplateResponse(
        request, _TOPICS_LIST_TEMPLATE, {"rows": rows, "csrf": csrf_token(request)}
    )


@router.post("/topics")
def create_topic(
    request: Request,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Cria um Tema vazio em `draft` e redireciona pra tela do Tema."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    try:
        with client.connect_rw() as db:
            topic = study_store.create_topic(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                title="Estudo sem nome",
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.topic.store_failed")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_topic_url(topic.id), status_code=303)


@router.get("/topics/{key}")
def topic_detail(request: Request, key: str) -> Response:
    """Tela do tema: estado vazio guiado (draft sem Materiais)."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        topic = _topic_of(db, key, ctx)
        if topic is None:
            return _topic_missing(request, key)
    return templates.TemplateResponse(
        request,
        _TOPIC_TEMPLATE,
        {
            "topic": topic,
            "state_label": _STATE_LABELS.get(topic.state, topic.state),
            "csrf": csrf_token(request),
        },
    )


@router.post("/topics/{key}/rename")
def rename_topic(
    request: Request,
    key: str,
    title: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Renomeia o tema inline (editável em estados não-arquivados)."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    title = title.strip()
    if not title:
        return PlainTextResponse(_EMPTY_TITLE, status_code=400)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    try:
        with client.connect_rw() as db:
            topic = _topic_of(db, key, ctx)
            if topic is None:
                return _topic_missing(request, key)
            study_store.set_topic_name(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                title=title,
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.topic.rename_failed", topic=_log_key(key))
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_topic_url(topic.id), status_code=303)
