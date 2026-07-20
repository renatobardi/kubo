"""Camada de acesso ao singleton de config operacional `settings` (ADR-0028 + ADR-0030 §1).

O registro `settings:global` é a única linha; leitura por id fixo, nunca `LIMIT 1` ambíguo.
O campo `default_destination` é um ponteiro `option<record<destination>>`; a resolução verifica
se o destino existe e não está arquivado (pausado resolve normalmente).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import destinations as destination_store

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


def resolve_default_destination(db: Any, settings: Settings) -> destination_store.Destination:
    """Dado o singleton, devolve o destino padrão resolvido.

    Levanta `ConfigError` claro se não houver destino padrão, se o registro não
    existir mais (dangling) ou se estiver arquivado. Um destino pausado resolve
    normalmente (ADR-0030 §2).
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
    """Destinos elegíveis como padrão: ativos ou pausados, nunca arquivados.

    A UI usa `name`/`channel` para o dropdown; o endereço (PII) não é exibido,
    mas vem no objeto como em toda a store de destinos (repr=False).
    """
    return [d for d in destination_store.list_destinations(db) if d.archived_at is None]
