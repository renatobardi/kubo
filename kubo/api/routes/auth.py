"""Authentication routes (ADR-0014 + ADR-0036): /login (GET form + POST scrypt),
/auth/firebase (POST ID token) and /logout.

Synchronous routes (`def`): `verify_password` (scrypt, ~50-150ms) and the `time.sleep(1)`
rate-limit run in Starlette's threadpool, without freezing the single-worker's event loop.
Login failure: sleep + structured log (no password/token in log) + 401.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.responses import Response
from surrealdb import RecordID

from kubo.api.auth import verify_password
from kubo.api.firebase_tokens import verify_id_token
from kubo.api.rendering import templates
from kubo.api.session import parse_record_id as _parse_record_id
from kubo.api.urls import safe_next
from kubo.errors import ConfigError, FirebaseTokenError, TeamInviteError
from kubo.store import client
from kubo.store import team_invites as team_invites_store
from kubo.store import tenancy as tenancy_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_FAIL_DELAY_SECONDS = 1
_LOGIN_TEMPLATE = "login.html"
_MEDIA_TYPE_TEXT = "text/plain"
_MSG_INVALID_SESSION = "Invalid session."
_INVITE_TOKEN_RE = re.compile("^[0-9a-f]{32}$")
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_WRITE_LOG = "auth.write_unavailable"

# Synthetic uid for sessions opened by scrypt login (break-glass).
_SCRYPT_OWNER_UID = "scrypt:owner"


def _open_session(request: Request, *, uid: str, tenant_id: str, role: str) -> None:
    """Regenerate the session (fixation) and write role + uid + tenant_id + auth timestamp."""
    request.session.clear()
    request.session["role"] = role
    request.session["uid"] = uid
    request.session["tenant_id"] = tenant_id
    request.session["auth_at"] = int(time.time())


class FirebaseLoginBody(BaseModel):
    """Body of POST /auth/firebase (hostile external input)."""

    id_token: str = Field(..., min_length=1)


def _login_context(
    request: Request,
    error: str | None = None,
    next_path: str = "/",
    invite_token: str | None = None,
) -> dict[str, Any]:
    """Login screen context: error message + Firebase config + next + invite."""
    return {
        "error": error,
        "next": safe_next(next_path),
        "invite": invite_token,
        "firebase": request.app.state.firebase_config.as_firebase_js_dict(),
    }


# Concurrency gate: at most ONE login attempt processed at a time. Without it, the
# `time.sleep(1)` on failure would only serialize per-request — N parallel connections
# gave N guesses/second and held N thread-pool threads (self-DoS of the other sync
# routes). Non-blocking acquisition: if one is already in flight, reject fast (429)
# without spending scrypt/sleep or holding a thread. Real, proportional rate-limit (ADR-0014).
_LOGIN_GATE = threading.Semaphore(1)

_SESSION_EXPIRED = "Sua sessão expirou — entre novamente."


@router.get("/login")
def login_form(
    request: Request,
    next: Annotated[str, Query()] = "",
    invite: Annotated[str, Query()] = "",
    expired: Annotated[str, Query()] = "",
) -> Response:
    """Show the login form. Already authenticated? Go straight to the safe `next`.

    `expired=1` is set by the stale-session handler (KUBO-140): the user WAS logged
    in and got sent back here, so the screen says why instead of appearing out of
    nowhere. The flag is a fixed message, never text taken from the query string."""
    if request.session.get("role") in {"owner", "member", "superadmin"}:
        return RedirectResponse(safe_next(next), status_code=303)
    notice = _SESSION_EXPIRED if expired else None
    return templates.TemplateResponse(
        request,
        _LOGIN_TEMPLATE,
        _login_context(request, notice, next_path=next, invite_token=invite or None),
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """Check the password. Correct: open session and redirect to the safe `next`. Wrong:
    sleep 1s (rate-limit), log the attempt and return 401 with the form + alert.

    One attempt at a time (non-blocking gate): if a login is already in flight, reject
    immediately (429) without spending scrypt/sleep or holding a thread-pool thread.
    """
    next_path = safe_next(next)
    if not _LOGIN_GATE.acquire(blocking=False):
        client_host = request.client.host if request.client else "unknown"
        _log.warning("api.login.busy", client=client_host)
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Tente novamente em instantes.", next_path=next_path),
            status_code=429,
        )
    try:
        if verify_password(password, request.app.state.password_hash):
            _open_session(
                request,
                uid=request.app.state.breakglass_user_id or _SCRYPT_OWNER_UID,
                tenant_id=request.app.state.breakglass_tenant_id,
                role="owner",
            )
            return RedirectResponse(next_path, status_code=303)
        time.sleep(_FAIL_DELAY_SECONDS)
        client_host = request.client.host if request.client else "unknown"
        _log.warning("api.login.failed", client=client_host)
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Senha incorreta.", next_path=next_path),
            status_code=401,
        )
    finally:
        _LOGIN_GATE.release()


@router.post("/logout")
def logout(request: Request) -> Response:
    """End the session and return to the login screen."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _firebase_config_ok(request: Request) -> bool:
    """True when a project id is configured (fail-closed).

    Owner uids are no longer required for login: self-signup creates a tenant
    automatically (ADR-0041). The allowlist is now only used for the superadmin
    role (KUBO-116).
    """
    cfg = request.app.state.firebase_config
    return bool(cfg.project_id)


