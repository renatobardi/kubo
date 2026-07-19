"""Contrato da store de `destination` (ADR-0027, ticket KUBO-43).

Integração (SurrealDB real): normalização por canal, unicidade, ciclo de 3 estados,
reativação de arquivado e delete atômico condicional a zero dispatches.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from kubo.errors import (
    DestinationHasHistoryError,
    DuplicateDestinationError,
    StaleDestinationError,
)
from kubo.store import client, destinations, knowledge, migrations

pytestmark = pytest.mark.integration

_DESTINATIONS_DB = "test_destinations"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_DESTINATIONS_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DESTINATIONS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DESTINATIONS_DB};")


def test_normalize_email_trims_and_lowercases() -> None:
    """E-mail: trim + lowercase — a mesma regra em todos os caminhos de escrita."""
    assert destinations.normalize_address("email", "  Foo@Bar.com  ") == "foo@bar.com"
    assert destinations.normalize_address("email", "USER@EXAMPLE.ORG") == "user@example.org"


def test_normalize_telegram_keeps_leading_hyphen_and_digits() -> None:
    """Telegram: dígitos com hífen opcional no início (grupos); tudo que não é dígito sai."""
    assert destinations.normalize_address("telegram", "+55 1199999-9999") == "5511999999999"
    assert destinations.normalize_address("telegram", "  -1001234567890  ") == "-1001234567890"
    assert destinations.normalize_address("telegram", "abc") == ""


def test_create_destination_lands_active_and_normalized(db: Any) -> None:
    """Cadastrar cria destino ativo e normaliza o endereço antes de gravar."""
    rid = destinations.create_destination(
        db, name="Renato", kind="pessoa", channel="telegram", address="  123456  "
    )

    row = destinations.get_destination(db, rid)
    assert row is not None
    assert row.name == "Renato"
    assert row.kind == "pessoa"
    assert row.channel == "telegram"
    assert row.address == "123456"
    assert row.enabled is True
    assert row.archived_at is None


def test_create_rejects_duplicate_active_or_paused(db: Any) -> None:
    """Mesmo (channel, address) normalizado já ativo/pausado → DuplicateDestinationError."""
    destinations.create_destination(db, name="A", kind="pessoa", channel="telegram", address="123")
    with pytest.raises(DuplicateDestinationError):
        destinations.create_destination(
            db, name="B", kind="sistema", channel="telegram", address="123"
        )

    # Pausado também segura o slot (arquivado é outro caso).
    rid = destinations.create_destination(
        db, name="C", kind="pessoa", channel="email", address="c@example.com"
    )
    destinations.set_destination_enabled(db, id=rid, enabled=False)
    with pytest.raises(DuplicateDestinationError):
        destinations.create_destination(
            db, name="D", kind="pessoa", channel="email", address="C@EXAMPLE.COM"
        )


def test_create_reactivates_archived_instead_of_duplicating(db: Any) -> None:
    """Re-cadastrar um endereço arquivado reativa o registro existente, não cria novo."""
    rid = destinations.create_destination(
        db, name="Velho", kind="pessoa", channel="telegram", address="999"
    )
    destinations.archive_destination(db, id=rid)

    again = destinations.create_destination(
        db, name="Novo", kind="sistema", channel="telegram", address="999"
    )

    assert str(again) == str(rid)
    row = destinations.get_destination(db, rid)
    assert row is not None
    assert row.enabled is True
    assert row.archived_at is None
    assert row.name == "Novo"
    assert row.kind == "sistema"


def test_edit_updates_name_and_address_preserving_id(db: Any) -> None:
    """Editar nome e endereço mantém o id; o endereço é normalizado."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.edit_destination(db, id=rid, name="A2", address=" 222 ")

    row = destinations.get_destination(db, rid)
    assert row is not None
    assert row.name == "A2"
    assert row.address == "222"


