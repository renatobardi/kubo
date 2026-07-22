"""Convite de onboarding de destino Telegram (ADR-0033, KUBO-58).

A tabela `invite` é separada de `destination` (ADR-0033 §2): o destino só nasce no
aceite. O token do convite é PII de roteamento — `repr=False` nos logs/tracebacks.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from surrealdb import RecordID

from kubo.errors import (
    DuplicateDestinationError,
    InviteNotResendableError,
    StaleInviteError,
    StoreError,
)
from kubo.store import transaction
from kubo.store.destinations import normalize_address


def _as_datetime(value: Any) -> datetime:
    """Normaliza um valor de datetime vindo do SurrealDB (datetime ou ISO string)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise StoreError(f"invalid datetime value: {type(value).__name__}")


@dataclass(frozen=True)
class Invite:
    """Convite pendente/aceito/expirado para um destino Telegram.

    `token` e `email` são PII/sensíveis — `repr=False` impede vazamento em logs.
    Status é calculado a partir de `accepted_at` e `expires_at`, sem campo redundante.
    """

    id: RecordID
    name: str
    email: str | None = field(repr=False)
    token: str = field(repr=False)
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime

    @property
    def status(self) -> str:
        """pending | expired | accepted, calculado no momento da leitura."""
        if self.accepted_at is not None:
            return "accepted"
        now = datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return "expired" if self.expires_at <= now else "pending"


def _fresh_invite_id() -> RecordID:
    """Novo id surrogate para um convite."""
    return RecordID("invite", secrets.token_hex(16))


def _invite_from_row(row: dict[str, Any]) -> Invite:
    """Constroi um `Invite` a partir de uma linha do banco."""
    accepted = row.get("accepted_at")
    return Invite(
        id=row["id"],
        name=row["name"],
        email=row.get("email"),
        token=row["token"],
        expires_at=_as_datetime(row["expires_at"]),
        accepted_at=_as_datetime(accepted) if accepted is not None else None,
        created_at=_as_datetime(row["created_at"]),
    )


def create_invite(db: Any, *, name: str, email: str | None = None) -> Invite:
    """Cria um convite com token único e TTL de 7 dias.

    `email` é opcional: quando ausente, a entrega cai no link copiável.
    """
    rid = _fresh_invite_id()
    token = secrets.token_hex(16)
    db.query(
        "CREATE $r SET name = $name, email = $email, token = $invite_token, "
        "expires_at = time::now() + 7d, accepted_at = NONE, created_at = time::now();",
        {
            "r": rid,
            "name": name.strip(),
            "email": email.strip() if email else None,
            "invite_token": token,
        },
    )
    invite = get_invite(db, rid)
    if invite is None:
        raise StoreError("invite vanished during creation")
    return invite


def get_invite(db: Any, id: RecordID) -> Invite | None:
    """Lê um convite pelo id."""
    rows = db.query("SELECT * FROM $r;", {"r": id})
    return _invite_from_row(rows[0]) if rows else None


def get_invite_by_token(db: Any, token: str) -> Invite | None:
    """Busca um convite pelo token único."""
    rows = db.query(
        "SELECT * FROM invite WHERE token = $invite_token LIMIT 1;",
        {"invite_token": token},
    )
    return _invite_from_row(rows[0]) if rows else None


def list_invites(db: Any) -> list[Invite]:
    """Lista todos os convites, do mais recente para o mais antigo."""
    rows = db.query("SELECT * FROM invite ORDER BY created_at DESC;")
    return [_invite_from_row(r) for r in rows]


def resend_invite(db: Any, id: RecordID) -> Invite:
    """Reenvia um convite expirado: gera token novo e reconta o TTL de 7 dias.

    Rejeita convites pendentes ou já aceitos (`InviteNotResendableError`).
    """
    new_token = secrets.token_hex(16)
    rows = db.query(
        "UPDATE $r SET token = $new_token, expires_at = time::now() + 7d "
        "WHERE accepted_at IS NONE AND expires_at <= time::now() RETURN AFTER;",
        {"r": id, "new_token": new_token},
    )
    if not rows:
        raise InviteNotResendableError("invite not resendable")
    return _invite_from_row(rows[0])


def accept_invite(db: Any, *, invite_id: RecordID, chat_id: str) -> RecordID:
    """Aceita um convite pendente/não-expirado e cria o destination Telegram.

    A operação é atômica: verifica `UNIQUE(channel, address)` ANTES de marcar
    o convite, depois cria o `destination`. Em colisão de chat_id levanta
    `DuplicateDestinationError`; em convite inválido/expirado/reusado levanta
    `StaleInviteError`.
    """
    normalized = normalize_address("telegram", chat_id)
    destination_id = RecordID("destination", secrets.token_hex(16))

    statements = [
        "LET $existing = (SELECT id FROM destination "
        "WHERE channel = $channel AND address = $address)",
        "IF count($existing) > 0 { THROW 'DuplicateChatIdError' }",
        "LET $updated = (UPDATE $r SET accepted_at = time::now() "
        "WHERE accepted_at IS NONE AND expires_at > time::now() RETURN AFTER)",
        "IF count($updated) == 0 { THROW 'StaleInviteError' }",
        "CREATE $dest SET name = $updated[0].name, kind = 'pessoa', channel = $channel, "
        "address = $address, enabled = true, archived_at = NONE",
    ]
    try:
        transaction.run_transaction(
            db,
            statements,
            {
                "r": invite_id,
                "channel": "telegram",
                "address": normalized,
                "dest": destination_id,
            },
        )
    except StoreError as exc:
        detail = str(exc)
        if "DuplicateChatIdError" in detail:
            raise DuplicateDestinationError("chat_id already registered: channel=telegram") from exc
        if "StaleInviteError" in detail:
            raise StaleInviteError("invite invalid or expired") from exc
        raise
    return destination_id
