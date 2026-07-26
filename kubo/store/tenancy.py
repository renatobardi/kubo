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
from typing import Any, Literal

from surrealdb import RecordID
from surrealdb.errors import InternalError

from kubo.errors import (
    ConfigError,
    DuplicateMembershipError,
    DuplicateOwnerError,
    DuplicateUserError,
    InvalidRoleError,
    KuboError,
    MembershipRequiredError,
    StoreError,
)
from kubo.store import transaction

Role = Literal["owner", "member"]
_VALID_ROLES: set[str] = {"owner", "member"}

_MEMBERSHIP_BY_USER_TENANT = "SELECT * FROM membership WHERE in = $u AND out = $t LIMIT 1;"


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


def _map_internal_error(exc: InternalError, message: str) -> KuboError:
    """Mapeia erros do driver para exceções de domínio quando possível."""
    text = str(exc)
    if "user_firebase_uid" in text:
        return DuplicateUserError("firebase_uid already exists")
    if "membership_user_tenant" in text:
        return DuplicateMembershipError("user already belongs to tenant")
    if "field `role`" in text:
        return InvalidRoleError("role must be 'owner' or 'member'")
    return StoreError(message)


def create_user(db: Any, *, firebase_uid: str, email: str | None = None) -> User:
    """Cria um `user` novo com firebase_uid único."""
    normalized_uid = firebase_uid.strip()
    if get_user_by_firebase_uid(db, normalized_uid) is not None:
        raise DuplicateUserError("firebase_uid already exists")

    user_id = _fresh_id("user")
    try:
        db.query(
            "CREATE $u SET firebase_uid = $uid, email = $email, created_at = time::now();",
            {
                "u": user_id,
                "uid": normalized_uid,
                "email": email.strip() if email else None,
            },
        )
    except InternalError as exc:
        raise _map_internal_error(exc, "user creation failed") from exc

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

    # Semeia o catálogo default do tenant (ADR-0042, KUBO-119).
    # Import local para evitar circularidade com kubo.store.catalog.
    from kubo.store import catalog

    catalog.seed_catalog(db, tenant_id=tenant_id, created_by=owner_user_id)
    return tenant


def get_tenant(db: Any, tenant_id: RecordID) -> Tenant | None:
    """Lê um tenant pelo id."""
    rows = db.query("SELECT * FROM $t;", {"t": tenant_id})
    return _tenant_from_row(rows[0]) if rows else None


def list_tenants(db: Any, tenant_ids: list[RecordID]) -> list[Tenant]:
    """Fetch multiple tenants by id in a single query."""
    if not tenant_ids:
        return []
    rows = db.query(
        "SELECT * FROM tenant WHERE id IN $ids;",
        {"ids": tenant_ids},
    )
    return [_tenant_from_row(r) for r in rows]


def _find_existing_membership(db: Any, user_id: RecordID, tenant_id: RecordID) -> Any:
    """Busca a membership pelo par (user, tenant), ou None."""
    rows = db.query(
        _MEMBERSHIP_BY_USER_TENANT,
        {"u": user_id, "t": tenant_id},
    )
    return rows[0] if rows else None


def _find_existing_owner(db: Any, tenant_id: RecordID) -> Any:
    """Busca a membership de owner de um tenant, ou None."""
    rows = db.query(
        "SELECT * FROM membership WHERE out = $t AND role = 'owner' LIMIT 1;",
        {"t": tenant_id},
    )
    return rows[0] if rows else None


def _create_owner_membership(db: Any, user_id: RecordID, tenant_id: RecordID) -> None:
    """Cria uma membership de owner, rejeitando atomicamente se já houver um.

    A checagem e a escrita rodam numa única transação para reduzir a janela de
    corrida entre dois criadores simultâneos. Ainda assim, ADR-0039 §I reforça que
    a garantia de exatamente 1 owner por tenant é feita em código, não por índice
    parcial do SurrealDB (suporte não confirmado na v3.1.5 pinada pelo ADR-0005).
    """
    try:
        transaction.run_transaction(
            db,
            [
                "LET $existing = (SELECT * FROM membership WHERE out = $t AND role = 'owner' LIMIT 1);",  # noqa: E501
                "IF array::len($existing) > 0 { THROW 'DuplicateOwnerError' } "
                "ELSE { RELATE $u->membership->$t SET role = 'owner', created_at = time::now() }",
            ],
            {"u": user_id, "t": tenant_id},
        )
    except StoreError as exc:
        if "DuplicateOwnerError" in str(exc):
            raise DuplicateOwnerError("tenant already has an owner") from exc
        raise


