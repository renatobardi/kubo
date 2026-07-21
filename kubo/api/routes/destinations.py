"""Destinations route (ADR-0027, ticket KUBO-43): DB-backed registry, manageable from the UI.

Mirrors the Sources screen 1:1: list with full address visible, create/edit/pause/
archive/restore/delete. Writes follow ADR-0018: CSRF, per-request kubo_rw, pydantic
validation at the boundary, staleness guard (409). The "Configured artifacts" section
derives from the cron saved in `settings` (KUBO-44) and the ACTIVE destinations in the
database (KUBO-48).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.errors import (
    ConfigError,
    DuplicateDestinationError,
    StaleDestinationError,
    format_validation_error,
)
from kubo.store import client
from kubo.store import destinations as destination_store
from kubo.store import settings as settings_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_LIST_TEMPLATE = "destinations/list.html"
_EDIT_TEMPLATE = "destinations/edit.html"
_DELETE_TEMPLATE = "destinations/delete.html"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_STALE_NOTICE = "Esse destino não está mais disponível para edição."
_CSRF_INVALID = "CSRF inválido — recarregue a página."
_WRITE_LOG = "destinations.write_unavailable"
_DESTINATIONS_ROUTE = "/destinations"


@dataclass(frozen=True)
class Artefato:
    """A configured recurring artifact (the digest): name, human agenda, source and
    the active destinations that receive it — derived from `settings.digest_cron` + database."""

    name: str
    agenda: str
    origem: str
    destinos: list[str]


def _humanize_cron(cron: str) -> str:
    """Translate a daily cron `M H * * *` into 'daily at HH:MM'; otherwise return the raw cron."""
    parts = cron.split()
    if (
        len(parts) == 5
        and parts[2:] == ["*", "*", "*"]
        and parts[0].isdigit()
        and parts[1].isdigit()
    ):
        return f"diário às {int(parts[1]):02d}:{int(parts[0]):02d}"
    return cron or "—"


def _digest_artefatos(cron: str | None, destination_names: list[str]) -> list[Artefato]:
    """Deriva o artefato `Digest` a partir do cron vindo de `settings` (KUBO-44).

    O digest envia para TODOS os destinos declarados (um artefato, um conteúdo nesta
    fase — sem digest por-destino). `agenda` traduz o cron para leitura humana."""
    if not cron:
        return []
    return [
        Artefato(
            name="Digest",
            agenda=_humanize_cron(cron),
            origem="destilados novos desde o último envio",
            destinos=destination_names,
        )
    ]


class NewDestination(BaseModel):
    """Validated input for the "Add destination" form — the pydantic boundary."""

    name: str = Field(min_length=1, max_length=100)
    kind: Literal["pessoa", "sistema"]
    channel: Literal["telegram", "email"]
    address: str = Field(min_length=1, max_length=200)

    @field_validator("name", "address", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        """Trim leading/trailing whitespace from text fields."""
        return value.strip()


class EditDestination(BaseModel):
    """Validated input for the edit form: name and address are editable;
    kind/channel are read-only (they come from the database)."""

    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=200)

    @field_validator("name", "address", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


def _render_list(
    request: Request,
    *,
    notice: str | None = None,
    status: int = 200,
    db: Any = None,
) -> Response:
    """Render the destinations list: settings cron + active destinations + all
    destinations + the create form."""
    if db is None:
        with client.connect() as ro:
            return _render_list(request, notice=notice, status=status, db=ro)
    db_destinations = destination_store.list_destinations(db)
    settings = settings_store.get_settings(db)
    active = destination_store.active_destinations(db)
    cron = settings.digest_cron if settings else None
    artefatos = _digest_artefatos(cron, [d.name for d in active])
    return templates.TemplateResponse(
        request,
        _LIST_TEMPLATE,
        {
            "destinations": db_destinations,
            "artefatos": artefatos,
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


@router.get("")
def list_page(request: Request) -> Response:
    """List database destinations and configured artifacts (settings + active destinations)."""
    return _render_list(request)


@router.post("")
def create(
    request: Request,
    name: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    channel: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Register a new destination. Duplicate becomes a SOFT notice (409)."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    try:
        payload = NewDestination(name=name, kind=kind, channel=channel, address=address)  # type: ignore[arg-type]
    except ValidationError as exc:
        return _render_list(request, notice=format_validation_error(exc), status=400)
    try:
        with client.connect_rw() as db:
            try:
                destination_store.create_destination(
                    db,
                    name=payload.name,
                    kind=payload.kind,
                    channel=payload.channel,
                    address=payload.address,
                )
            except DuplicateDestinationError:
                return _render_list(
                    request, notice="This destination is already registered.", status=409, db=db
                )
            except StaleDestinationError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse(_DESTINATIONS_ROUTE, status_code=303)
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


def _render_edit(
    request: Request, detail: Any, *, notice: str | None = None, status: int = 200
) -> Response:
    """Render the edit form from a destination read from the DATABASE."""
    return templates.TemplateResponse(
        request,
        _EDIT_TEMPLATE,
        {
            "destination": detail,
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


@router.get("/{did}/edit")
def edit_page(request: Request, did: str) -> Response:
    """Edit form for ONE destination. Archived/missing redirects back to the list."""
    with client.connect() as ro:
        detail = destination_store.get_destination(ro, RecordID("destination", did))
    if detail is None or detail.archived_at is not None:
        return RedirectResponse(_DESTINATIONS_ROUTE, status_code=303)
    return _render_edit(request, detail)


@router.post("/{did}/edit")
def edit(
    request: Request,
    did: str,
    name: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Edit a destination's name and address while preserving its id."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    destination_id = RecordID("destination", did)
    try:
        payload = EditDestination(name=name, address=address)  # type: ignore[arg-type]
    except ValidationError as exc:
        with client.connect() as ro:
            detail = destination_store.get_destination(ro, destination_id)
        if detail is None or detail.archived_at is not None:
            return _render_list(request, notice=_STALE_NOTICE, status=409)
        return _render_edit(request, detail, notice=format_validation_error(exc), status=400)
    with client.connect() as ro:
        detail = destination_store.get_destination(ro, destination_id)
    if detail is None or detail.archived_at is not None:
        return _render_list(request, notice=_STALE_NOTICE, status=409)
    return _apply_edit(request, destination_id, detail, payload)


def _apply_edit(
    request: Request, destination_id: RecordID, detail: Any, payload: EditDestination
) -> Response:
    """Apply the edit, mapping duplicate (409 on the form) and staleness (409 on the list)."""
    try:
        with client.connect_rw() as db:
            try:
                destination_store.edit_destination(
                    db, id=destination_id, name=payload.name, address=payload.address
                )
            except DuplicateDestinationError:
                return _render_edit(
                    request,
                    detail,
                    notice="A destination with this address already exists.",
                    status=409,
                )
            except StaleDestinationError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse(_DESTINATIONS_ROUTE, status_code=303)
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


def _lifecycle_action(
    request: Request,
    csrf: str,
    action: Callable[[Any], None],
) -> Response:
    """Run a lifecycle action following ADR-0018:
    CSRF (403) → connect_rw (503) → store action → redirect 303."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    try:
        with client.connect_rw() as db:
            try:
                action(db)
            except StaleDestinationError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse(_DESTINATIONS_ROUTE, status_code=303)
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