def _canonical_record_id(record: RecordID) -> str:
    """Canonical string form of a RecordID, without SurrealDB escaping of special characters."""
    return f"{record.table_name}:{record.id}"


def _membership_for_tenant(memberships: Any, tenant_id: str) -> Any | None:
    """Return the membership matching tenant_id (string), or None."""
    target = _parse_record_id(tenant_id)
    if target is None:
        return None
    for m in memberships:
        if m.tenant == target:
            return m
    return None


def _membership_for_session(db: Any, uid: str, tenant_id: str) -> Any | None:
    """Load user by firebase uid and return their membership for tenant_id, or None."""
    user = tenancy_store.get_user_by_firebase_uid(db, uid)
    if user is None:
        return None
    memberships = tenancy_store.list_memberships_for_user(db, user.id)
    return _membership_for_tenant(memberships, tenant_id)


def _invite_denied(request: Request, message: str, *, next_path: str, invite: str) -> Response:
    """401 do fluxo de convite, preservando o token no form de login."""
    return templates.TemplateResponse(
        request,
        _LOGIN_TEMPLATE,
        _login_context(request, message, next_path=next_path, invite_token=invite),
        status_code=401,
    )


def _accept_invite(
    request: Request, *, uid: str, email: str | None, invite: str, next_path: str
) -> tuple[Any, str] | Response:
    """Aceita um convite de equipe: cria o user (se novo) e a membership no tenant do convite.

    ESCRITA — abre `connect_rw` (kubo_rw EDITOR, ADR-0018); `ConfigError` sobe para o
    handler traduzir em 503. Devolve `(tenant, role)` ou a resposta 401 do convite inválido.
    """
    with client.connect_rw() as db:
        tenant_user = tenancy_store.get_user_by_firebase_uid(db, uid)
        if tenant_user is None:
            tenant_user = tenancy_store.create_user(db, firebase_uid=uid, email=email)
        try:
            accepted = team_invites_store.accept_team_invite(
                db, token=invite, user_id=tenant_user.id
            )
        except TeamInviteError as exc:
            _log.warning("api.firebase.invite_failed", reason=str(exc))
            return _invite_denied(
                request,
                "Convite inválido, expirado ou já usado.",
                next_path=next_path,
                invite=invite,
            )
        tenant = tenancy_store.get_tenant(db, accepted.tenant_id)
        if tenant is None:
            return _invite_denied(request, "Convite inválido.", next_path=next_path, invite=invite)
        return tenant, accepted.role


@router.post("/auth/firebase")
def firebase_login(
    request: Request,
    body: FirebaseLoginBody,
    next: Annotated[str, Query()] = "",
    invite: Annotated[str, Query()] = "",
) -> Response:
    """Verify Firebase ID token server-side and open a session.

    With `?invite=<token>`, the login becomes a team invite acceptance: it creates
    the user (if new) and a `member` membership in the invite's tenant, never a
    new tenant.

    Fail-closed: missing config → 503; invalid token/uid → 401;
    invalid/expired invite → 401.
    """
    next_path = safe_next(next)
    if not _firebase_config_ok(request):
        _log.warning("api.firebase.config_missing")
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Login Firebase indisponível.", next_path=next_path),
            status_code=503,
        )

    try:
        token_user = verify_id_token(
            body.id_token,
            request.app.state.firebase_config.project_id,
        )
    except FirebaseTokenError as exc:
        _log.warning("api.firebase.failed", code=exc.code)
        if exc.code == "jwks_unavailable":
            return templates.TemplateResponse(
                request,
                _LOGIN_TEMPLATE,
                _login_context(request, "Login Firebase indisponível.", next_path=next_path),
                status_code=503,
            )
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, "Autenticação Firebase falhou.", next_path=next_path),
            status_code=401,
        )

    uid = token_user["uid"]
    # Todo bloco abaixo GRAVA (user, tenant, membership) — precisa do kubo_rw EDITOR.
    # Com o kubo_ro (VIEWER) do servidor o SurrealDB engole o CREATE em silêncio e o
    # signup morre em `user vanished during creation`. Molde ADR-0018: connect_rw
    # por-request, ConfigError (credencial ausente) → 503.
    try:
        if tenancy_store.is_superadmin(uid, request.app.state.superadmin_uids):
            with client.connect_rw() as db:
                user = tenancy_store.get_user_by_firebase_uid(db, uid)
                if user is None:
                    tenancy_store.create_user(
                        db,
                        firebase_uid=uid,
                        email=token_user.get("email") or None,
                    )
            _open_session(
                request,
                uid=uid,
                tenant_id=request.app.state.breakglass_tenant_id,
                role="superadmin",
            )
            return RedirectResponse(next_path, status_code=303)

        if invite:
            outcome = _accept_invite(
                request,
                uid=uid,
                email=token_user.get("email") or None,
                invite=invite,
                next_path=next_path,
            )
            if isinstance(outcome, Response):
                return outcome
            tenant, role = outcome
        else:
            with client.connect_rw() as db:
                _, tenant = tenancy_store.get_or_create_user_and_tenant(
                    db,
                    firebase_uid=uid,
                    email=token_user.get("email") or None,
                )
            role = "owner"
    except ConfigError:
        _log.warning(_WRITE_LOG, route="auth.firebase")
        # Mesma cortesia dos outros caminhos de indisponibilidade desta rota
        # (config_missing/jwks_unavailable): re-renderiza o form com a mensagem.
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, _WRITE_UNAVAILABLE, next_path=next_path),
            status_code=503,
        )

    _open_session(
        request,
        uid=uid,
        tenant_id=_canonical_record_id(tenant.id),
        role=role,
    )
    return RedirectResponse(next_path, status_code=303)


