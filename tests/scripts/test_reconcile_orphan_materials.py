"""Testes da camada pura do reconcile_orphan_materials (KUBO-206).

Testa find_orphans e remove_orphans sem rede — usa tmp_path como volume e
um mock de db. A casca (main, com SurrealDB) é exercida na sessão de ops.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.reconcile_orphan_materials import find_orphans, remove_orphans


class _FakeDB:
    """Mock de db.query que devolve file_paths de material."""

    def __init__(self, file_paths: list[str]) -> None:
        self._rows = [{"file_path": p} for p in file_paths]

    def query(self, _surql: str, _params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return self._rows


def test_find_orphans_identifies_files_without_db_record(tmp_path: Path) -> None:
    """Arquivos no volume sem registro no banco são órfãos."""
    (tmp_path / "tenant1" / "user1").mkdir(parents=True)
    orphan_file = tmp_path / "tenant1" / "user1" / "deadbeef.epub"
    orphan_file.write_bytes(b"orphan")
    tracked_file = tmp_path / "tenant1" / "user1" / "cafef00d.pdf"
    tracked_file.write_bytes(b"tracked")

    db = _FakeDB([str(tracked_file)])
    orphans = find_orphans(tmp_path, db)

    assert str(orphan_file) in orphans
    assert str(tracked_file) not in orphans


def test_find_orphans_empty_volume(tmp_path: Path) -> None:
    """Volume vazio + banco vazio = nenhum órfão."""
    db = _FakeDB([])
    assert find_orphans(tmp_path, db) == set()


def test_remove_orphans_deletes_only_orphans(tmp_path: Path) -> None:
    """remove_orphans apaga órfãos e preserva arquivos com registro."""
    orphan = tmp_path / "orphan.epub"
    orphan.write_bytes(b"orphan")
    tracked = tmp_path / "tracked.pdf"
    tracked.write_bytes(b"tracked")

    removed = remove_orphans({str(orphan)})

    assert removed == 1
    assert not orphan.exists()
    assert tracked.exists()


def test_remove_orphans_idempotent(tmp_path: Path) -> None:
    """Segunda rodada não encontra órfãos (idempotente)."""
    orphan = tmp_path / "orphan.epub"
    orphan.write_bytes(b"orphan")

    first = remove_orphans({str(orphan)})
    second = remove_orphans({str(orphan)})

    assert first == 1
    assert second == 0  # já foi removido, missing_ok=True


def test_remove_orphans_never_deletes_tracked_file(tmp_path: Path) -> None:
    """remove_orphans só apaga o que está na lista de órfãos — nunca um arquivo tracked."""
    tracked = tmp_path / "tracked.pdf"
    tracked.write_bytes(b"tracked")

    removed = remove_orphans(set())  # lista vazia = nada a remover

    assert removed == 0
    assert tracked.exists()
