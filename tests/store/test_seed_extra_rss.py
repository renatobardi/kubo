"""Seed ad hoc de feeds RSS adicionais (segunda leva de fontes de IA).

Valida que a carga de feeds descobertos via pesquisa é idempotente e não
sobrescreve título/tags que o dono já tenha editado pela UI.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, knowledge, migrations
from kubo.store.seed_extra_rss import FEEDS, main, seed_extra_rss_sources

pytestmark = pytest.mark.integration

_EXTRA_RSS_DB = "test_seed_extra_rss"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_EXTRA_RSS_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_EXTRA_RSS_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_EXTRA_RSS_DB};")


def _count_source(db: Any) -> int:
    rows = db.query("SELECT count() FROM source GROUP ALL;")
    return int(rows[0]["count"]) if rows else 0


def test_seed_creates_all_extra_feeds(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Ambiente limpo: o seed cria todos os feeds adicionais como Cadastros rss ativos."""
    processed = seed_extra_rss_sources(db)

    assert processed == len(FEEDS)
    assert _count_source(db) == len(FEEDS)

    active = knowledge.active_sources(db, tenant_id=tenant_id, user_id=user_id, kind="rss")
    assert len(active) == len(FEEDS)

    by_canonical = {s.canonical: s for s in active}
    moonshot = by_canonical["https://medium.com/feed/@kimi_moonshot"]
    assert moonshot.title == "Moonshot AI / Kimi"

    moonshot_detail = knowledge.get_source(db, moonshot.id)
    assert moonshot_detail is not None
    assert moonshot_detail.enabled is True


def test_seed_is_idempotent(db: Any) -> None:
    """Rodar duas vezes não duplica: a segunda chamada processa tudo, mas upsert é no-op."""
    assert seed_extra_rss_sources(db) == len(FEEDS)
    assert seed_extra_rss_sources(db) == len(FEEDS)

    assert _count_source(db) == len(FEEDS)


def test_seed_preserves_owner_edits(db: Any) -> None:
    """O seed não sobrescreve título, tags ou pausa definidos pelo dono."""
    rid = knowledge.create_source(
        db,
        kind="rss",
        canonical="https://medium.com/feed/@kimi_moonshot",
        title="Meu título",
    )
    knowledge.edit_source(
        db,
        id=rid,
        title="Meu título",
        tags=["custom"],
        canonical="https://medium.com/feed/@kimi_moonshot",
    )
    knowledge.set_source_enabled(db, id=rid, enabled=False)

    seed_extra_rss_sources(db)

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title == "Meu título"
    assert got.tags == ["custom"]
    assert got.enabled is False


def test_main_connects_and_seeds(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() usa client.connect_rw e devolve a quantidade de feeds processados."""

    class _FakeConnect:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> Any:
            return self._inner

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr(
        "kubo.store.seed_extra_rss.client.connect", lambda cfg=None: _FakeConnect(db)
    )

    assert main() == len(FEEDS)
    assert _count_source(db) == len(FEEDS)
