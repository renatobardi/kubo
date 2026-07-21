"""Destination registry store layer (ADR-0027, ticket KUBO-43).

Every write goes through `normalize_address(channel, address)` before uniqueness
validation. Address is routing PII: stored plain in the database, but never in
logs/repr/traceback (the dataclass uses `repr=False` and the store never logs it).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Literal

from surrealdb import RecordID

from kubo.errors import (
    DestinationHasHistoryError,
    DuplicateDestinationError,
    StaleDestinationError,
    StoreError,
)
from kubo.store.transaction import run_transaction

Channel = Literal["telegram", "email"]


@dataclass(frozen=True)
class Destination:
    """A distribution destination. `address` is PII — `repr=False` prevents leaks
    in object logs/tracebacks."""

    id: RecordID
    name: str
    kind: str
    channel: Channel
    address: str = field(repr=False)
    enabled: bool
    archived_at: str | None
    dispatches: int = 0


def normalize_address(channel: str, address: str) -> str:
    """Normalize the address by channel before any write.

    Email → trim + lowercase. Telegram → digits, with optional leading minus
    (group chat_id is negative); any non-digit character is removed.
    """
    raw = address.strip()
    if channel == "email":
        return raw.lower()
    if channel == "telegram":
        sign = "-" if raw.startswith("-") else ""
        digits = "".join(c for c in raw if c.isdigit())
        return f"{sign}{digits}"
    return raw


def record_id_from_destination(destination: str) -> RecordID:
    """Convert a `destination:<key>` string into a `RecordID` (KUBO-48 cutover)."""
    table, sep, key = destination.partition(":")
    if not sep or table != "destination" or not key:
        raise StoreError(f"invalid destination reference: {destination!r}")
    return RecordID("destination", key)


def _fresh_destination_id() -> RecordID:
    """New surrogate id for a destination record."""
    return RecordID("destination", secrets.token_hex(16))


_UNPAUSE_MODES = ("backlog", "recente")


def valid_unpause_mode(raw: str) -> str:
    """Validate and normalize the unpause mode (backlog/recente); raise on invalid."""
    v = raw.strip().lower()
    if v not in _UNPAUSE_MODES:
        raise StoreError("invalid unpause mode")
    return v


def normalize_unpause_mode(raw: str | None) -> str:
    """Normalize the unpause mode; invalid values fall back to the default backlog."""
    if not raw:
        return "backlog"
    v = raw.strip().lower()
    return v if v in _UNPAUSE_MODES else "backlog"


def _find_destination_id(db: Any, *, channel: str, address: str) -> RecordID | None:
    """Resolve a destination id by its natural key (channel, address), or None."""
    rows = db.query(
        "SELECT id FROM destination WHERE channel = $channel AND address = $address;",
        {"channel": channel, "address": address},
    )
    return rows[0]["id"] if rows else None


def _parse_channel(raw: Any) -> Channel:
    """Validate a channel value coming from the database."""
    if raw in ("telegram", "email"):
        return raw
    raise StoreError(f"invalid destination channel in database: {raw!r}")


def _destination_from_row(row: dict[str, Any]) -> Destination:
    """Build a `Destination` from a database row."""
    archived = row.get("archived_at")
    return Destination(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        channel=_parse_channel(row["channel"]),
        address=row["address"],
        enabled=bool(row["enabled"]),
        archived_at=str(archived) if archived is not None else None,
        dispatches=int(row.get("dispatches") or 0),
    )


def get_destination(db: Any, id: RecordID) -> Destination | None:
    """Read one destination by id, or None if it does not exist."""
    rows = db.query("SELECT * FROM $r;", {"r": id})
    if not rows:
        return None
    return _destination_from_row(rows[0])


def create_destination(db: Any, *, name: str, kind: str, channel: str, address: str) -> RecordID:
    """Register a new destination, normalizing the address. If an archived destination
    with the same (channel, address) exists, reactivate it (update name/kind); if it is
    active/paused, raise `DuplicateDestinationError`.
    """
    if channel not in ("telegram", "email"):
        raise StoreError(f"invalid channel: {channel!r}")
    normalized = normalize_address(channel, address)
    existing = _find_destination_id(db, channel=channel, address=normalized)
    if existing is not None:
        current = get_destination(db, existing)
        if current is None:
            raise StaleDestinationError(f"destination vanished during creation: {existing}")
        if current.archived_at is not None:
            updated = db.query(
                "UPDATE $r SET name = $name, kind = $kind, enabled = true, "
                "archived_at = NONE WHERE archived_at IS NOT NONE;",
                {"r": existing, "name": name, "kind": kind},
            )
            if not updated:
                raise StaleDestinationError(f"destination vanished during reactivation: {existing}")
            return existing
        raise DuplicateDestinationError(f"destination already registered: channel={channel}")
    rid = _fresh_destination_id()
    db.query(
        "CREATE $r SET name = $name, kind = $kind, channel = $channel, "
        "address = $address, enabled = true, archived_at = NONE;",
        {"r": rid, "name": name, "kind": kind, "channel": channel, "address": normalized},
    )
    return rid


def edit_destination(db: Any, *, id: RecordID, name: str, address: str) -> None:
    """Edit a destination's name and address while preserving its id.

    Rejects if the destination is archived or if the new (channel, address) collides
    with another destination.
    """
    current = get_destination(db, id)
    if current is None or current.archived_at is not None:
        raise StaleDestinationError(f"destination not editable (missing or archived): {id}")
    normalized = normalize_address(current.channel, address)
    if normalized != current.address:
        other = _find_destination_id(db, channel=current.channel, address=normalized)
        if other is not None and str(other) != str(id):
            raise DuplicateDestinationError(
                f"destination already registered: channel={current.channel}"
            )
    updated = db.query(
        "UPDATE $r SET name = $name, address = $address WHERE archived_at IS NONE;",
        {"r": id, "name": name, "address": normalized},
    )
    if not updated:
        raise StaleDestinationError(f"destination archived during edit: {id}")


def reset_watermark_statement(
    *, prefix: str, destination: Destination
) -> tuple[str, dict[str, Any]]:
    """Return a zero-item `CREATE dispatch` statement (watermark=time::now()) and its params.

    `prefix` scopes the bind keys ($d, $dest, $ch) so the caller can build a multi-statement
    transaction without key collisions.
    """
    d = f"{prefix}d"
    dest = f"{prefix}dest"
    ch = f"{prefix}ch"
    rid = RecordID("dispatch", secrets.token_hex(16))
    return (
        f"CREATE ${d} SET destination = ${dest}, channel = ${ch}, status = 'ok', "
        f"artifact = 'digest', watermark = time::now(), item_count = 0, items = [], error = NONE",
        {
            d: rid,
            dest: destination.id,
            ch: destination.channel,
        },
    )


def _run_reactivate_transaction(
    db: Any,
    *,
    id: RecordID,
    update_statement: str,
    update_params: dict[str, Any],
    mode: str,
    destination: Destination | None,
) -> None:
    """Run UPDATE + watermark reset (when mode='recente') in an atomic transaction.

    Raises `StaleDestinationError` if the UPDATE touches no rows.
    """
    statements: list[str] = [
        f"LET $updated = ({update_statement} RETURN AFTER)",
        "IF count($updated) == 0 { THROW 'StaleDestinationError' }",
    ]
    params: dict[str, Any] = dict(update_params)
    if mode == "recente":
        if destination is None:
            raise StoreError("destination is required for mode='recente'")
        stmt, p = reset_watermark_statement(prefix="", destination=destination)
        statements.append(stmt)
        params |= p
    try:
        run_transaction(db, statements, params)
    except StoreError as exc:
        if "StaleDestinationError" in str(exc):
            raise StaleDestinationError(
                f"destination not editable (missing or archived): {id}"
            ) from exc
        raise


def set_destination_enabled(
    db: Any,
    *,
    id: RecordID,
    enabled: bool,
    mode: str | None = None,
    destination: Destination | None = None,
) -> None:
    """Pause (`enabled=false`) or resume (`enabled=true`) a non-archived destination.

    mode='recente' writes a zero-item dispatch that advances the watermark —
    atomically with the UPDATE.
    """
    mode = normalize_unpause_mode(mode)
    update = "UPDATE $r SET enabled = $enabled WHERE archived_at IS NONE"
    _run_reactivate_transaction(
        db,
        id=id,
        update_statement=update,
        update_params={"r": id, "enabled": enabled},
        mode=mode,
        destination=destination if enabled else None,
    )


def archive_destination(db: Any, *, id: RecordID) -> None:
    """Archive a destination: `enabled=false` + `archived_at` atomically."""
    updated = db.query(
        "UPDATE $r SET enabled = false, archived_at = time::now() WHERE archived_at IS NONE;",
        {"r": id},
    )
    if not updated:
        raise StaleDestinationError(
            f"destination not archivable (missing or already archived): {id}"
        )


def restore_destination(
    db: Any,
    *,
    id: RecordID,
    mode: str | None = None,
    destination: Destination | None = None,
) -> None:
    """Restore an archived destination to active state.

    mode='recente' writes a zero-item dispatch that advances the watermark —
    atomically with the UPDATE.
    """
    mode = normalize_unpause_mode(mode)
    update = "UPDATE $r SET enabled = true, archived_at = NONE WHERE archived_at IS NOT NONE"
    _run_reactivate_transaction(
        db,
        id=id,
        update_statement=update,
        update_params={"r": id},
        mode=mode,
        destination=destination,
    )


def destination_dispatch_count(db: Any, id: RecordID) -> int:
    """Count how many dispatches point to this destination."""
    rows = db.query(
        "SELECT count() FROM dispatch WHERE destination = $addr GROUP ALL;",
        {"addr": id},
    )
    return int(rows[0]["count"]) if rows else 0


def delete_destination(db: Any, *, id: RecordID) -> None:
    """Atomic hard delete of a destination with zero dispatches.

    Also clears `settings.default_destination` inside the same transaction
    (ADR-0030 §2). Raises `DestinationHasHistoryError` when dispatches exist,
    and `StaleDestinationError` when the record is gone.
    """
    try:
        run_transaction(
            db,
            [
                "UPDATE settings SET default_destination = NONE WHERE default_destination = $r",
                "LET $exists = (SELECT id FROM $r)",
                "IF count($exists) == 0 { THROW 'StaleDestinationError' }",
                "LET $deleted = (DELETE $r WHERE (SELECT count() FROM dispatch "
                "WHERE destination = $r GROUP ALL)[0].count = 0 RETURN BEFORE)",
                "IF count($deleted) == 0 { THROW 'DestinationHasHistoryError' }",
            ],
            {"r": id},
        )
    except StoreError as exc:
        if "StaleDestinationError" in str(exc):
            raise StaleDestinationError(f"destination not found: {id}") from exc
        if "DestinationHasHistoryError" in str(exc):
            raise DestinationHasHistoryError(
                f"destination has dispatches (archive first): {id}"
            ) from exc
        raise


def active_destinations(db: Any, *, channel: str | None = None) -> list[Destination]:
    """List ACTIVE destinations (`enabled=true`, `archived_at IS NONE`). If `channel`
    is provided, filter by channel; otherwise return all (ADR-0029 §9)."""
    query = "SELECT * FROM destination WHERE enabled = true AND archived_at IS NONE"
    params: dict[str, Any] = {}
    if channel is not None:
        query += " AND channel = $channel"
        params["channel"] = channel
    query += ";"
    rows = db.query(query, params)
    return [_destination_from_row(r) for r in rows]


def reset_destination_watermark(db: Any, *, destination: Destination) -> None:
    """Advance the destination watermark to database `time::now()` without delivering content.

    Writes a zero-item `ok` dispatch (artifact='digest'), auditable in the Dispatches
    UI — the "recente" option on reactivation/unpause (ADR-0029 §6).
    """
    stmt, params = reset_watermark_statement(prefix="", destination=destination)
    db.query(stmt + ";", params)


_LIST_DESTINATIONS_SQL = (
    "SELECT *, "
    "(SELECT count() FROM dispatch WHERE destination = $parent.id GROUP ALL)[0].count "
    "AS dispatches FROM destination;"
)


def list_destinations(db: Any) -> list[Destination]:
    """List all destinations with their dispatch count (for the UI)."""
    rows = db.query(_LIST_DESTINATIONS_SQL)
    return [_destination_from_row(r) for r in rows]