@router.get("/invite/{token}")
def invite_landing(request: Request, token: str, next: Annotated[str, Query()] = "") -> Response:
    """Public landing for a team invite: validate token format before rendering login."""
    invite_token = token if _INVITE_TOKEN_RE.match(token) else None
    if invite_token is None:
        return templates.TemplateResponse(
            request,
            _LOGIN_TEMPLATE,
            _login_context(request, error="Invalid invite link.", next_path=next),
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        _LOGIN_TEMPLATE,
        _login_context(request, next_path=next, invite_token=invite_token),
    )


@router.post("/auth/invite")
def create_invite(request: Request) -> Response:
    """Owner generates a team invite for the active tenant.

    Returns `text/plain` with the invite link; the UI can copy or render it.
    """
    session_role = request.session.get("role")
    if session_role != "owner":
        return Response("Access denied.", status_code=403, media_type=_MEDIA_TYPE_TEXT)

    tenant_id = request.session.get("tenant_id")
    uid = request.session.get("uid")
    if not tenant_id or not uid:
        return Response(_MSG_INVALID_SESSION, status_code=403, media_type=_MEDIA_TYPE_TEXT)

    tenant_record = _parse_record_id(tenant_id)
    if tenant_record is None:
        return Response(_MSG_INVALID_SESSION, status_code=403, media_type=_MEDIA_TYPE_TEXT)

    # Autorização é leitura pura (kubo_ro); só a criação do convite escreve (kubo_rw).
    with client.connect() as ro:
        membership = _membership_for_session(ro, uid, tenant_id)
    if membership is None or membership.role != "owner":
        return Response("Only owner can invite.", status_code=403, media_type=_MEDIA_TYPE_TEXT)

    try:
        with client.connect_rw() as db:
            invite = team_invites_store.create_team_invite(
                db,
                tenant_id=tenant_record,
                created_by=membership.user,
            )
    except ConfigError:
        _log.warning(_WRITE_LOG, route="auth.invite")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)

    link = f"{request.base_url}invite/{invite.token}"
    return PlainTextResponse(link)


@router.post("/auth/switch")
def switch_workspace(
    request: Request,
    tenant_id: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    """Switch the active tenant in the session.

    Owner/member só podem trocar para um tenant ao qual pertencem. Superadmin
    pode trocar para qualquer tenant existente (ADR-0041 §VI)."""
    uid = request.session.get("uid")
    if not uid:
        return Response(_MSG_INVALID_SESSION, status_code=403, media_type=_MEDIA_TYPE_TEXT)

    target = _parse_record_id(tenant_id)
    if target is None:
        return Response("Invalid tenant.", status_code=403, media_type=_MEDIA_TYPE_TEXT)

    with client.connect() as db:
        user = tenancy_store.get_user_by_firebase_uid(db, uid)
        if user is None:
            return Response(_MSG_INVALID_SESSION, status_code=403, media_type=_MEDIA_TYPE_TEXT)

        is_superadmin = tenancy_store.is_superadmin(uid, request.app.state.superadmin_uids)
        if is_superadmin:
            # Superadmin pode trocar para qualquer tenant existente, mas a
            # autorização é re-verificada na store primitive (ADR-0041 §VI).
            tenant = tenancy_store.get_tenant(db, target)
            if tenant is None:
                return Response("Tenant not allowed.", status_code=403, media_type=_MEDIA_TYPE_TEXT)
            tenancy_store.assert_membership_or_superadmin(
                db, user_id=user.id, tenant_id=target, superadmin=True
            )
            role = "superadmin"
        else:
            membership = _membership_for_session(db, uid, tenant_id)
            if membership is None:
                return Response("Tenant not allowed.", status_code=403, media_type=_MEDIA_TYPE_TEXT)
            target = membership.tenant
            role = membership.role

    _open_session(
        request,
        uid=uid,
        tenant_id=_canonical_record_id(target),
        role=role,
    )
    target = safe_next(next)
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)
