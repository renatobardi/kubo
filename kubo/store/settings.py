"""Store layer for the operational settings singleton `settings` (ADR-0028 + ADR-0030 §1).

The `settings:global` record is a single row; read by fixed id, never ambiguous `LIMIT 1`.
The `default_destination` field is an `option<record<destination>>` pointer; resolution checks
that the destination exists and is not archived (paused resolves normally).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import client
from kubo.store import destinations as destination_store
from kubo.store.scoped import ScopedStore
from kubo.store.transaction import run_transaction

_SETTINGS_ID = RecordID("settings", "global")

# Transação atômica: UPSERT do singleton + watermarks de dispatch via FOR.
# O guard vê literais fixos; os dados variáveis entram por bind params.
_PUT_SETTINGS_AND_RESET_SQL = (
    "UPSERT $r SET digest_cron = $cron, distribution_paused = $paused, "
    "default_destination = $dest; "
    "FOR $x IN $destinations { "
    "CREATE $x.id SET tenant_id = $tenant_id, destination = $x.destination, "
    "channel = $x.channel, status = 'ok', artifact = 'digest', "
    "watermark = time::now(), item_count = 0, items = [], error = NONE "
    "}"
)


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


def get_settings(db: client.UnscopedDb) -> Settings | None:
    """Lê o singleton `settings:global`, ou `None` se ainda não existe.

    O singleton é global (não tenant-scoped) — lido por id fixo, sem filtro de tenant.
    """
    rows = db.query("SELECT * FROM $r;", {"r": _SETTINGS_ID})
    return _settings_from_row(rows[0]) if rows else None


def put_settings(
    db: client.UnscopedDb,
    *,
    digest_cron: str,
    distribution_paused: bool,
    default_destination: RecordID | None,
) -> None:
    """Cria ou atualiza `settings:global` com os três campos.

    O singleton é global (não tenant-scoped) — escrito por id fixo, sem filtro de tenant.
    """
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
    session: ScopedStore,
    *,
    digest_cron: str,
    distribution_paused: bool,
    default_destination: RecordID | None,
    unpause_recent: bool,
    destinations: list[destination_store.Destination],
) -> None:
    """Update `settings:global` and, if `unpause_recent`, reset watermarks in a
    single atomic transaction. `$tenant_id` is injected by the session (KUBO-128, ADR-0053)."""
    watermark_entries = [
        {
            "id": RecordID("dispatch", secrets.token_hex(16)),
            "destination": destination.id,
            "channel": destination.channel,
        }
        for destination in (destinations if unpause_recent else [])
    ]
    params: dict[str, Any] = {
        "r": _SETTINGS_ID,
        "cron": digest_cron,
        "paused": distribution_paused,
        "dest": default_destination,
        "destinations": watermark_entries,
    }
    run_transaction(session, [_PUT_SETTINGS_AND_RESET_SQL], params)


def resolve_default_destination(
    session: ScopedStore, settings: Settings
) -> destination_store.Destination:
    """Resolve the default destination from the settings singleton.

    Raises a clear `ConfigError` when there is no default, when the record is
    dangling, or when it is archived. A paused destination resolves normally
    (ADR-0030 §2).
    """
    if settings.default_destination is None:
        raise ConfigError("destino padrão não definido — configure em Configurações")
    dest = destination_store.get_destination(session, settings.default_destination)
    if dest is None:
        raise ConfigError("destino padrão não existe mais — escolha outro em Configurações")
    if dest.archived_at is not None:
        raise ConfigError(
            "destino padrão está arquivado — reative ou escolha outro em Configurações"
        )
    return dest


def default_destination_choices(session: ScopedStore) -> list[destination_store.Destination]:
    """Destinations eligible as default: active or paused, never archived.

    The UI uses `name`/`channel` for the dropdown; the address (PII) is not shown,
    but it is part of the object as in all destination store usage (repr=False).
    """
    return [d for d in destination_store.list_destinations(session) if d.archived_at is None]
