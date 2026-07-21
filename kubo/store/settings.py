"""Store layer for the operational settings singleton `settings` (ADR-0028 + ADR-0030 §1).

The `settings:global` record is a single row; read by fixed id, never ambiguous `LIMIT 1`.
The `default_destination` field is an `option<record<destination>>` pointer; resolution checks
that the destination exists and is not archived (paused resolves normally).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import destinations as destination_store
from kubo.store.destinations import reset_watermark_statement
from kubo.store.transaction import run_transaction

_SETTINGS_ID = RecordID("settings", "global")


@dataclass(frozen=True)
class Settings:
    """Singleton de config operacional. `default_destination` é `RecordID` ou `None`."""

    id: RecordID
    digest_cron: str
    distribution_paused: bool
    default_destination: RecordID | None


def _settings_from_row(row: dict[str, Any]) -> Settings:
    """Monta `Settings` a partir de uma linha do banco."""
    default = row.get("default_destination")
    return Settings(
        id=row["id"],
        digest_cron=row["digest_cron"],
        distribution_paused=bool(row["distribution_paused"]),
        default_destination=default,
    )


def get_settings(db: Any) -> Settings | None:
    """Lê o singleton `settings:global`, ou `None` se ainda não existe."""
    rows = db.query("SELECT * FROM $r;", {"r": _SETTINGS_ID})
    return _settings_from_row(rows[0]) if rows else None


def put_settings(
    db: Any,
    *,
    digest_cron: str,
    distribution_paused: bool,
    default_destination: RecordID | None,
) -> None:
    """Cria ou atualiza `settings:global` com os três campos."""
    db.query(
        "UPSERT $r SET digest_cron = $cron, distribution_paused = $paused, "
        "default_destination = $dest;",
        {
            "r": _SETTINGS_ID,
            "cron": digest_cron,
            "paused": distribution_paused,
            "dest": default_destination,
        },
    )


def put_settings_and_reset(
    db: Any,
    *,
    digest_cron: str,
    distribution_paused: bool,
    default_destination: RecordID | None,
    unpause_recent: bool,
    destinations: list[destination_store.Destination],
) -> None:
    """Update `settings:global` and, if `unpause_recent`, reset watermarks in a
    single atomic transaction."""
    statements: list[str] = [
        "UPSERT $r SET digest_cron = $cron, distribution_paused = $paused, "
        "default_destination = $dest",
    ]
    params: dict[str, Any] = {
        "r": _SETTINGS_ID,
        "cron": digest_cron,
        "paused": distribution_paused,
        "dest": default_destination,
    }
    if unpause_recent:
        for i, destination in enumerate(destinations):
            stmt, p = reset_watermark_statement(prefix=f"d{i}_", destination=destination)
            statements.append(stmt)
            params |= p
    run_transaction(db, statements, params)


def resolve_default_destination(db: Any, settings: Settings) -> destination_store.Destination:
    """Resolve the default destination from the settings singleton.

    Raises a clear `ConfigError` when there is no default, when the record is
    dangling, or when it is archived. A paused destination resolves normally
    (ADR-0030 §2).
    """
    if settings.default_destination is None:
        raise ConfigError("destino padrão não definido — configure em Configurações")
    dest = destination_store.get_destination(db, settings.default_destination)
    if dest is None:
        raise ConfigError("destino padrão não existe mais — escolha outro em Configurações")
    if dest.archived_at is not None:
        raise ConfigError(
            "destino padrão está arquivado — reative ou escolha outro em Configurações"
        )
    return dest


def default_destination_choices(db: Any) -> list[destination_store.Destination]:
    """Destinations eligible as default: active or paused, never archived.

    The UI uses `name`/`channel` for the dropdown; the address (PII) is not shown,
    but it is part of the object as in all destination store usage (repr=False).
    """
    return [d for d in destination_store.list_destinations(db) if d.archived_at is None]
