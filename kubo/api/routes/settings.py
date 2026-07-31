"""Rota de Configurações (KUBO-44): agenda do digest, pausa da distribuição e
 destino padrão editáveis pela UI.

Escrita no molde ADR-0018: CSRF, kubo_rw por-request, validação pydantic na borda
(cron via `CronTrigger.from_crontab`), fail-fast 503 sem a credencial. Singleton
`settings:global`, sem staleness.
"""

from __future__ import annotations

from typing import Annotated
from zoneinfo import ZoneInfo

import structlog
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel, ValidationError, field_validator, model_validator
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.api.session import resolve_session
from kubo.errors import ConfigError, format_validation_error
from kubo.store import client
from kubo.store import destinations as destination_store
from kubo.store import settings as settings_store
from kubo.store import tenancy as tenancy_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_TEMPLATE = "settings/index.html"
_SETTINGS_ROUTE = "/settings"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_WRITE_LOG = "settings.write_unavailable"
_DENIED = "Acesso negado."


def _default_cron() -> str:
    """Valor de fábrica do digest: 09:30, como o schedules.yaml legado."""
    return "30 9 * * *"


class SettingsForm(BaseModel):
    """Entrada validada do form de Configurações."""

    digest_cron: str
    distribution_paused_raw: str = "false"
    default_destination_raw: str = ""
    unpause_mode_raw: str = "backlog"

    @field_validator("digest_cron", mode="after")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        """Cron precisa ser parseável pelo APScheduler (fail-fast na borda)."""
        v = v.strip()
        # A timezone de validação é a mesma do schedules.yaml (America/Sao_Paulo);
        # a string do cron em si não muda com a timezone.
        try:
            CronTrigger.from_crontab(v, timezone=ZoneInfo("America/Sao_Paulo"))
        except ValueError as exc:
            raise ValueError("cron inválido") from exc
        return v

    @field_validator("unpause_mode_raw", mode="after")
    @classmethod
    def _valid_unpause_mode(cls, v: str) -> str:
        """Modo de unpause global: backlog (padrão) ou recente."""
        return destination_store.valid_unpause_mode(v)

    @model_validator(mode="after")
    def _normalize(self) -> "SettingsForm":
        """Checkbox não marcado não envia valor; string vazia de destino = None."""
        self.distribution_paused_raw = self.distribution_paused_raw.strip().lower()
        self.default_destination_raw = self.default_destination_raw.strip()
        return self

    @property
    def distribution_paused(self) -> bool:
        """True quando o checkbox foi enviado como 'on', 'true', '1' ou 'yes'."""
        return self.distribution_paused_raw in ("on", "true", "1", "yes")

    @property
    def default_destination(self) -> RecordID | None:
        """RecordID do destino padrão ou None quando o select está vazio."""
        if not self.default_destination_raw:
            return None
        return RecordID("destination", self.default_destination_raw)

    @property
    def unpause_mode(self) -> str:
        """Modo escolhido ao despausar a distribuição global."""
        return self.unpause_mode_raw


def _render_page(
    request: Request,
    settings: settings_store.Settings | None,
    choices: list[destination_store.Destination],
    *,
    notice: str | None = None,
    status: int = 200,
    unpause_mode: str = "backlog",
    work_context: str = "",
) -> Response:
    """Renderiza a tela de Configurações com os valores atuais e as opções de destino."""
    return templates.TemplateResponse(
        request,
        _TEMPLATE,
        {
            "digest_cron": settings.digest_cron if settings else _default_cron(),
            "distribution_paused": settings.distribution_paused if settings else False,
            "default_destination": settings.default_destination if settings else None,
            "choices": choices,
            "unpause_mode": unpause_mode,
            "csrf": csrf_token(request),
            "notice": notice,
            "work_context": work_context,
        },
        status_code=status,
    )


def _session_work_context(request: Request, db: object) -> str:
    """Contexto de trabalho do usuário da sessão, ou vazio sem sessão resolvível.

    A tela renderiza mesmo sem membership real (fixtures de settings usam o
    singleton global) — identidade só é obrigatória no POST /settings/profile.
    """
    ctx = resolve_session(request, db)
    if ctx is None:
        return ""
    user = tenancy_store.get_user(db, ctx.user_id)
    return (user.work_context or "") if user else ""