def create_membership(db: Any, *, user_id: RecordID, tenant_id: RecordID, role: Role) -> Membership:
    """Cria uma membership `user -> tenant` com papel `owner` ou `member`.

    Rejeita `role='owner'` se o tenant já tiver um owner (`DuplicateOwnerError`).
    Rejeita se o user já pertencer ao tenant (`DuplicateMembershipError`).
    """
    if role not in _VALID_ROLES:
        raise InvalidRoleError("role must be 'owner' or 'member'")

    if _find_existing_membership(db, user_id, tenant_id) is not None:
        raise DuplicateMembershipError("user already belongs to tenant")

    if role == "owner":
        if _find_existing_owner(db, tenant_id) is not None:
            raise DuplicateOwnerError("tenant already has an owner")
        _create_owner_membership(db, user_id, tenant_id)
    else:
        try:
            db.query(
                "RELATE $u->membership->$t SET role = $role, created_at = time::now();",
                {"u": user_id, "t": tenant_id, "role": role},
            )
        except InternalError as exc:
            raise _map_internal_error(exc, "membership creation failed") from exc

    rows = db.query(
        _MEMBERSHIP_BY_USER_TENANT,
        {"u": user_id, "t": tenant_id},
    )
    if not rows:
        raise StoreError("membership vanished during creation")
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
        _MEMBERSHIP_BY_USER_TENANT,
        {"u": user_id, "t": tenant_id},
    )
    if not rows:
        raise MembershipRequiredError("user does not belong to tenant")


def is_superadmin(uid: str, superadmin_uids: set[str]) -> bool:
    """True se `uid` está na allowlist de superadmin (ADR-0041 §VI)."""
    return uid in superadmin_uids


def assert_membership_if_given(
    db: Any,
    *,
    user_id: RecordID | None,
    tenant_id: RecordID | None,
) -> None:
    """Garante membership quando os dois ids são fornecidos; silencia se ambos forem None.

    Levanta `ConfigError` se apenas um dos dois for passado (uso interno inconsistente).
    Usada pelas store functions durante a transição para tenancy obrigatório (KUBO-117).
    """
    if tenant_id is None and user_id is None:
        return
    if tenant_id is None or user_id is None:
        raise ConfigError("tenant_id e user_id devem ser fornecidos juntos")
    assert_membership(db, user_id=user_id, tenant_id=tenant_id)


def assert_membership_or_superadmin(
    db: Any,
    *,
    user_id: RecordID,
    tenant_id: RecordID,
    superadmin: bool = False,
) -> None:
    """Garante membership OU papel superadmin; levanta MembershipRequiredError se não.

    `superadmin` é a única exceção deliberada à regra de membership (ADR-0041 §VI),
    e deve ser checada explicitamente no ponto de chamada — nunca bypass implícito.
    """
    if superadmin:
        return
    assert_membership(db, user_id=user_id, tenant_id=tenant_id)


def is_member(db: Any, *, user_id: RecordID, tenant_id: RecordID) -> bool:
    """True se `user_id` tem membership em `tenant_id`."""
    return _find_existing_membership(db, user_id, tenant_id) is not None


def parse_tenant_id(raw: str) -> RecordID | None:
    """`tenant:<key>` ou `<key>` → RecordID da tabela `tenant`, ou None se inválido."""
    key = raw.strip()
    if not key:
        return None
    if ":" in key:
        table, _, key = key.partition(":")
        if table != "tenant" or not key:
            return None
    return RecordID("tenant", key)


def get_first_tenant(db: Any) -> RecordID:
    """Devolve o id do primeiro tenant do banco, ou levanta ConfigError se não houver."""
    rows = db.query("SELECT id FROM tenant LIMIT 1;")
    if not rows:
        raise ConfigError("nenhum tenant encontrado")
    return rows[0]["id"]


def get_tenant_owner(db: Any, tenant_id: RecordID) -> RecordID:
    """Devolve o user owner de um tenant, ou levanta ConfigError se não houver."""
    rows = db.query(
        "SELECT in AS user FROM membership WHERE out = $t AND role = 'owner' LIMIT 1;",
        {"t": tenant_id},
    )
    if not rows:
        raise ConfigError(f"tenant {tenant_id} não tem owner")
    return rows[0]["user"]


def get_or_create_user_and_tenant(
    db: Any, *, firebase_uid: str, email: str | None = None
) -> tuple[User, Tenant]:
    """Garante que uma identidade Firebase tenha um user e um tenant owner.

    Na primeira chamada cria `user` + `tenant` + `membership(role=owner)`.
    Nas chamadas seguintes retorna o user existente e o primeiro tenant do qual
    ele é membro.
    """
    user = get_user_by_firebase_uid(db, firebase_uid)
    if user is None:
        user = create_user(db, firebase_uid=firebase_uid, email=email)
        tenant_name = (email or firebase_uid).strip()
        tenant = create_tenant(db, name=tenant_name, owner_user_id=user.id)
        return user, tenant

    memberships = sorted(
        list_memberships_for_user(db, user.id),
        key=lambda m: (0 if m.role == "owner" else 1, m.created_at),
    )
    if memberships:
        tenant = get_tenant(db, memberships[0].tenant)
        if tenant is None:
            raise StoreError("membership references a missing tenant")
        return user, tenant

    # User órfão (não deveria acontecer fora migração incompleta) — cria um tenant.
    tenant_name = (email or firebase_uid or str(user.id)).strip()
    tenant = create_tenant(db, name=tenant_name, owner_user_id=user.id)
    return user, tenant
