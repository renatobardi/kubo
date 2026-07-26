"""Schema de tenancy e primitiva de autorização por membership (ADR-0039, KUBO-113).

Entidades:
- `tenant`: equipe/workspace.
- `user`: identidade humana (distinta de `persona`, que é papel de agente).
- `membership`: relação N:N `user -> tenant` com `role` `owner` | `member`.

Toda operação tenant-scoped deve chamar `assert_membership` no `kubo/store/` antes de
executar (ADR-0039 §II). Exatamente 1 `owner` por tenant é verificado em código, não
por constraint de banco (suporte não confirmado na v3.1.5 pinada pelo ADR-0005).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from surrealdb import RecordID

from kubo.errors import DuplicateOwnerError, MembershipRequiredError, StoreError
from kubo.store import transaction


@dataclass(frozen=True)
class Tenant:
    """Equipe/workspace de usuários do Kubo."""

    id: RecordID
    name: str
    created_at: datetime


@dataclass(frozen=True)
class User:
    """Identidade humana (Firebase), distinta de `persona` (papel de agente).

    `email` é PII/sensível — `repr=False` impede vazamento em logs.
    """

    id: RecordID
    firebase_uid: str
    email: str | None = field(repr=False)
    created_at: datetime


@dataclass(frozen=True)
class Membership:
    """Relação N:N entre `user` e `tenant`, com papel `owner` ou `member`."""

    id: RecordID
    user: RecordID
    tenant: RecordID
    role: str
    created_at: datetime


def _as_datetime(value: Any) -> datetime:
    """Normaliza um valor de datetime vindo do SurrealDB (datetime ou ISO string)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise StoreError(f"invalid datetime value: {type(value).__name__}")


def _fresh_id(table: str) -> RecordID:
    """Novo id surrogate para uma tabela."""
    return RecordID(table, secrets.token_hex(16))


def _tenant_from_row(row: dict[str, Any]) -> Tenant:
    """Constroi um `Tenant` a partir de uma linha do banco."""
    return Tenant(
        id=row["id"],
        name=row["name"],
        created_at=_as_datetime(row["created_at"]),
    )


def _user_from_row(row: dict[str, Any]) -> User:
    """Constroi um `User` a partir de uma linha do banco."""
    return User(
        id=row["id"],
        firebase_uid=row["firebase_uid"],
        email=row.get("email"),
        created_at=_as_datetime(row["created_at"]),
    )


def _membership_from_row(row: dict[str, Any]) -> Membership:
    """Constroi um `Membership` a partir de uma linha do banco."""
    return Membership(
        id=row["id"],
        user=row["in"],
        tenant=row["out"],
        role=row["role"],
        created_at=_as_datetime(row["created_at"]),
    )


def create_user(db: Any, *, firebase_uid: str, email: str | None = None) -> User:
    """Cria um `user` novo com firebase_uid único."""
    user_id = _fresh_id("user")
    db.query(
        "CREATE $u SET firebase_uid = $uid, email = $email, created_at = time::now();",
        {
            "u": user_id,
            "uid": firebase_uid.strip(),
            "email": email.strip() if email else None,
        },
    )
    user = get_user(db, user_id)
    if user is None:
        raise StoreError("user vanished during creation")
    return user


def get_user_by_firebase_uid(db: Any, firebase_uid: str) -> User | None:
    """Busca um user pelo firebase_uid, ou None se não existe."""
    rows = db.query(
        "SELECT * FROM user WHERE firebase_uid = $uid LIMIT 1;",
        {"uid": firebase_uid},
    )
    return _user_from_row(rows[0]) if rows else None


def get_user(db: Any, user_id: RecordID) -> User | None:
    """Lê um user pelo id."""
    rows = db.query("SELECT * FROM $u;", {"u": user_id})
    return _user_from_row(rows[0]) if rows else None


def create_tenant(db: Any, *, name: str, owner_user_id: RecordID) -> Tenant:
    """Cria um tenant novo e uma membership `owner` para o usuário indicado.

    A criação é atômica: tenant + membership(owner) numa única transação.
    """
    tenant_id = _fresh_id("tenant")
    transaction.run_transaction(
        db,
        [
            "CREATE $t SET name = $name, created_at = time::now()",
            "RELATE $u->membership->$t SET role = 'owner', created_at = time::now()",
        ],
        {"t": tenant_id, "name": name.strip(), "u": owner_user_id},
    )
    tenant = get_tenant(db, tenant_id)
    if tenant is None:
        raise StoreError("tenant vanished during creation")
    return tenant


def get_tenant(db: Any, tenant_id: RecordID) -> Tenant | None:
    """Lê um tenant pelo id."""
    rows = db.query("SELECT * FROM $t;", {"t": tenant_id})
    return _tenant_from_row(rows[0]) if rows else None


def create_membership(db: Any, *, user_id: RecordID, tenant_id: RecordID, role: str) -> Membership:
    """Cria uma membership `user -> tenant` com papel `owner` ou `member`.

    Rejeita `role='owner'` se o tenant já tiver um owner (`DuplicateOwnerError`).
    """
    if role == "owner":
        existing = db.query(
            "SELECT * FROM membership WHERE out = $t AND role = 'owner' LIMIT 1;",
            {"t": tenant_id},
        )
        if existing:
            raise DuplicateOwnerError("tenant already has an owner")

    rows = db.query(
        "RELATE $u->membership->$t SET role = $role, created_at = time::now() RETURN AFTER;",
        {"u": user_id, "t": tenant_id, "role": role},
    )
    if not rows:
        raise StoreError("membership creation failed")
    return _membership_from_row(rows[0])


def list_memberships_for_user(db: Any, user_id: RecordID) -> list[Membership]:
    """Lista todas as memberships de um user."""
    rows = db.query(
        "SELECT * FROM membership WHERE in = $u;",
        {"u": user_id},
    )
    return [_membership_from_row(r) for r in rows]


def list_memberships_for_tenant(db: Any, tenant_id: RecordID) -> list[Membership]:
    """Lista todas as memberships de um tenant."""
    rows = db.query(
        "SELECT * FROM membership WHERE out = $t;",
        {"t": tenant_id},
    )
    return [_membership_from_row(r) for r in rows]


def assert_membership(db: Any, *, user_id: RecordID, tenant_id: RecordID) -> None:
    """Garante que o user pertence ao tenant; levanta `MembershipRequiredError` se não.

    Esta é a primitiva de autorização do ADR-0039 §II: toda operação tenant-scoped
    no `kubo/store/` deve chamá-la antes de executar.
    """
    rows = db.query(
        "SELECT * FROM membership WHERE in = $u AND out = $t LIMIT 1;",
        {"u": user_id, "t": tenant_id},
    )
    if not rows:
        raise MembershipRequiredError("user does not belong to tenant")
