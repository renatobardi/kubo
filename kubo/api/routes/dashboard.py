"""Dashboard (home) — collection counts + latest runs + workspace context (ADR-0014 UI).

Synchronous route (store is blocking); one connection per request. Latest runs
break down failure by `error.kind` (input from the post-M6 mini-session: daily runs
end as error/rate_limit on the free tier).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from kubo.api.rendering import templates
from kubo.api.session import resolve_session
from kubo.store import client, knowledge
from kubo.store import tenancy as tenancy_store

router = APIRouter()

_RECENT_RUNS = 10


def _workspaces_for_session(
    request: Request, db: object
) -> tuple[list[dict[str, object]], str, str]:
    """Build workspace list, current tenant id and role for the current session."""
    uid = request.session.get("uid")
    tenant_id = request.session.get("tenant_id", "")
    role = request.session.get("role", "")
    if not uid or not tenant_id:
        return [], tenant_id, role

    user = tenancy_store.get_user_by_firebase_uid(db, uid)
    if user is None:
        return [], tenant_id, role

    memberships = tenancy_store.list_memberships_for_user(db, user.id)
    tenants = tenancy_store.list_tenants(db, [m.tenant for m in memberships])
    tenant_by_id = {str(t.id): t for t in tenants}
    workspaces: list[dict[str, object]] = []
    for m in memberships:
        t = tenant_by_id.get(str(m.tenant))
        name = t.name if t else str(m.tenant)
        workspaces.append(
            {
                "id": str(m.tenant),
                "name": name,
                "role": m.role,
                "active": str(m.tenant) == tenant_id,
            }
        )
    return workspaces, tenant_id, role


@router.get("/")
def dashboard(request: Request) -> Response:
    """Home page: collection counts, latest runs, workspace switcher and invite card.

    Filtra as contagens pelo tenant ativo da sessão (KUBO-126): superadmin lê
    qualquer tenant, owner/member só o seu. `recent_runs` ainda é global (a tabela
    `run` não tem `tenant_id` — KUBO-117 pendente)."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return Response("Acesso negado.", status_code=403, media_type="text/plain")
        is_superadmin = ctx.role == "superadmin"
        counts = knowledge.dashboard_counts(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            superadmin=is_superadmin,
        )
        runs = knowledge.recent_runs(db, limit=_RECENT_RUNS)
        workspaces, current_tenant_id, role = _workspaces_for_session(request, db)

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "counts": counts,
            "runs": runs,
            "workspaces": workspaces,
            "current_tenant_id": current_tenant_id,
            "role": role,
        },
    )
