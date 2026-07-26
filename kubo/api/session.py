"""Resolução de sessão para rotas tenant-scoped (ADR-0039, ADR-0041).

Toda rota que precisa do tenant ativo deve chamar `resolve_session` dentro de uma
conexão aberta e usar o `tenant_id`/`user_id` retornado nas store functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError
from kubo.store import tenancy as tenancy_store


@dataclass(frozen=True)
class SessionContext:
    """Tenant, usuário e papel da sessão autenticada, já validados."""

    tenant_id: RecordID
    user_id: RecordID
    role: str


def parse_record_id(value: str) -> RecordID | None:
    """Converte uma string `table:id` em RecordID, ou None se malformado.

    Helper compartilhado entre `session.resolve_session` e `routes.auth` — a
    sessão guarda ids como string (serializável no cookie), as store functions
    exigem `RecordID`.
    """
    if not value or ":" not in value:
        return None
    table, rid = value.split(":", 1)
    if not table or not rid:
        return None
    return RecordID(table, rid)


def resolve_session(request: Request, db: Any) -> SessionContext | None:
    """Lê a sessão do request, valida usuário/tenant e retorna contexto.

    O papel `superadmin` bypassa a checagem de membership (ADR-0041 §VI), mas é
    re-verificado contra a allowlist de env — o cookie não é fonte de verdade
    para o bypass. `owner`/`member` precisam pertencer ao tenant ativo.

    Retorna None para sessão incompleta, tenant malformado, usuário inexistente
    ou membership inválida — a rota deve negar o acesso (403).
    """
    uid = request.session.get("uid")
    tenant_id = request.session.get("tenant_id")
    role = request.session.get("role")
    if not uid or not tenant_id or not role:
        return None

    tenant = parse_record_id(tenant_id)
    if tenant is None:
        return None

    user = tenancy_store.get_user_by_firebase_uid(db, uid)
    if user is None:
        return None

    is_superadmin = role == "superadmin" and tenancy_store.is_superadmin(
        uid, request.app.state.superadmin_uids
    )
    try:
        tenancy_store.assert_membership_or_superadmin(
            db,
            user_id=user.id,
            tenant_id=tenant,
            superadmin=is_superadmin,
        )
    except MembershipRequiredError:
        return None
    return SessionContext(
        tenant_id=tenant,
        user_id=user.id,
        role="superadmin" if is_superadmin else role,
    )
