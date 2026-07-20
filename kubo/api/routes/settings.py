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
from kubo.errors import ConfigError, format_validation_error
from kubo.store import client
from kubo.store import destinations as destination_store
from kubo.store import settings as settings_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_TEMPLATE = "settings/index.html"
_SETTINGS_ROUTE = "/settings"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_WRITE_LOG = "settings.write_unavailable"


def _default_cron() -> str:
    """Valor de fábrica do digest: 09:30, como o schedules.yaml legado."""
    return "30 9 * * *"


class SettingsForm(BaseModel):
    """Entrada validada do form de Configurações."""

    digest_cron: str
    distribution_paused_raw: str = "false"
    default_destination_raw: str = ""

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


def _render_page(
    request: Request,
    settings: settings_store.Settings | None,
    choices: list[destination_store.Destination],
    *,
    notice: str | None = None,
    status: int = 200,
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
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


@router.get("")
def settings_page(request: Request) -> Response:
    """Tela de Configurações: lê settings + destinos elegíveis."""
    with client.connect() as ro:
        settings = settings_store.get_settings(ro)
        choices = settings_store.default_destination_choices(ro)
    return _render_page(request, settings, choices)


@router.post("")
def update_settings(
    request: Request,
    digest_cron: Annotated[str, Form()] = _default_cron(),
    distribution_paused: Annotated[str, Form()] = "false",
    default_destination: Annotated[str, Form()] = "",
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
        )
    except ValidationError as exc:
        with client.connect() as ro:
            settings = settings_store.get_settings(ro)
            choices = settings_store.default_destination_choices(ro)
        return _render_page(
            request, settings, choices, notice=format_validation_error(exc), status=400
        )

    if form.default_destination is not None:
        with client.connect() as ro:
            try:
                temp = settings_store.Settings(
                    id=RecordID("settings", "global"),
                    digest_cron=form.digest_cron,
                    distribution_paused=form.distribution_paused,
                    default_destination=form.default_destination,
                )
                settings_store.resolve_default_destination(ro, temp)
            except ConfigError as exc:
                settings = settings_store.get_settings(ro)
                choices = settings_store.default_destination_choices(ro)
                return _render_page(request, settings, choices, notice=str(exc), status=400)

    try:
        with client.connect_rw() as db:
            settings_store.put_settings(
                db,
                digest_cron=form.digest_cron,
                distribution_paused=form.distribution_paused,
                default_destination=form.default_destination,
            )
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_SETTINGS_ROUTE, status_code=303)
