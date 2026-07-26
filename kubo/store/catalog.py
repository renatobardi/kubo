"""Catálogo por-tenant no banco + changelog de auditoria (ADR-0042, KUBO-119).

A store trabalha com dicts de conteúdo; os Pydantic models (`Persona`, `Integration`,
`FlowTemplate`) vivem em `kubo.runtime.*` e são convertidos pelo chamador. Isso evita
circularidade com os loaders de runtime, que puxam deste módulo.

Toda operação pública exige `user_id` e `tenant_id` e passa por `assert_membership`
(ADR-0039 §II) antes de executar — a autorização é da store, não do chamador.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.runtime.catalog_defaults import (
    DEFAULT_FLOW_TEMPLATES,
    DEFAULT_INTEGRATIONS,
    DEFAULT_PERSONAS,
)
from kubo.store import transaction

# SQL templates reutilizáveis — evitam duplicação de literais identificada pelo Sonar.
_SELECT_BY_ID = "SELECT * FROM $r;"
_DELETE_BY_ID = "DELETE $r;"


def _get_catalog_item(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    table: str,
    name: str,
    from_row: Any,
) -> dict[str, Any] | None:
    """Lê um item de catálogo pelo nome, ou None se não existe no tenant."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(_SELECT_BY_ID, {"r": _catalog_id(tenant_id, table, name)})
    return from_row(rows[0]) if rows else None


def _upsert_catalog_item(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    table: str,
    name: str,
    kind: str,
    set_clause: str,
    fields: dict[str, Any],
    from_row: Any,
) -> dict[str, Any]:
    """Cria ou atualiza um item de catálogo e grava changelog."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rid = _catalog_id(tenant_id, table, name)
    existing = db.query(_SELECT_BY_ID, {"r": rid})
    before = from_row(existing[0]) if existing else None

    params: dict[str, Any] = {"r": rid, "t": tenant_id, "n": name}
    params.update(fields)
    transaction.run_transaction(db, [set_clause], params)

    after = from_row(db.query(_SELECT_BY_ID, {"r": rid})[0])
    _log_changelog(
        db,
        tenant_id=tenant_id,
        kind=kind,
        item_name=name,
        before=before,
        after=after,
        changed_by=user_id,
    )
    return after


def _delete_catalog_item(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    table: str,
    name: str,
    kind: str,
    from_row: Any,
) -> None:
    """Remove um item de catálogo e grava changelog com after=None."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rid = _catalog_id(tenant_id, table, name)
    existing = db.query(_SELECT_BY_ID, {"r": rid})
    if not existing:
        raise ConfigError(f"{kind} '{name}' not found in tenant catalog")
    before = from_row(existing[0])
    db.query(_DELETE_BY_ID, {"r": rid})
    _log_changelog(
        db,
        tenant_id=tenant_id,
        kind=kind,
        item_name=name,
        before=before,
        after=None,
        changed_by=user_id,
    )


def _catalog_id(tenant_id: RecordID, table: str, name: str) -> RecordID:
    """RecordID determinístico por (tenant, nome) — idempotente e sem SELECT-then-CREATE."""
    key = f"{tenant_id}:{name}"
    return RecordID(table, hashlib.sha256(key.encode("utf-8")).hexdigest())


def _fresh_changelog_id() -> RecordID:
    """Novo id para cada entrada de changelog (não é determinístico: um item
    pode mudar muitas vezes)."""
    return RecordID("catalog_changelog", secrets.token_hex(16))


def _assert_membership(db: Any, *, user_id: RecordID, tenant_id: RecordID) -> None:
    """Proxy para a autorização por membership do ADR-0039 §II.

    Import local para evitar circularidade com kubo.store.tenancy.
    """
    from kubo.store import tenancy

    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)


def _persona_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Dict limpo que o Pydantic `Persona` valida, sem metadados da store."""
    return {
        "name": row["name"],
        "executor": row["executor"],
        "model": row.get("model"),
        "prompt": row.get("prompt", ""),
        "permissions": list(row.get("permissions") or []),
    }


def _integration_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Dict limpo que o Pydantic `Integration` valida."""
    return {
        "name": row["name"],
        "kind": row["kind"],
        "auth": row["auth"],
        "rate_limit": row.get("rate_limit"),
        "base_url": row.get("base_url"),
    }


