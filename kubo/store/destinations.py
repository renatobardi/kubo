"""Camada de acesso ao Cadastro de destinos (ADR-0027, ticket KUBO-43).

Toda escrita passa por `normalize_address(channel, address)` antes da checagem de
unicidade. Endereço é PII de roteamento: vive plain no banco, mas nunca em
log/repr/traceback (o dataclass usa `repr=False` e a store não loga o valor).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from surrealdb import RecordID

from kubo.errors import (
    DestinationHasHistoryError,
    DuplicateDestinationError,
    StaleDestinationError,
    StoreError,
)
from kubo.store.transaction import run_transaction


@dataclass(frozen=True)
class Destination:
    """Um destino de distribuição. `address` é PII — `repr=False` evita vazamento
    em log/traceback do objeto."""

    id: RecordID
    name: str
    kind: str
    channel: str
    address: str = field(repr=False)
    enabled: bool
    archived_at: str | None
    dispatches: int = 0


def normalize_address(channel: str, address: str) -> str:
    """Normaliza o endereço por canal antes de qualquer escrita.

    E-mail → trim + lowercase. Telegram → dígitos, com hífen opcional no início
    (chat_id de grupo é negativo); todo caractere que não é dígito é removido.
    """
    raw = address.strip()
    if channel == "email":
        return raw.lower()
    if channel == "telegram":
        sign = "-" if raw.startswith("-") else ""
        digits = "".join(c for c in raw if c.isdigit())
        return f"{sign}{digits}"
    return raw


def _fresh_destination_id() -> RecordID:
    """Id surrogate novo para destination."""
    return RecordID("destination", secrets.token_hex(16))


def _destination_ref(id: RecordID) -> str:
    """String `destination:<key>` usada em `dispatch.destination` nesta fase."""
    return str(id)


_UNPAUSE_MODES = ("backlog", "recente")


def valid_unpause_mode(raw: str) -> str:
    """Valida e normaliza o modo de unpause (backlog/recente); levanta em inválido."""
    v = raw.strip().lower()
    if v not in _UNPAUSE_MODES:
        raise ValueError("modo de unpause inválido")
    return v


def normalize_unpause_mode(raw: str | None) -> str:
    """Normaliza o modo de unpause; valores inválidos caem no default backlog."""
    if not raw:
        return "backlog"
    v = raw.strip().lower()
    return v if v in _UNPAUSE_MODES else "backlog"


def _find_destination_id(db: Any, *, channel: str, address: str) -> RecordID | None:
    """Resolve o id de um destination pela chave natural (channel, address), ou None."""
    rows = db.query(
        "SELECT id FROM destination WHERE channel = $channel AND address = $address;",
        {"channel": channel, "address": address},
    )
    return rows[0]["id"] if rows else None


def _destination_from_row(row: dict[str, Any]) -> Destination:
    """Monta um `Destination` a partir de uma linha do banco."""
    archived = row.get("archived_at")
    return Destination(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        channel=row["channel"],
        address=row["address"],
        enabled=bool(row["enabled"]),
        archived_at=str(archived) if archived is not None else None,
        dispatches=int(row.get("dispatches") or 0),
    )


def get_destination(db: Any, id: RecordID) -> Destination | None:
    """Lê UM destino por id, ou None se não existe."""
    rows = db.query("SELECT * FROM $r;", {"r": id})
    if not rows:
        return None
    return _destination_from_row(rows[0])


def create_destination(db: Any, *, name: str, kind: str, channel: str, address: str) -> RecordID:
    """Cadastra um destino novo, normalizando o endereço. Se um destino arquivado
    com o mesmo (channel, address) existir, reativa-o (atualiza nome/kind); se estiver
    ativo/pausado, levanta `DuplicateDestinationError`.
    """
    normalized = normalize_address(channel, address)
    existing = _find_destination_id(db, channel=channel, address=normalized)
    if existing is not None:
        current = get_destination(db, existing)
        if current is None:
            raise StaleDestinationError(f"destino sumiu durante criação: {existing}")
        if current.archived_at is not None:
            updated = db.query(
                "UPDATE $r SET name = $name, kind = $kind, enabled = true, "
                "archived_at = NONE WHERE archived_at IS NOT NONE;",
                {"r": existing, "name": name, "kind": kind},
            )
            if not updated:
                raise StaleDestinationError(f"destino sumiu durante reativação: {existing}")
            return existing
        raise DuplicateDestinationError(f"destino já cadastrado: channel={channel}")
    rid = _fresh_destination_id()
    db.query(
        "CREATE $r SET name = $name, kind = $kind, channel = $channel, "
        "address = $address, enabled = true, archived_at = NONE;",
        {"r": rid, "name": name, "kind": kind, "channel": channel, "address": normalized},
    )
    return rid


def edit_destination(db: Any, *, id: RecordID, name: str, address: str) -> None:
    """Edita nome e endereço de um destino, preservando id. Rejeita se o destino
    estiver arquivado ou se o novo (channel, address) colidir com outro destino.
    """
    current = get_destination(db, id)
    if current is None or current.archived_at is not None:
        raise StaleDestinationError(f"destino não editável (inexistente ou arquivado): {id}")
    normalized = normalize_address(current.channel, address)
    if normalized != current.address:
        other = _find_destination_id(db, channel=current.channel, address=normalized)
        if other is not None and str(other) != str(id):
            raise DuplicateDestinationError(f"destino já cadastrado: channel={current.channel}")
    updated = db.query(
        "UPDATE $r SET name = $name, address = $address WHERE archived_at IS NONE;",
        {"r": id, "name": name, "address": normalized},
    )
    if not updated:
        raise StaleDestinationError(f"destino arquivado durante a edição: {id}")


def _reset_watermark_sql_and_params(
    *, prefix: str, destination: Destination
) -> tuple[str, dict[str, Any]]:
    """Devolve um statement CREATE dispatch de zero-item (watermark=time::now()) e params."""
    rid = RecordID("dispatch", secrets.token_hex(16))
    return (
        "CREATE $d SET destination = $dest, channel = $ch, status = 'ok', "
        "artifact = 'digest', watermark = time::now(), item_count = 0, items = [], error = NONE",
        {
            "d": rid,
            "dest": _destination_ref(destination.id),
            "ch": destination.channel,
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
    """Executa UPDATE + reset de watermark (quando mode='recente') numa transação atômica.

    Levanta `StaleDestinationError` se o UPDATE não tocar nenhuma linha.
    """
    statements: list[str] = [
        f"LET $updated = ({update_statement} RETURN AFTER)",
        "IF count($updated) == 0 { THROW 'StaleDestinationError' }",
    ]
    params: dict[str, Any] = dict(update_params)
    if mode == "recente":
        if destination is None:
            raise ValueError("destination é obrigatório para mode='recente'")
        stmt, p = _reset_watermark_sql_and_params(prefix="", destination=destination)
        statements.append(stmt)
        params |= p
    try:
        run_transaction(db, statements, params)
    except StoreError as exc:
        if "StaleDestinationError" in str(exc):
            raise StaleDestinationError(
                f"destino não editável (inexistente ou arquivado): {id}"
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
    """Pausa (`enabled=false`) ou retoma (`enabled=true`) um destino NÃO arquivado.

    mode='recente' grava um dispatch zero-item que avança o watermark — atômico com o UPDATE.
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
    """Arquiva um destino: `enabled=false` + `archived_at` atômico."""
    updated = db.query(
        "UPDATE $r SET enabled = false, archived_at = time::now() WHERE archived_at IS NONE;",
        {"r": id},
    )
    if not updated:
        raise StaleDestinationError(f"destino não arquivável (inexistente ou já arquivado): {id}")


