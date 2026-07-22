"""Webhook inbound do Telegram (ADR-0033, KUBO-62/69).

`/telegram/webhook` é PÚBLICO por natureza: Telegram bate nele sem sessão do Kubo.
Proteção por `X-Telegram-Bot-Api-Secret-Token` (obrigatório). Respostas são sempre
200 para evitar retry storms do Telegram; falhas vão para log estruturado.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

import structlog
from anyio import to_thread
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.responses import Response

from kubo.errors import (
    ConfigError,
    DuplicateDestinationError,
    StaleInviteError,
    StoreError,
)
from kubo.store import client
from kubo.store import invites as invite_store

_SECRET_TOKEN = os.environ.get("KUBO_TELEGRAM_WEBHOOK_SECRET", "").strip()
if not _SECRET_TOKEN:
    raise ConfigError(
        "KUBO_TELEGRAM_WEBHOOK_SECRET é obrigatório para o webhook do Telegram (ADR-0033)"
    )

_log = structlog.get_logger(__name__)
router = APIRouter()


def _verify_secret(request: Request) -> bool:
    """Compara o header do Telegram com o secret configurado (tempo constante)."""
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(received, _SECRET_TOKEN)


def _extract_start_token(body: dict[str, Any]) -> str | None:
    """Extrai o token de convite de uma mensagem `/start <token>`.

    Telegram envia `/start <payload>` quando o usuário clica num deep link.
    Ignora variações como `/start@botname token`.
    """
    message = body.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text", "")
    if not isinstance(text, str):
        return None
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    if not parts[0].lower().startswith("/start"):
        return None
    return parts[1].strip()


def _process_invite(body: dict[str, Any]) -> None:
    """Lógica síncrona de aceite do convite — roda num worker thread."""
    token = _extract_start_token(body)
    if token is None:
        _log.debug("telegram_webhook_no_start_token")
        return

    try:
        with client.connect_rw() as db:
            invite = invite_store.get_invite_by_token(db, token)
            if invite is None:
                _log.info("telegram_webhook_invite_not_found", token=token)
                return

            chat = body["message"].get("chat", {})
            chat_id = chat.get("id")
            if chat_id is None:
                _log.warning("telegram_webhook_missing_chat_id")
                return

            try:
                invite_store.accept_invite(db, invite_id=invite.id, chat_id=str(chat_id))
            except DuplicateDestinationError:
                _log.warning(
                    "telegram_webhook_chat_id_already_registered",
                    invite_id=str(invite.id),
                    token=token,
                )
            except StaleInviteError:
                _log.info(
                    "telegram_webhook_stale_invite",
                    invite_id=str(invite.id),
                    token=token,
                )
            except StoreError:
                _log.exception("telegram_webhook_store_error", invite_id=str(invite.id))
    except ConfigError:
        _log.error("telegram_webhook_config_error")


@router.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Recebe updates do Telegram; trata apenas `/start <token>` de convite."""
    if not _verify_secret(request):
        _log.warning("telegram_webhook_unauthorized")
        return PlainTextResponse("unauthorized", status_code=401)

    try:
        body = await request.json()
    except ValueError:
        _log.warning("telegram_webhook_invalid_json")
        return JSONResponse({"status": "ok"}, status_code=200)

    await to_thread.run_sync(_process_invite, body)
    return JSONResponse({"status": "ok"}, status_code=200)