def test_edit_rejects_address_collision_with_another_destination(db: Any) -> None:
    """Editar para (channel, address) de OUTRO destino → DuplicateDestinationError."""
    a = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.create_destination(db, name="B", kind="pessoa", channel="telegram", address="222")
    with pytest.raises(DuplicateDestinationError):
        destinations.edit_destination(db, id=a, name="A", address="222")


def test_edit_archived_is_stale(db: Any) -> None:
    """Destino arquivado não pode ser editado."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.archive_destination(db, id=rid)
    with pytest.raises(StaleDestinationError):
        destinations.edit_destination(db, id=rid, name="A2", address="222")


def test_pause_resume_cycle(db: Any) -> None:
    """Pausar (enabled=false, archived_at=None) e retomar são reversíveis."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.set_destination_enabled(db, id=rid, enabled=False)
    assert destinations.get_destination(db, rid).enabled is False  # type: ignore[union-attr]

    destinations.set_destination_enabled(db, id=rid, enabled=True)
    assert destinations.get_destination(db, rid).enabled is True  # type: ignore[union-attr]


def test_pause_archived_is_stale(db: Any) -> None:
    """Pausar/retomar um arquivado é stale — só `restore_destination` reativa."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.archive_destination(db, id=rid)
    with pytest.raises(StaleDestinationError):
        destinations.set_destination_enabled(db, id=rid, enabled=True)


def test_archive_and_restore_cycle(db: Any) -> None:
    """Arquivar grava enabled=false + archived_at; restaurar limpa os dois."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.archive_destination(db, id=rid)
    row = destinations.get_destination(db, rid)
    assert row is not None
    assert row.enabled is False
    assert row.archived_at is not None

    destinations.restore_destination(db, id=rid)
    row = destinations.get_destination(db, rid)
    assert row is not None
    assert row.enabled is True
    assert row.archived_at is None


def test_delete_hard_removes_when_zero_dispatches(db: Any) -> None:
    """Hard delete remove o destino quando nenhum dispatch o aponta."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    destinations.delete_destination(db, id=rid)
    assert destinations.get_destination(db, rid) is None


def test_delete_refused_when_dispatch_exists(db: Any) -> None:
    """Delete atômico recusa se há dispatches; o destino permanece."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    now = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination=str(rid),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=0,
        items=[],
    )
    with pytest.raises(DestinationHasHistoryError):
        destinations.delete_destination(db, id=rid)

    assert destinations.get_destination(db, rid) is not None


def test_active_destinations_filters_by_channel_and_state(db: Any) -> None:
    """active_destinations(channel) devolve só ativos (enabled, não arquivados) daquele canal."""
    destinations.create_destination(db, name="Tg", kind="pessoa", channel="telegram", address="111")
    rid2 = destinations.create_destination(
        db, name="Tg2", kind="pessoa", channel="telegram", address="222"
    )
    destinations.archive_destination(db, id=rid2)
    rid3 = destinations.create_destination(
        db, name="Tg3", kind="pessoa", channel="telegram", address="333"
    )
    destinations.set_destination_enabled(db, id=rid3, enabled=False)
    destinations.create_destination(
        db, name="Email", kind="pessoa", channel="email", address="e@example.com"
    )

    tg = destinations.active_destinations(db, channel="telegram")
    assert len(tg) == 1
    assert tg[0].address == "111"


def test_list_destinations_counts_dispatches(db: Any) -> None:
    """list_destinations inclui a contagem de dispatches de cada destino."""
    rid = destinations.create_destination(
        db, name="A", kind="pessoa", channel="telegram", address="111"
    )
    now = datetime.now(timezone.utc)
    knowledge.insert_dispatch(
        db,
        destination=str(rid),
        channel="telegram",
        status="ok",
        watermark=now,
        item_count=0,
        items=[],
    )
    rows = destinations.list_destinations(db)
    assert len(rows) == 1
    assert rows[0].dispatches == 1