@router.post("/{did}/disable")
def disable(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Pause the destination (`enabled=false`)."""
    rid = RecordID("destination", did)
    return _lifecycle_action(
        request,
        csrf,
        lambda db: destination_store.set_destination_enabled(db, id=rid, enabled=False),
    )


@router.post("/{did}/enable")
def enable(
    request: Request,
    did: str,
    csrf: Annotated[str, Form()] = "",
    mode: Annotated[str, Form()] = "backlog",
) -> Response:
    """Resume a paused destination (`enabled=true`). mode=recente advances the watermark."""
    rid = RecordID("destination", did)

    def _action(db: Any) -> None:
        destination = destination_store.get_destination(db, rid)
        if destination is None:
            raise StaleDestinationError(f"destination not found: {rid}")
        destination_store.set_destination_enabled(
            db, id=rid, enabled=True, mode=mode, destination=destination
        )

    return _lifecycle_action(request, csrf, _action)


@router.post("/{did}/archive")
def archive(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Archive a destination (soft delete)."""
    rid = RecordID("destination", did)
    return _lifecycle_action(
        request, csrf, lambda db: destination_store.archive_destination(db, id=rid)
    )


@router.post("/{did}/restore")
def restore(
    request: Request,
    did: str,
    csrf: Annotated[str, Form()] = "",
    mode: Annotated[str, Form()] = "backlog",
) -> Response:
    """Restore an archived destination. mode=recente advances the watermark."""
    rid = RecordID("destination", did)

    def _action(db: Any) -> None:
        destination = destination_store.get_destination(db, rid)
        if destination is None:
            raise StaleDestinationError(f"destination not found: {rid}")
        destination_store.restore_destination(db, id=rid, mode=mode, destination=destination)

    return _lifecycle_action(request, csrf, _action)


def _render_delete(
    request: Request, detail: Any, dispatches: int, *, notice: str | None = None, status: int = 200
) -> Response:
    """Delete confirmation screen: zero dispatches → offer POST; >0 → advise to archive."""
    return templates.TemplateResponse(
        request,
        _DELETE_TEMPLATE,
        {
            "destination": detail,
            "dispatches": dispatches,
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


@router.get("/{did}/delete")
def delete_page(request: Request, did: str) -> Response:
    """Delete confirmation screen: the double-check."""
    rid = RecordID("destination", did)
    with client.connect() as ro:
        detail = destination_store.get_destination(ro, rid)
        dispatches = (
            destination_store.destination_dispatch_count(ro, rid) if detail is not None else 0
        )
    if detail is None:
        return RedirectResponse(_DESTINATIONS_ROUTE, status_code=303)
    return _render_delete(request, detail, dispatches)


@router.post("/{did}/delete")
def delete(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Hard-delete a destination with zero dispatches (also clears default settings pointer)."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    rid = RecordID("destination", did)
    try:
        with client.connect_rw() as db:
            detail = destination_store.get_destination(db, rid)
            if detail is None:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            dispatches = destination_store.destination_dispatch_count(db, rid)
            if dispatches > 0:
                return _render_delete(
                    request,
                    detail,
                    dispatches,
                    notice="Esse destino tem envios e não pode ser apagado — arquive.",
                    status=409,
                )
            try:
                destination_store.delete_destination(db, id=rid)
            except destination_store.DestinationHasHistoryError:
                return _render_delete(
                    request,
                    detail,
                    destination_store.destination_dispatch_count(db, rid),
                    notice="Esse destino tem envios e não pode ser apagado — arquive.",
                    status=409,
                )
            return RedirectResponse(_DESTINATIONS_ROUTE, status_code=303)
    except ConfigError:
        _log.warning(_WRITE_LOG)
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