def _flow_template_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Dict limpo que o Pydantic `FlowTemplate` valida."""
    return {
        "name": row["name"],
        "version": row["version"],
        "board": row["board"],
        "cast": list(row["cast"]),
        "deliverable": row["deliverable"],
        "triggers": list(row["triggers"]),
        "budget_usd": row.get("budget_usd"),
    }


def _log_changelog(
    db: Any,
    *,
    tenant_id: RecordID,
    kind: str,
    item_name: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    changed_by: RecordID,
) -> None:
    """Grava uma linha de auditoria em catalog_changelog."""
    rid = _fresh_changelog_id()
    db.query(
        "CREATE $r SET tenant_id = $t, kind = $k, item_name = $n, "
        "before = $b, after = $a, changed_by = $u, changed_at = time::now();",
        {
            "r": rid,
            "t": tenant_id,
            "k": kind,
            "n": item_name,
            "b": before,
            "a": after,
            "u": changed_by,
        },
    )


def seed_catalog(db: Any, *, tenant_id: RecordID, created_by: RecordID) -> None:
    """Semeia o catálogo default de um tenant novo (personas, integrações, templates).

    Idempotente por id determinístico: re-rodar NÃO cria duplicatas e NÃO sobrescreve
    campos já preenchidos (usa coalesce `field ?? $value`), preservando edições do
    usuário. `created_at` é READONLY e `updated_at` default só entra na criação.
    """
    _assert_membership(db, user_id=created_by, tenant_id=tenant_id)

    statements: list[str] = []
    params: dict[str, Any] = {"t": tenant_id}

    def _add(prefix: str, table: str, item: dict[str, Any], keys: tuple[str, ...]) -> None:
        rid = _catalog_id(tenant_id, table, item["name"])
        p_rid = f"{prefix}r"
        p_name = f"{prefix}n"
        sets = ["tenant_id = $t", f"name = ${p_name}"]
        params[p_rid] = rid
        params[p_name] = item["name"]
        for key in keys:
            p_key = f"{prefix}{key}"
            value = item.get(key)
            sets.append(f"{key} = {key} ?? ${p_key}")
            params[p_key] = value
        sets.append("updated_at = updated_at ?? time::now()")
        statements.append(f"UPSERT ${p_rid} SET {', '.join(sets)}")

    for i, persona in enumerate(DEFAULT_PERSONAS):
        _add(f"p{i}_", "catalog_persona", persona, ("executor", "model", "prompt", "permissions"))
    for i, integration in enumerate(DEFAULT_INTEGRATIONS):
        _add(
            f"i{i}_", "catalog_integration", integration, ("kind", "auth", "rate_limit", "base_url")
        )
    for i, template in enumerate(DEFAULT_FLOW_TEMPLATES):
        _add(
            f"ft{i}_",
            "catalog_flow_template",
            template,
            ("version", "board", "cast", "deliverable", "triggers", "budget_usd"),
        )

    transaction.run_transaction(db, statements, params)


# ── Personas ──────────────────────────────────────────────────────────────────


def list_personas(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[dict[str, Any]]:
    """Lista todas as personas do tenant, ordenadas por nome."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        "SELECT * FROM catalog_persona WHERE tenant_id = $t ORDER BY name ASC;",
        {"t": tenant_id},
    )
    return [_persona_from_row(r) for r in rows]


def get_persona(
    db: Any, *, tenant_id: RecordID, name: str, user_id: RecordID
) -> dict[str, Any] | None:
    """Lê uma persona pelo nome, ou None se não existe no tenant."""
    return _get_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_persona",
        name=name,
        from_row=_persona_from_row,
    )


def upsert_persona(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, persona: dict[str, Any]
) -> dict[str, Any]:
    """Cria ou atualiza uma persona no catálogo do tenant e grava changelog."""
    return _upsert_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_persona",
        name=persona["name"],
        kind="persona",
        set_clause=(
            "UPSERT $r SET tenant_id = $t, name = $n, executor = $e, model = $m, "
            "prompt = $p, permissions = $perms, updated_at = time::now()"
        ),
        fields={
            "e": persona["executor"],
            "m": persona.get("model"),
            "p": persona.get("prompt", ""),
            "perms": list(persona.get("permissions", [])),
        },
        from_row=_persona_from_row,
    )


