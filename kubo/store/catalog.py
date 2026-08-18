"""Catálogo por-tenant no banco + changelog de auditoria (ADR-0042, KUBO-119, ADR-0053).

A store trabalha com dicts de conteúdo; os Pydantic models (`Persona`, `Integration`,
`FlowTemplate`) vivem em `kubo.runtime.*` e são convertidos pelo chamador. Isso evita
circularidade com os loaders de runtime, que puxam deste módulo.

Toda operação pública recebe uma `ScopedStore` (KUBO-212, ADR-0053): a sessão carrega
`(tenant_id, user_id)`, checa membership na criação, e injeta `$tenant_id`/`$user_id`
nos params de toda query. O caller não passa tenant_id/user_id — a sessão os fornece.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from surrealdb import RecordID

from kubo.errors import ConfigError, StoreError
from kubo.runtime.catalog_defaults import (
    DEFAULT_FLOW_TEMPLATES,
    DEFAULT_INTEGRATIONS,
    DEFAULT_PERSONAS,
)
from kubo.store import transaction
from kubo.store.scoped import ScopedStore

# SQL templates reutilizáveis — evitam duplicação de literais identificada pelo Sonar.
_SELECT_BY_ID = "SELECT * FROM $r;"
_DELETE_BY_ID = "DELETE $r;"

# Personas que bypassam `resolve_persona` (lidas direto de DEFAULT_PERSONAS pela
# rota) não são semeadas no catálogo do tenant — apareceriam editáveis mas edições
# não teriam efeito (ADR-0046 §IV).
_NON_SEEDED_PERSONAS: frozenset[str] = frozenset({"work_context_reviewer"})


def _list_catalog_items(
    session: ScopedStore,
    *,
    table: str,
    from_row: Any,
) -> list[dict[str, Any]]:
    """Lista todos os itens de uma tabela de catálogo do tenant, ordenados por nome."""
    rows = session.query(
        f"SELECT * FROM {table} WHERE tenant_id = $tenant_id ORDER BY name ASC;"  # noqa: S608
    )
    return [from_row(r) for r in rows]


def _get_catalog_item(
    session: ScopedStore,
    *,
    table: str,
    name: str,
    from_row: Any,
) -> dict[str, Any] | None:
    """Lê um item de catálogo pelo nome, ou None se não existe no tenant."""
    rows = session.query(_SELECT_BY_ID, {"r": _catalog_id(session.tenant_id, table, name)})
    return from_row(rows[0]) if rows else None


def _upsert_catalog_item(
    session: ScopedStore,
    *,
    table: str,
    name: str,
    kind: str,
    set_clause: str,
    fields: dict[str, Any],
    from_row: Any,
) -> dict[str, Any]:
    """Cria ou atualiza um item de catálogo e grava changelog numa transação só.

    O `before` e o `after` do changelog são capturados via LET dentro da mesma
    transação — `before` antes do UPSERT, `after` depois — garantindo que o audit
    trail reflita exatamente o estado observado e gravado na transação, sem janela
    de corrida com escritores concorrentes (ADR-0042, CodeRabbit review)."""
    rid = _catalog_id(session.tenant_id, table, name)

    params: dict[str, Any] = {"r": rid, "n": name}
    params.update(fields)
    changelog_stmt, changelog_params = _changelog_statement(
        tenant_id=session.tenant_id,
        kind=kind,
        item_name=name,
        before_expr="$before_row",
        after_expr="$after_row",
        changed_by=session.user_id,
    )
    transaction.run_transaction(
        session,
        [
            "LET $before_row = (SELECT * FROM $r)[0]",
            set_clause,
            "LET $after_row = (SELECT * FROM $r)[0]",
            changelog_stmt,
        ],
        {**params, **changelog_params},
    )
    persisted = session.query(_SELECT_BY_ID, {"r": rid})
    return from_row(persisted[0])


def _delete_catalog_item(
    session: ScopedStore,
    *,
    table: str,
    name: str,
    kind: str,
    from_row: Any,
) -> None:
    """Remove um item de catálogo e grava changelog com after=None numa transação só.

    O `before` é capturado via LET dentro da transação, antes do DELETE — o snapshot
    de auditoria compartilha a mesma transação da mutação, sem janela de corrida
    (CodeRabbit review). A checagem de existência também roda na transação: se o
    item não existe, o `before_row` é NONE e a transação aborta com erro explícito."""
    rid = _catalog_id(session.tenant_id, table, name)

    params: dict[str, Any] = {"r": rid}
    changelog_stmt, changelog_params = _changelog_statement(
        tenant_id=session.tenant_id,
        kind=kind,
        item_name=name,
        before_expr="$before_row",
        after_expr="None",
        changed_by=session.user_id,
    )
    try:
        transaction.run_transaction(
            session,
            [
                "LET $before_row = (SELECT * FROM $r)[0]",
                "IF $before_row IS NONE { THROW 'ConfigError: not found' } ELSE { DELETE $r }",
                changelog_stmt,
            ],
            {**params, **changelog_params},
        )
    except StoreError as exc:
        if "ConfigError: not found" in str(exc):
            raise ConfigError(f"{kind} '{name}' not found in tenant catalog") from exc
        raise


def _catalog_id(tenant_id: RecordID, table: str, name: str) -> RecordID:
    """RecordID determinístico por (tenant, nome) — idempotente e sem SELECT-then-CREATE."""
    key = f"{tenant_id}:{name}"
    return RecordID(table, hashlib.sha256(key.encode("utf-8")).hexdigest())


def _fresh_changelog_id() -> RecordID:
    """Novo id para cada entrada de changelog (não é determinístico: um item
    pode mudar muitas vezes)."""
    return RecordID("catalog_changelog", secrets.token_hex(16))


def _persona_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Dict limpo que o Pydantic `Persona` valida, sem metadados da store."""
    return {
        "name": row["name"],
        "executor": row["executor"],
        "model": row.get("model"),
        "prompt": row.get("prompt", ""),
        "permissions": list(row.get("permissions") or []),
        "max_tokens": row.get("max_tokens"),
        "temperature": row.get("temperature"),
        "reasoning_effort": row.get("reasoning_effort"),
        "timeout": row.get("timeout"),
        "max_turns": row.get("max_turns"),
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


def _changelog_statement(
    *,
    tenant_id: RecordID,
    kind: str,
    item_name: str,
    before_expr: str,
    after_expr: str,
    changed_by: RecordID,
) -> tuple[str, dict[str, Any]]:
    """Monta o statement e os binds do CREATE catalog_changelog.

    `before_expr`/`after_expr` são expressões SurrealDB referenciadas inline —
    `$before_row`/`$after_row` (variáveis LET capturadas na mesma transação) para
    upserts, ou `None` para o `after` de deletes. Os bind keys usam prefixo `c`
    para não colidirem com os do item.
    """
    rid = _fresh_changelog_id()
    stmt = (
        "CREATE $cr SET tenant_id = $ct, kind = $ck, item_name = $cn, "
        f"before = {before_expr}, after = {after_expr}, "
        "changed_by = $cu, changed_at = time::now()"
    )
    params: dict[str, Any] = {
        "cr": rid,
        "ct": tenant_id,
        "ck": kind,
        "cn": item_name,
        "cu": changed_by,
    }
    return stmt, params


def seed_catalog(session: ScopedStore) -> None:
    """Semeia o catálogo default de um tenant novo (personas, integrações, templates).

    Idempotente por id determinístico: re-rodar NÃO cria duplicatas e NÃO sobrescreve
    campos já preenchidos (usa coalesce `field ?? $value`), preservando edições do
    usuário. `created_at` é READONLY e `updated_at` default só entra na criação.
    """
    statements: list[str] = []
    params: dict[str, Any] = {}

    def _add(prefix: str, table: str, item: dict[str, Any], keys: tuple[str, ...]) -> None:
        rid = _catalog_id(session.tenant_id, table, item["name"])
        p_rid = f"{prefix}r"
        p_name = f"{prefix}n"
        sets = ["tenant_id = $tenant_id", f"name = ${p_name}"]
        params[p_rid] = rid
        params[p_name] = item["name"]
        for key in keys:
            p_key = f"{prefix}{key}"
            value = item.get(key)
            sets.append(f"{key} = {key} ?? ${p_key}")
            params[p_key] = value
        sets.append("updated_at = updated_at ?? time::now()")
        statements.append(f"UPSERT ${p_rid} SET {', '.join(sets)}")

    # Personas that bypass `resolve_persona` (read directly from DEFAULT_PERSONAS)
    # are not seeded into the tenant catalog — they'd appear editable but edits
    # would have no effect (ADR-0046 §IV).
    _seeded_personas = [p for p in DEFAULT_PERSONAS if p["name"] not in _NON_SEEDED_PERSONAS]
    for i, persona in enumerate(_seeded_personas):
        _add(
            f"p{i}_",
            "catalog_persona",
            persona,
            (
                "executor",
                "model",
                "prompt",
                "permissions",
                "max_tokens",
                "temperature",
                "reasoning_effort",
                "timeout",
                "max_turns",
            ),
        )
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

    transaction.run_transaction(session, statements, params)


# ── Personas ──────────────────────────────────────────────────────────────────


def list_personas(session: ScopedStore) -> list[dict[str, Any]]:
    """Lista todas as personas do tenant, ordenadas por nome."""
    return _list_catalog_items(
        session,
        table="catalog_persona",
        from_row=_persona_from_row,
    )


def get_persona(session: ScopedStore, *, name: str) -> dict[str, Any] | None:
    """Lê uma persona pelo nome, ou None se não existe no tenant."""
    return _get_catalog_item(
        session,
        table="catalog_persona",
        name=name,
        from_row=_persona_from_row,
    )


def upsert_persona(session: ScopedStore, *, persona: dict[str, Any]) -> dict[str, Any]:
    """Cria ou atualiza uma persona no catálogo do tenant e grava changelog."""
    return _upsert_catalog_item(
        session,
        table="catalog_persona",
        name=persona["name"],
        kind="persona",
        set_clause=(
            "UPSERT $r SET tenant_id = $tenant_id, name = $n, executor = $e, model = $m, "
            "prompt = $p, permissions = $perms, max_tokens = $mt, temperature = $temp, "
            "reasoning_effort = $re, timeout = $to, max_turns = $mturns, updated_at = time::now()"
        ),
        fields={
            "e": persona["executor"],
            "m": persona.get("model"),
            "p": persona.get("prompt", ""),
            "perms": list(persona.get("permissions", [])),
            "mt": persona.get("max_tokens"),
            "temp": persona.get("temperature"),
            "re": persona.get("reasoning_effort"),
            "to": persona.get("timeout"),
            "mturns": persona.get("max_turns"),
        },
        from_row=_persona_from_row,
    )


def delete_persona(session: ScopedStore, *, name: str) -> None:
    """Remove uma persona do catálogo do tenant e grava changelog com after=None."""
    _delete_catalog_item(
        session,
        table="catalog_persona",
        name=name,
        kind="persona",
        from_row=_persona_from_row,
    )


# ── Integrations ──────────────────────────────────────────────────────────────


def list_integrations(session: ScopedStore) -> list[dict[str, Any]]:
    """Lista todas as integrações do tenant, ordenadas por nome."""
    return _list_catalog_items(
        session,
        table="catalog_integration",
        from_row=_integration_from_row,
    )


def get_integration(session: ScopedStore, *, name: str) -> dict[str, Any] | None:
    """Lê uma integração pelo nome, ou None se não existe no tenant."""
    return _get_catalog_item(
        session,
        table="catalog_integration",
        name=name,
        from_row=_integration_from_row,
    )


def upsert_integration(session: ScopedStore, *, integration: dict[str, Any]) -> dict[str, Any]:
    """Cria ou atualiza uma integração no catálogo do tenant e grava changelog."""
    return _upsert_catalog_item(
        session,
        table="catalog_integration",
        name=integration["name"],
        kind="integration",
        set_clause=(
            "UPSERT $r SET tenant_id = $tenant_id, name = $n, kind = $k, auth = $a, "
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


def delete_integration(session: ScopedStore, *, name: str) -> None:
    """Remove uma integração do catálogo do tenant e grava changelog."""
    _delete_catalog_item(
        session,
        table="catalog_integration",
        name=name,
        kind="integration",
        from_row=_integration_from_row,
    )


# ── Flow Templates ────────────────────────────────────────────────────────────


def list_flow_templates(session: ScopedStore) -> list[dict[str, Any]]:
    """Lista todos os flow_templates do tenant, ordenados por nome."""
    return _list_catalog_items(
        session,
        table="catalog_flow_template",
        from_row=_flow_template_from_row,
    )


def get_flow_template(session: ScopedStore, *, name: str) -> dict[str, Any] | None:
    """Lê um flow_template pelo nome, ou None se não existe no tenant."""
    return _get_catalog_item(
        session,
        table="catalog_flow_template",
        name=name,
        from_row=_flow_template_from_row,
    )


def upsert_flow_template(session: ScopedStore, *, template: dict[str, Any]) -> dict[str, Any]:
    """Cria ou atualiza um flow_template no catálogo do tenant e grava changelog."""
    return _upsert_catalog_item(
        session,
        table="catalog_flow_template",
        name=template["name"],
        kind="flow_template",
        set_clause=(
            "UPSERT $r SET tenant_id = $tenant_id, name = $n, version = $v, board = $b, "
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


def delete_flow_template(session: ScopedStore, *, name: str) -> None:
    """Remove um flow_template do catálogo do tenant e grava changelog."""
    _delete_catalog_item(
        session,
        table="catalog_flow_template",
        name=name,
        kind="flow_template",
        from_row=_flow_template_from_row,
    )


# ── Changelog (read-only) ─────────────────────────────────────────────────────


def list_changelog(session: ScopedStore) -> list[dict[str, Any]]:
    """Histórico de mudanças do catálogo do tenant, do mais recente para o mais antigo."""
    rows = session.query(
        "SELECT * FROM catalog_changelog WHERE tenant_id = $tenant_id ORDER BY changed_at DESC;",
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
