"""Contrato da store de `invite` (ADR-0033, tickets KUBO-58/68/69).

Integração (SurrealDB real): ciclo de vida do convite — criar, expirar, reenviar,
aceite (cria destination) e colisão de chat_id. Status é calculado, não armazenado.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from kubo.errors import (
    DuplicateDestinationError,
    InviteNotResendableError,
    StaleInviteError,
)
from kubo.store import client, destinations, invites, migrations

pytestmark = pytest.mark.integration


_INVITES_DB = "test_invites"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_INVITES_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_INVITES_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_INVITES_DB};")


def _seconds_from_now(value: datetime) -> float:
    """Diferença em segundos entre um datetime do banco e agora (UTC)."""
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        return (value - now.replace(tzinfo=None)).total_seconds()
    return (value - now).total_seconds()


def test_create_invite_generates_token_and_seven_day_expiry(db: Any) -> None:
    """Criar convite gera token, deixa accepted_at vazio e expira em ~7 dias."""
    invite = invites.create_invite(db, name="Marina", email="marina@exemplo.com")

    assert invite.name == "Marina"
    assert invite.email == "marina@exemplo.com"
    assert invite.accepted_at is None
    assert invite.status == "pending"
    assert len(invite.token) == 32
    # TTL de 7 dias com tolerância de 1 minuto para o tempo de execução.
    assert 7 * 24 * 3600 - 60 < _seconds_from_now(invite.expires_at) < 7 * 24 * 3600 + 60


def test_create_invite_without_email_is_allowed(db: Any) -> None:
    """Convite sem e-mail é válido — entrega cai no link copiável."""
    invite = invites.create_invite(db, name="Sem email")
    assert invite.email is None
    assert invite.status == "pending"


def test_get_invite_by_token(db: Any) -> None:
    """Busca por token devolve o convite correto."""
    created = invites.create_invite(db, name="Marina")
    found = invites.get_invite_by_token(db, created.token)
    assert found is not None
    assert str(found.id) == str(created.id)


def test_get_invite_by_token_missing_returns_none(db: Any) -> None:
    """Token inexistente retorna None (sem exceção)."""
    assert invites.get_invite_by_token(db, "não-existe") is None


def test_list_invites_orders_by_created_desc(db: Any) -> None:
    """Listagem traz convites do mais recente para o mais antigo."""
    invites.create_invite(db, name="Primeiro")
    invites.create_invite(db, name="Segundo")

    rows = invites.list_invites(db)
    assert [r.name for r in rows] == ["Segundo", "Primeiro"]
    assert all(r.status == "pending" for r in rows)
    assert all(len(r.token) == 32 for r in rows)


def test_status_expired_when_expires_at_passed(db: Any) -> None:
    """Status vira 'expired' depois do prazo."""
    invite = invites.create_invite(db, name="Marina")
    # Simula passagem do tempo alterando expires_at para o passado.
    db.query(
        "UPDATE $r SET expires_at = time::now() - 1s;",
        {"r": invite.id},
    )
    updated = invites.get_invite(db, invite.id)
    assert updated is not None
    assert updated.status == "expired"


def test_resend_rejects_non_expired_invite(db: Any) -> None:
    """Reenviar só é permitido quando o convite já expirou."""
    invite = invites.create_invite(db, name="Marina")
    with pytest.raises(InviteNotResendableError):
        invites.resend_invite(db, invite.id)


def test_resend_updates_token_and_expires_at(db: Any) -> None:
    """Reenviar de convite expirado gera token novo e reconta o prazo."""
    invite = invites.create_invite(db, name="Marina")
    db.query(
        "UPDATE $r SET expires_at = time::now() - 1s;",
        {"r": invite.id},
    )
    old_token = invite.token

    resent = invites.resend_invite(db, invite.id)
    assert resent.token != old_token
    assert resent.status == "pending"
    assert 7 * 24 * 3600 - 60 < _seconds_from_now(resent.expires_at) < 7 * 24 * 3600 + 60


def test_resend_rejects_already_accepted_invite(db: Any) -> None:
    """Convite aceito não pode ser reenviado."""
    invite = invites.create_invite(db, name="Marina")
    invites.accept_invite(db, invite_id=invite.id, chat_id="123456")
    with pytest.raises(InviteNotResendableError):
        invites.resend_invite(db, invite.id)


def test_accept_invite_creates_destination_and_marks_accepted(db: Any) -> None:
    """Aceite cria destination ativo e marca accepted_at."""
    invite = invites.create_invite(db, name="Marina")
    destination_id = invites.accept_invite(db, invite_id=invite.id, chat_id="123456")

    updated = invites.get_invite(db, invite.id)
    assert updated is not None
    assert updated.accepted_at is not None
    assert updated.status == "accepted"

    destination = destinations.get_destination(db, destination_id)
    assert destination is not None
    assert destination.name == "Marina"
    assert destination.kind == "pessoa"
    assert destination.channel == "telegram"
    assert destination.address == "123456"
    assert destination.enabled is True


def test_accept_invite_rejects_duplicate_chat_id(db: Any) -> None:
    """chat_id já cadastrado em outro destination recusa o aceite."""
    destinations.create_destination(
        db, name="Dono", kind="pessoa", channel="telegram", address="123456"
    )
    invite = invites.create_invite(db, name="Marina")

    with pytest.raises(DuplicateDestinationError):
        invites.accept_invite(db, invite_id=invite.id, chat_id="123456")

    updated = invites.get_invite(db, invite.id)
    assert updated is not None
    assert updated.accepted_at is None


def test_accept_invite_rejects_expired_invite(db: Any) -> None:
    """Convite expirado não pode ser aceito."""
    invite = invites.create_invite(db, name="Marina")
    db.query(
        "UPDATE $r SET expires_at = time::now() - 1s;",
        {"r": invite.id},
    )
    with pytest.raises(StaleInviteError):
        invites.accept_invite(db, invite_id=invite.id, chat_id="123456")


def test_accept_invite_rejects_reused_invite(db: Any) -> None:
    """Convite já aceito não pode ser aceito de novo."""
    invite = invites.create_invite(db, name="Marina")
    invites.accept_invite(db, invite_id=invite.id, chat_id="123456")
    with pytest.raises(StaleInviteError):
        invites.accept_invite(db, invite_id=invite.id, chat_id="999999")
