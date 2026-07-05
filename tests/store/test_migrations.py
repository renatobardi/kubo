"""Testes do runner de migrations (integração: exige SurrealDB via docker)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from surrealdb.errors import SurrealError

from kubo.store import client, migrations

pytestmark = pytest.mark.integration


@pytest.fixture
def db() -> Iterator[Any]:
    """Conexão isolada num database próprio do teste, limpo antes e depois."""
    cfg = replace(client.config(), database="test_migrations")
    with client.connect(cfg) as conn:
        conn.query("REMOVE TABLE IF EXISTS migration; REMOVE TABLE IF EXISTS widget;")
        yield conn
        conn.query("REMOVE TABLE IF EXISTS migration; REMOVE TABLE IF EXISTS widget;")


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql)


def test_applies_in_name_order_and_records(db: Any, tmp_path: Path) -> None:
    """Aplica em ordem de nome (não de criação) e registra cada uma."""
    _write(tmp_path, "0002_data.surql", "CREATE widget:b SET n = 2;")
    _write(
        tmp_path, "0001_schema.surql", "DEFINE TABLE widget SCHEMALESS; CREATE widget:a SET n = 1;"
    )

    applied = migrations.apply_migrations(db, tmp_path)

    assert applied == ["0001_schema.surql", "0002_data.surql"]
    recorded = {r["name"] for r in db.query("SELECT name FROM migration;")}
    assert recorded == {"0001_schema.surql", "0002_data.surql"}
    assert db.query("SELECT count() FROM widget GROUP ALL;")[0]["count"] == 2


def test_rerun_is_noop(db: Any, tmp_path: Path) -> None:
    """Rodar de novo sem migration nova não aplica nada."""
    _write(tmp_path, "0001_schema.surql", "DEFINE TABLE widget SCHEMALESS;")

    assert migrations.apply_migrations(db, tmp_path) == ["0001_schema.surql"]
    assert migrations.apply_migrations(db, tmp_path) == []


def test_applies_only_new_migrations(db: Any, tmp_path: Path) -> None:
    """Ao surgir uma migration nova, só ela é aplicada."""
    _write(tmp_path, "0001_a.surql", "DEFINE TABLE widget SCHEMALESS;")
    migrations.apply_migrations(db, tmp_path)

    _write(tmp_path, "0002_b.surql", "CREATE widget:x SET n = 1;")

    assert migrations.apply_migrations(db, tmp_path) == ["0002_b.surql"]


def test_failed_migration_leaves_no_record(db: Any, tmp_path: Path) -> None:
    """Migration que falha levanta e não deixa registro (aplicar+registrar é atômico)."""
    _write(tmp_path, "0001_ok.surql", "DEFINE TABLE widget SCHEMALESS;")
    _write(tmp_path, "0002_bad.surql", "THIS IS NOT VALID SURQL;")

    with pytest.raises(SurrealError):
        migrations.apply_migrations(db, tmp_path)

    recorded = {r["name"] for r in db.query("SELECT name FROM migration;")}
    assert recorded == {"0001_ok.surql"}  # a boa commitou; a quebrada não deixou rastro