@router.get("")
def settings_page(request: Request) -> Response:
    """Tela de Configurações: lê settings + destinos elegíveis + perfil do usuário."""
    with client.connect() as ro:
        ctx = resolve_session(request, ro)
        if ctx is None:
            return PlainTextResponse("Acesso negado.", status_code=403)
        settings = settings_store.get_settings(ro)
        choices = settings_store.default_destination_choices(ro)
        work_context = _session_work_context(request, ro)
    return _render_page(request, settings, choices, work_context=work_context)


@router.post("")
def update_settings(
    request: Request,
    digest_cron: Annotated[str, Form()] = _default_cron(),
    distribution_paused: Annotated[str, Form()] = "false",
    default_destination: Annotated[str, Form()] = "",
    unpause_mode: Annotated[str, Form()] = "backlog",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Persiste as configurações operacionais após validação pydantic + CSRF."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    try:
        form = SettingsForm(
            digest_cron=digest_cron,
            distribution_paused_raw=distribution_paused,
            default_destination_raw=default_destination,
            unpause_mode_raw=unpause_mode,
        )
    except ValidationError as exc:
        with client.connect() as ro:
            ctx = resolve_session(request, ro)
            if ctx is None:
                return PlainTextResponse("Acesso negado.", status_code=403)
            settings = settings_store.get_settings(ro)
            choices = settings_store.default_destination_choices(ro)
        return _render_page(
            request,
            settings,
            choices,
            notice=format_validation_error(exc),
            status=400,
            unpause_mode=destination_store.normalize_unpause_mode(unpause_mode),
        )

    try:
        with client.connect_rw() as db:
            ctx = resolve_session(request, db)
            if ctx is None:
                return PlainTextResponse("Acesso negado.", status_code=403)
            if form.default_destination is not None:
                temp = settings_store.Settings(
                    id=RecordID("settings", "global"),
                    digest_cron=form.digest_cron,
                    distribution_paused=form.distribution_paused,
                    default_destination=form.default_destination,
                )
                try:
                    settings_store.resolve_default_destination(db, temp)
                except ConfigError as exc:
                    settings = settings_store.get_settings(db)
                    choices = settings_store.default_destination_choices(db)
                    return _render_page(request, settings, choices, notice=str(exc), status=400)

            old_settings = settings_store.get_settings(db)
            unpause_recent = (
                old_settings is not None
                and old_settings.distribution_paused
                and not form.distribution_paused
                and form.unpause_mode == "recente"
            )
            destinations = destination_store.active_destinations(db) if unpause_recent else []
            settings_store.put_settings_and_reset(
                db,
                digest_cron=form.digest_cron,
                distribution_paused=form.distribution_paused,
                default_destination=form.default_destination,
                unpause_recent=unpause_recent,
                destinations=destinations,
                tenant_id=ctx.tenant_id,
            )
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_SETTINGS_ROUTE, status_code=303)


class ProfileForm(BaseModel):
    """Validação de borda do perfil: contexto de trabalho curto, sem espaço sobrando."""

    work_context: str

    @field_validator("work_context")
    @classmethod
    def _strip_and_cap(cls, v: str) -> str:
        text = v.strip()
        if len(text) > 4000:
            raise ValueError("contexto de trabalho passa de 4000 caracteres")
        return text


def _current_page(request: Request, *, notice: str, status: int) -> Response:
    """Re-renderiza a tela de Configurações preservando os valores atuais."""
    with client.connect() as ro:
        settings = settings_store.get_settings(ro)
        choices = settings_store.default_destination_choices(ro)
        work_context = _session_work_context(request, ro)
    return _render_page(
        request, settings, choices, notice=notice, status=status, work_context=work_context
    )


@router.post("/profile")
def update_profile(
    request: Request,
    work_context: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Grava o contexto de trabalho do USUÁRIO da sessão (ADR-0043).

    O texto entra em prompts de personas — a UI avisa para nunca incluir segredos.
    """
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    try:
        form = ProfileForm(work_context=work_context)
    except ValidationError as exc:
        return _current_page(request, notice=format_validation_error(exc), status=400)

    with client.connect() as ro:
        ctx = resolve_session(request, ro)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    try:
        with client.connect_rw() as db:
            tenancy_store.update_user_work_context(
                db, user_id=ctx.user_id, work_context=form.work_context
            )
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    _log.info("settings.profile.updated", user=str(ctx.user_id), chars=len(form.work_context))
    return RedirectResponse(_SETTINGS_ROUTE, status_code=303)
