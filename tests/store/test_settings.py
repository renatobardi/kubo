"""Contrato da store de `settings` (ADR-0028 + ADR-0030 §1, ticket KUBO-44).

Integração (SurrealDB real): singleton `settings:global`, resolução do destino padrão
(arquivado rejeita, pausado aceita) e choices para a UI de Configurações.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import client, destinations, migrations, settings
from kubo.store.scoped import ScopedStore

pytestmark = pytest.mark.integration

_SETTINGS_DB = "test_settings"


def _destination_record_id(key: str) -> RecordID:
    """Cria um RecordID de destination para usar nos asserts sem gravar no banco."""
    return RecordID("destination", key)


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_SETTINGS_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_SETTINGS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_SETTINGS_DB};")


@pytest.fixture
def session(db: Any, tenant_id: RecordID, user_id: RecordID) -> ScopedStore:
    """ScopedStore para o tenant/user do fixture."""
    return ScopedStore(db, tenant_id=tenant_id, user_id=user_id)


def test_get_settings_returns_none_when_absent(db: Any) -> None:
    """Sem seed, o singleton não existe e a store devolve None (boot falha alto fora daqui)."""
    assert settings.get_settings(db) is None


def test_put_settings_creates_singleton(db: Any) -> None:
    """`put_settings` cria `settings:global` com os três campos."""
    settings.put_settings(
        db,
        digest_cron="0 10 * * *",
        distribution_paused=True,
        default_destination=None,
    )

    got = settings.get_settings(db)
    assert got is not None
    assert str(got.id) == "settings:global"
    assert got.digest_cron == "0 10 * * *"
    assert got.distribution_paused is True
    assert got.default_destination is None


def test_put_settings_updates_singleton(db: Any) -> None:
    """`put_settings` é idempotente: regrava a mesma linha, não duplica."""
    dest = _destination_record_id("x")
    settings.put_settings(
        db, digest_cron="0 9 * * *", distribution_paused=False, default_destination=None
    )
    settings.put_settings(
        db, digest_cron="0 11 * * *", distribution_paused=True, default_destination=dest
    )

    got = settings.get_settings(db)
    assert got is not None
    assert got.digest_cron == "0 11 * * *"
    assert got.distribution_paused is True
    assert str(got.default_destination) == str(dest)

    rows = db.query("SELECT count() FROM settings GROUP ALL;")
    assert int(rows[0]["count"]) == 1


def test_resolve_default_destination_returns_destination(session: ScopedStore) -> None:
    """O destino padrão resolvível devolve o registro completo."""
    rid = destinations.create_destination(
        session, name="Renato", kind="pessoa", channel="telegram", address="123"
    )
    s = settings.Settings(
        id=RecordID("settings", "global"),
        digest_cron="0 9 * * *",
        distribution_paused=False,
        default_destination=rid,
    )

    resolved = settings.resolve_default_destination(session, s)

    assert resolved.id == rid
    assert resolved.name == "Renato"


def test_resolve_default_destination_raises_when_not_set(session: ScopedStore) -> None:
    """Sem `default_destination`, a resolução levanta ConfigError claro."""
    s = settings.Settings(
        id=RecordID("settings", "global"),
        digest_cron="0 9 * * *",
        distribution_paused=False,
        default_destination=None,
    )

    with pytest.raises(ConfigError, match="destino padrão"):
        settings.resolve_default_destination(session, s)


def test_resolve_default_destination_raises_when_dangling(session: ScopedStore) -> None:
    """Ponteiro aponta para destination inexistente -> ConfigError."""
    s = settings.Settings(
        id=RecordID("settings", "global"),
        digest_cron="0 9 * * *",
        distribution_paused=False,
        default_destination=_destination_record_id("ghost"),
    )

    with pytest.raises(ConfigError, match="destino padrão"):
        settings.resolve_default_destination(session, s)


def test_resolve_default_destination_raises_when_archived(session: ScopedStore) -> None:
    """Destino padrão arquivado rejeita; só arquivado rejeita."""
    rid = destinations.create_destination(
        session, name="Velho", kind="pessoa", channel="telegram", address="999"
    )
    destinations.archive_destination(session, id=rid)
    s = settings.Settings(
        id=RecordID("settings", "global"),
        digest_cron="0 9 * * *",
        distribution_paused=False,
        default_destination=rid,
    )

    with pytest.raises(ConfigError, match="arquivado"):
        settings.resolve_default_destination(session, s)


def test_resolve_default_destination_allows_paused_destination(
    session: ScopedStore,
) -> None:
    """Destino pausado resolve normalmente."""
    rid = destinations.create_destination(
        session, name="Pausado", kind="pessoa", channel="telegram", address="111"
    )
    destinations.set_destination_enabled(session, id=rid, enabled=False)
    s = settings.Settings(
        id=RecordID("settings", "global"),
        digest_cron="0 9 * * *",
        distribution_paused=False,
        default_destination=rid,
    )

    resolved = settings.resolve_default_destination(session, s)

    assert resolved.id == rid
    assert resolved.enabled is False


def test_default_destination_choices_excludes_archived(session: ScopedStore) -> None:
    """O dropdown da UI mostra ativos e pausados, mas nunca arquivados."""
    active = destinations.create_destination(
        session, name="Ativo", kind="pessoa", channel="telegram", address="1"
    )
    paused = destinations.create_destination(
        session, name="Pausado", kind="pessoa", channel="telegram", address="2"
    )
    destinations.set_destination_enabled(session, id=paused, enabled=False)
    archived = destinations.create_destination(
        session, name="Arquivado", kind="sistema", channel="telegram", address="3"
    )
    destinations.archive_destination(session, id=archived)

    choices = settings.default_destination_choices(session)

    assert {str(c.id) for c in choices} == {str(active), str(paused)}
    assert all(c.archived_at is None for c in choices)
