"""Catálogo de personas (KUBO-221): leitura geral, edição restrita ao owner.

A rota de escrita segue o molde ADR-0018: CSRF, kubo_rw por-request, validação
pydantic na borda, fail-fast 503 quando a credencial de escrita está ausente.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.api.session import resolve_session
from kubo.errors import ConfigError, StoreError, format_validation_error
from kubo.llm.registry import get_ignored_params
from kubo.runtime.personas import Persona
from kubo.store import catalog as catalog_store
from kubo.store import client
from kubo.store.scoped import scoped

_log = structlog.get_logger(__name__)
router = APIRouter()

_TEMPLATE = "personas/index.html"
_PERSONAS_ROUTE = "/personas"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_WRITE_LOG = "personas.write_unavailable"
_DENIED = "Acesso negado."
_CSRF_INVALID = "CSRF inválido — recarregue a página."


class PersonaUpdateForm(BaseModel):
    """Campos editáveis da configuração LLM de uma persona."""

    model_config = ConfigDict(extra="forbid")

    model: str
    max_tokens: int
    temperature: float
    reasoning_effort: str = ""
    timeout: float

    @field_validator("reasoning_effort", mode="after")
    @classmethod
    def _empty_is_none(cls, v: str) -> str | None:
        """String vazia no form significa ausência de reasoning_effort."""
        return v or None


def _persona_context(persona: Persona) -> dict[str, Any]:
    """Dict de contexto de template para uma persona, incluindo campos ignorados."""
    ignored = get_ignored_params(
        persona.model or "",
        temperature=persona.temperature,
        reasoning_effort=persona.reasoning_effort,
    )
    return {
        "persona": persona,
        "ignored": ignored,
    }


def _render_page(
    request: Request,
    personas: list[Persona],
    *,
    notice: str | None = None,
    status: int = 200,
) -> Response:
    """Renderiza a tela de personas com as configurações vigentes."""
    return templates.TemplateResponse(
        request,
        _TEMPLATE,
        {
            "personas": [_persona_context(p) for p in personas],
            "model_choices": [
                "anthropic/claude-haiku-4-5",
                "anthropic/claude-sonnet-5",
                "anthropic/claude-opus-5",
                "groq/llama-3.3-70b-versatile",
            ],
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


def _can_write(role: str) -> bool:
    """Somente owner e superadmin podem editar o catálogo de personas."""
    return role in ("owner", "superadmin")


def _update_persona(session: Any, name: str, form: PersonaUpdateForm) -> Persona:
    """Lê a persona atual, aplica os campos editáveis e persiste com changelog."""
    row = catalog_store.get_persona(session, name=name)
    if row is None:
        raise StoreError(f"persona '{name}' não encontrada")

    current = Persona.model_validate(row)
    updated = Persona(
        name=current.name,
        executor=current.executor,
        model=form.model,
        prompt=current.prompt,
        permissions=current.permissions,
        max_tokens=form.max_tokens,
        temperature=form.temperature,
        reasoning_effort=form.reasoning_effort,
        timeout=form.timeout,
        max_turns=current.max_turns,
    )
    persisted = catalog_store.upsert_persona(session, persona=updated.model_dump())
    return Persona.model_validate(persisted)


@router.get("/personas")
def personas_page(request: Request) -> Response:
    """Lista as personas do tenant com as configurações de LLM vigentes."""
    with client.connect() as ro:
        ctx = resolve_session(request, ro)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        session = scoped(ro, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        rows = catalog_store.list_personas(session)
    personas = [Persona.model_validate(r) for r in rows]
    return _render_page(request, personas)


@router.post("/personas/{name}")
def update_persona(
    request: Request,
    name: str,
    model: str = Form(""),
    max_tokens: str = Form("1024"),
    temperature: str = Form("0.0"),
    reasoning_effort: str = Form(""),
    timeout: str = Form("60.0"),
    csrf: str = Form(""),
) -> Response:
    """Persiste a configuração de LLM de uma persona; só owner/superadmin."""
    if not verify_csrf(request, csrf):
        return _render_page(request, [], notice=_CSRF_INVALID, status=403)

    try:
        form = PersonaUpdateForm(
            model=model,
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            reasoning_effort=reasoning_effort,
            timeout=float(timeout),
        )
    except (ValidationError, ValueError) as form_error:
        with client.connect() as ro:
            ctx = resolve_session(request, ro)
            if ctx is None:
                return PlainTextResponse(_DENIED, status_code=403)
            session = scoped(ro, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
            rows = catalog_store.list_personas(session)
        notice = (
            format_validation_error(form_error)
            if isinstance(form_error, ValidationError)
            else str(form_error)
        )
        return _render_page(
            request,
            [Persona.model_validate(r) for r in rows],
            notice=notice,
            status=400,
        )

    try:
        with client.connect_rw() as db:
            ctx = resolve_session(request, db)
            if ctx is None:
                return PlainTextResponse(_DENIED, status_code=403)
            if not _can_write(ctx.role):
                return PlainTextResponse(_DENIED, status_code=403)
            session = scoped(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
            _update_persona(session, name, form)
    except ConfigError:
        _log.warning(_WRITE_LOG, route="personas.update", persona=name)
        return _render_page(request, [], notice=_WRITE_UNAVAILABLE, status=503)
    except StoreError as exc:
        with client.connect() as ro:
            ctx = resolve_session(request, ro)
            if ctx is None:
                return PlainTextResponse(_DENIED, status_code=403)
            session = scoped(ro, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
            rows = catalog_store.list_personas(session)
        return _render_page(
            request,
            [Persona.model_validate(r) for r in rows],
            notice=str(exc),
            status=400,
        )

    _log.info("persona.updated", persona=name, user=str(ctx.user_id))
    return RedirectResponse(_PERSONAS_ROUTE, status_code=303)
