"""User profile routes (ADR-0045, KUBO-149).

GET /profile — profile page.
POST /profile — updates display_name, language and timezone.
POST /membership/preferences — updates the active workspace theme.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.api.session import resolve_session
from kubo.errors import ConfigError, StoreError
from kubo.store import client
from kubo.store import tenancy as tenancy_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_TEMPLATE = "profile/index.html"
_PROFILE_ROUTE = "/profile"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_WRITE_LOG = "profile.write_unavailable"
_DENIED = "Acesso negado."


class ProfileForm(BaseModel):
    """Global profile form fields (validation lives in the store)."""

    display_name: str
    language: str
    timezone: str


class ThemeForm(BaseModel):
    """Workspace theme form field (validation lives in the store)."""

    theme: str


def _profile_context(db: Any, ctx: Any) -> dict[str, Any]:
    """Builds the template context from the resolved session."""
    user = tenancy_store.get_user(db, ctx.user_id)
    if user is None:
        raise StoreError("user not found")

    profile = tenancy_store.get_user_profile(db, ctx.user_id)
    membership = tenancy_store.get_membership(db, user_id=ctx.user_id, tenant_id=ctx.tenant_id)
    theme = membership.theme if membership else "system"

    return {
        "email": user.email,
        "display_name": profile.display_name if profile else "",
        "language": profile.language if profile else "pt-BR",
        "timezone": profile.timezone if profile else "America/Sao_Paulo",
        "theme": theme,
        "avatar_seed": user.email or "",
    }


def _render_page(
    request: Request,
    *,
    notice: str | None = None,
    status: int = 200,
) -> Response:
    """Re-renders the profile page, optionally with a notice."""
    with client.connect() as ro:
        ctx = resolve_session(request, ro)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        data = _profile_context(ro, ctx)
    data["csrf"] = csrf_token(request)
    data["notice"] = notice
    return templates.TemplateResponse(request, _TEMPLATE, data, status_code=status)


@router.get("/profile")
def profile_page(request: Request) -> Response:
    """Profile page: global data plus the active workspace theme."""
    return _render_page(request)


@router.post("/profile")
def update_profile(
    request: Request,
    display_name: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    timezone: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Persists the global profile after CSRF."""
    if not verify_csrf(request, csrf):
        return _render_page(request, notice="CSRF inválido — recarregue a página.", status=403)
    form = ProfileForm(
        display_name=display_name,
        language=language,
        timezone=timezone,
    )

    with client.connect() as ro:
        ctx = resolve_session(request, ro)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)

    try:
        with client.connect_rw() as db:
            tenancy_store.update_user_profile(
                db,
                user_id=ctx.user_id,
                display_name=form.display_name,
                language=form.language,
                timezone=form.timezone,
            )
    except ConfigError:
        _log.warning(_WRITE_LOG, route="profile.update")
        return _render_page(request, notice=_WRITE_UNAVAILABLE, status=503)
    except StoreError as exc:
        return _render_page(request, notice=str(exc), status=400)

    request.session["display_name"] = form.display_name.strip()
    _log.info("profile.updated", user=str(ctx.user_id))
    return RedirectResponse(_PROFILE_ROUTE, status_code=303)


@router.post("/membership/preferences")
def update_theme(
    request: Request,
    theme: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Persists the active workspace theme after CSRF."""
    if not verify_csrf(request, csrf):
        return _render_page(request, notice="CSRF inválido — recarregue a página.", status=403)
    form = ThemeForm(theme=theme)

    with client.connect() as ro:
        ctx = resolve_session(request, ro)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)

    try:
        with client.connect_rw() as db:
            tenancy_store.update_membership_theme(
                db,
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                theme=form.theme,
            )
    except ConfigError:
        _log.warning(_WRITE_LOG, route="membership.preferences")
        return _render_page(request, notice=_WRITE_UNAVAILABLE, status=503)
    except StoreError as exc:
        return _render_page(request, notice=str(exc), status=400)

    _log.info("membership.theme.updated", user=str(ctx.user_id), theme=form.theme)
    return RedirectResponse(_PROFILE_ROUTE, status_code=303)
