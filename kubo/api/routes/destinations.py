"""Rota de Destinos (ADR-0027, ticket KUBO-43): Cadastro DB-backed, gerenciável pela UI.

Espelha 1:1 a tela de Fontes: lista com endereço completo visível, criar/editar/pausar/
arquivar/reativar/apagar. Escrita no molde ADR-0018: CSRF, kubo_rw por-request,
validação pydantic na borda, guarda de staleness (409). A entrega continua usando o
`destinations.yaml` (cutover é KUBO-48): a seção "Artefatos configurados" ainda deriva
de `schedules.yaml` + `destinations.yaml`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
import yaml
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.distribution.destinations import Destination, load_destinations
from kubo.errors import (
    ConfigError,
    DestinationHasHistoryError,
    DuplicateDestinationError,
    StaleDestinationError,
    format_validation_error,
)
from kubo.store import client
from kubo.store import destinations as destination_store

_log = structlog.get_logger(__name__)
router = APIRouter()

_REPO_ROOT = Path(__file__).parents[3]
_DESTINATIONS_PATH = _REPO_ROOT / "destinations.yaml"
_SCHEDULES_PATH = _REPO_ROOT / "schedules.yaml"
_LIST_TEMPLATE = "destinations/list.html"
_EDIT_TEMPLATE = "destinations/edit.html"
_DELETE_TEMPLATE = "destinations/delete.html"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_STALE_NOTICE = "Esse destino não está mais disponível para edição."


@dataclass(frozen=True)
class Artefato:
    """Um artefato recorrente configurado (o digest): nome, agenda humana, origem e
    os destinos que o recebem — derivado do schedules.yaml + destinations.yaml."""

    name: str
    agenda: str
    origem: str
    destinos: list[str]


def _humanize_cron(cron: str) -> str:
    """Traduz um cron diário `M H * * *` para 'diário às HH:MM'; senão devolve o cron cru."""
    parts = cron.split()
    if (
        len(parts) == 5
        and parts[2:] == ["*", "*", "*"]
        and parts[0].isdigit()
        and parts[1].isdigit()
    ):
        return f"diário às {int(parts[1]):02d}:{int(parts[0]):02d}"
    return cron or "—"


def _digest_artefatos(schedules_path: Path, destinations: list[Destination]) -> list[Artefato]:
    """Deriva os artefatos configurados dos entries `digest` do schedules.yaml.

    O digest envia para TODOS os destinos declarados (um artefato, um conteúdo nesta
    fase — sem digest por-destino). `agenda` traduz o cron para leitura humana."""
    raw = yaml.safe_load(schedules_path.read_text(encoding="utf-8"))
    entries = raw.get("schedules", []) if isinstance(raw, dict) else []
    nomes = [d.name for d in destinations]
    artefatos: list[Artefato] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("worker") == "digest":
            artefatos.append(
                Artefato(
                    name="Digest",
                    agenda=_humanize_cron(str(entry.get("cron", ""))),
                    origem="destilados novos desde o último envio",
                    destinos=nomes,
                )
            )
    return artefatos


class NewDestination(BaseModel):
    """Entrada validada do form "Adicionar destino" — a fronteira pydantic."""

    name: str = Field(min_length=1, max_length=100)
    kind: Literal["pessoa", "sistema"]
    channel: Literal["telegram", "email"]
    address: str = Field(min_length=1, max_length=200)

    @field_validator("name", "address", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        """Tira espaços antes/depois dos campos de texto."""
        return value.strip()

    @model_validator(mode="after")
    def _block_email_until_worker(self) -> NewDestination:
        """Canal e-mail é barrado até o worker de e-mail existir (KUBO-47)."""
        if self.channel == "email":
            raise ValueError("Canal e-mail ainda não está habilitado.")
        return self


class EditDestination(BaseModel):
    """Entrada validada do form de edição: nome e endereço são editáveis;
    kind/channel são read-only (vêm do banco)."""

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
    """Renderiza a lista de Destinos: artefatos (YAML) + destinos (DB) + form."""
    if db is None:
        with client.connect() as ro:
            db_destinations = destination_store.list_destinations(ro)
    else:
        db_destinations = destination_store.list_destinations(db)
    yaml_destinations = load_destinations(_DESTINATIONS_PATH)
    artefatos = _digest_artefatos(_SCHEDULES_PATH, yaml_destinations)
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
    """Lista os destinos do banco e os artefatos configurados (YAML, ainda não cutover)."""
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
    """Cadastra um destino novo. E-mail é barrado até KUBO-47; duplicata vira aviso
    SOFT (409)."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
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
                    request, notice="Esse destino já está cadastrado.", status=409, db=db
                )
            return RedirectResponse("/destinations", status_code=303)
    except ConfigError:
        _log.warning("destinations.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


def _render_edit(
    request: Request, detail: Any, *, notice: str | None = None, status: int = 200
) -> Response:
    """Renderiza o form de edição a partir do destino do BANCO."""
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
    """Form de edição de UM destino. Arquivado/inexistente volta para a lista."""
    with client.connect() as ro:
        detail = destination_store.get_destination(ro, RecordID("destination", did))
    if detail is None or detail.archived_at is not None:
        return RedirectResponse("/destinations", status_code=303)
    return _render_edit(request, detail)


@router.post("/{did}/edit")
def edit(
    request: Request,
    did: str,
    name: Annotated[str, Form()] = "",
    address: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Edita nome e endereço de um destino, preservando id."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
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
    """Aplica a edição, mapeando duplicata (409 no form) e staleness (409 na lista)."""
    try:
        with client.connect_rw() as db:
            try:
                destination_store.edit_destination(
                    db, id=destination_id, name=payload.name, address=payload.address
                )
            except DuplicateDestinationError:
                return _render_edit(
                    request, detail, notice="Já existe um destino com esse endereço.", status=409
                )
            except StaleDestinationError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse("/destinations", status_code=303)
    except ConfigError:
        _log.warning("destinations.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


def _lifecycle_action(request: Request, csrf: str, action: Callable[[Any], None]) -> Response:
    """Executa uma ação de ciclo de vida (pausar/retomar/arquivar/reativar) no molde
    ADR-0018: CSRF (403) → connect_rw (503) → ação da store → redirect 303."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    try:
        with client.connect_rw() as db:
            try:
                action(db)
            except StaleDestinationError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse("/destinations", status_code=303)
    except ConfigError:
        _log.warning("destinations.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


@router.post("/{did}/disable")
def disable(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Pausa o destino (`enabled=false`)."""
    rid = RecordID("destination", did)
    return _lifecycle_action(
        request,
        csrf,
        lambda db: destination_store.set_destination_enabled(db, id=rid, enabled=False),
    )


@router.post("/{did}/enable")
def enable(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Retoma um destino pausado (`enabled=true`)."""
    rid = RecordID("destination", did)
    return _lifecycle_action(
        request,
        csrf,
        lambda db: destination_store.set_destination_enabled(db, id=rid, enabled=True),
    )


@router.post("/{did}/archive")
def archive(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Arquiva um destino (soft delete)."""
    rid = RecordID("destination", did)
    return _lifecycle_action(
        request, csrf, lambda db: destination_store.archive_destination(db, id=rid)
    )


@router.post("/{did}/restore")
def restore(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Restaura um destino arquivado."""
    rid = RecordID("destination", did)
    return _lifecycle_action(
        request, csrf, lambda db: destination_store.restore_destination(db, id=rid)
    )


def _render_delete(
    request: Request, detail: Any, dispatches: int, *, notice: str | None = None, status: int = 200
) -> Response:
    """Tela de confirmação de apagar: zero dispatches → oferece POST; >0 → orienta arquivar."""
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
    """Tela de confirmação de apagar: a dupla verificação."""
    rid = RecordID("destination", did)
    with client.connect() as ro:
        detail = destination_store.get_destination(ro, rid)
        dispatches = (
            destination_store.destination_dispatch_count(ro, rid) if detail is not None else 0
        )
    if detail is None:
        return RedirectResponse("/destinations", status_code=303)
    return _render_delete(request, detail, dispatches)


@router.post("/{did}/delete")
def delete(request: Request, did: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Apaga de vez um destino com ZERO dispatches."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    rid = RecordID("destination", did)
    try:
        with client.connect_rw() as db:
            try:
                destination_store.delete_destination(db, id=rid)
            except DestinationHasHistoryError:
                detail = destination_store.get_destination(db, rid)
                if detail is None:
                    return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
                dispatches = destination_store.destination_dispatch_count(db, rid)
                return _render_delete(
                    request,
                    detail,
                    dispatches,
                    notice="Esse destino tem envios e não pode ser apagado — arquive.",
                    status=409,
                )
            except StaleDestinationError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse("/destinations", status_code=303)
    except ConfigError:
        _log.warning("destinations.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