def delete_persona(db: Any, *, tenant_id: RecordID, name: str, user_id: RecordID) -> None:
    """Remove uma persona do catálogo do tenant e grava changelog com after=None."""
    _delete_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_persona",
        name=name,
        kind="persona",
        from_row=_persona_from_row,
    )


# ── Integrations ──────────────────────────────────────────────────────────────


def list_integrations(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[dict[str, Any]]:
    """Lista todas as integrações do tenant, ordenadas por nome."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        "SELECT * FROM catalog_integration WHERE tenant_id = $t ORDER BY name ASC;",
        {"t": tenant_id},
    )
    return [_integration_from_row(r) for r in rows]


def get_integration(
    db: Any, *, tenant_id: RecordID, name: str, user_id: RecordID
) -> dict[str, Any] | None:
    """Lê uma integração pelo nome, ou None se não existe no tenant."""
    return _get_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_integration",
        name=name,
        from_row=_integration_from_row,
    )


def upsert_integration(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, integration: dict[str, Any]
) -> dict[str, Any]:
    """Cria ou atualiza uma integração no catálogo do tenant e grava changelog."""
    return _upsert_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_integration",
        name=integration["name"],
        kind="integration",
        set_clause=(
            "UPSERT $r SET tenant_id = $t, name = $n, kind = $k, auth = $a, "
            "rate_limit = $rl, base_url = $b, updated_at = time::now()"
        ),
        fields={
            "k": integration["kind"],
            "a": integration["auth"],
            "rl": integration.get("rate_limit"),
            "b": integration.get("base_url"),
        },
        from_row=_integration_from_row,
    )


def delete_integration(db: Any, *, tenant_id: RecordID, name: str, user_id: RecordID) -> None:
    """Remove uma integração do catálogo do tenant e grava changelog."""
    _delete_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_integration",
        name=name,
        kind="integration",
        from_row=_integration_from_row,
    )


# ── Flow Templates ────────────────────────────────────────────────────────────


def list_flow_templates(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[dict[str, Any]]:
    """Lista todos os flow_templates do tenant, ordenados por nome."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        "SELECT * FROM catalog_flow_template WHERE tenant_id = $t ORDER BY name ASC;",
        {"t": tenant_id},
    )
    return [_flow_template_from_row(r) for r in rows]


def get_flow_template(
    db: Any, *, tenant_id: RecordID, name: str, user_id: RecordID
) -> dict[str, Any] | None:
    """Lê um flow_template pelo nome, ou None se não existe no tenant."""
    return _get_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_flow_template",
        name=name,
        from_row=_flow_template_from_row,
    )


def upsert_flow_template(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, template: dict[str, Any]
) -> dict[str, Any]:
    """Cria ou atualiza um flow_template no catálogo do tenant e grava changelog."""
    return _upsert_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_flow_template",
        name=template["name"],
        kind="flow_template",
        set_clause=(
            "UPSERT $r SET tenant_id = $t, name = $n, version = $v, board = $b, "
            "cast = $c, deliverable = $d, triggers = $tr, budget_usd = $bu, "
            "updated_at = time::now()"
        ),
        fields={
            "v": template["version"],
            "b": template["board"],
            "c": list(template["cast"]),
            "d": template["deliverable"],
            "tr": list(template["triggers"]),
            "bu": template.get("budget_usd"),
        },
        from_row=_flow_template_from_row,
    )


def delete_flow_template(db: Any, *, tenant_id: RecordID, name: str, user_id: RecordID) -> None:
    """Remove um flow_template do catálogo do tenant e grava changelog."""
    _delete_catalog_item(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        table="catalog_flow_template",
        name=name,
        kind="flow_template",
        from_row=_flow_template_from_row,
    )


# ── Changelog (read-only) ─────────────────────────────────────────────────────


def list_changelog(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[dict[str, Any]]:
    """Histórico de mudanças do catálogo do tenant, do mais recente para o mais antigo."""
    _assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        "SELECT * FROM catalog_changelog WHERE tenant_id = $t ORDER BY changed_at DESC;",
        {"t": tenant_id},
    )
    return [
        {
            "kind": r["kind"],
            "item_name": r["item_name"],
            "before": r.get("before"),
            "after": r.get("after"),
            "changed_by": r["changed_by"],
            "changed_at": r["changed_at"],
        }
        for r in rows
    ]