def restore_destination(
    db: Any,
    *,
    id: RecordID,
    mode: str | None = None,
    destination: Destination | None = None,
) -> None:
    """Restaura um destino arquivado ao estado ativo.

    mode='recente' grava um dispatch zero-item que avança o watermark — atômico com o UPDATE.
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
    """Conta quantos dispatches apontam para este destino (string RecordID)."""
    rows = db.query(
        "SELECT count() FROM dispatch WHERE destination = $addr GROUP ALL;",
        {"addr": _destination_ref(id)},
    )
    return int(rows[0]["count"]) if rows else 0


def delete_destination(db: Any, *, id: RecordID) -> None:
    """Hard delete atômico de um destino: só remove se `dispatch.destination` não
    aponta para ele. O `DELETE ... WHERE` fecha a janela entre a checagem e o delete.
    """
    if get_destination(db, id) is None:
        raise StaleDestinationError(f"destino inexistente: {id}")
    deleted = db.query(
        "DELETE $r WHERE (SELECT count() FROM dispatch WHERE destination = $addr GROUP ALL)"
        "[0].count = 0 RETURN BEFORE;",
        {"r": id, "addr": _destination_ref(id)},
    )
    if not deleted:
        if destination_dispatch_count(db, id) > 0:
            raise DestinationHasHistoryError(f"destino tem envios (arquive): {id}")
        raise StaleDestinationError(f"destino inexistente: {id}")


def active_destinations(db: Any, *, channel: str | None = None) -> list[Destination]:
    """Lista destinos ATIVOS (`enabled=true`, `archived_at IS NONE`). Se `channel`
    for fornecido, filtra por canal; senão, retorna todos (ADR-0029 §9)."""
    query = "SELECT * FROM destination WHERE enabled = true AND archived_at IS NONE"
    params: dict[str, Any] = {}
    if channel is not None:
        query += " AND channel = $channel"
        params["channel"] = channel
    query += ";"
    rows = db.query(query, params)
    return [_destination_from_row(r) for r in rows]


def reset_destination_watermark(db: Any, *, destination: Destination) -> None:
    """Avança o watermark do destino para `time::now()` do banco sem entregar conteúdo.

    Grava um dispatch `ok` de zero-item (artifact='digest'), auditável na tela de
    Envios — a opção "recente" da reativação/unpause (ADR-0029 §6).
    """
    stmt, params = _reset_watermark_sql_and_params(prefix="", destination=destination)
    db.query(stmt + ";", params)


_LIST_DESTINATIONS_SQL = (
    "SELECT *, "
    "(SELECT count() FROM dispatch WHERE destination = "
    "string::concat('destination:', meta::id($parent.id)) GROUP ALL)[0].count "
    "AS dispatches FROM destination;"
)


def list_destinations(db: Any) -> list[Destination]:
    """Lista todos os destinos com contagem de dispatches (para a UI)."""
    rows = db.query(_LIST_DESTINATIONS_SQL)
    return [_destination_from_row(r) for r in rows]
