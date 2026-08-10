#!/usr/bin/env python3
"""Reconciliação de arquivos órfãos no volume de materiais (KUBO-206).

O unlink best-effort na rota de delete pode falhar (permissão, I/O, crash) e
deixar arquivo no volume sem registro correspondente na tabela `material`.
Este script compara o volume vs banco e remove órfãos.

Idempotente: rodar 2x não quebra nada — a segunda rodada não encontra órfãos.
Nunca apaga arquivo com registro correspondente no banco.

Uso (M = `python -m scripts.reconcile_orphan_materials`):
    uv run $M              # DRY-RUN, lista órfãos
    uv run $M --apply      # remove órfãos do volume
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import structlog

from kubo.store import client

_log = structlog.get_logger().bind(worker="reconcile_orphan_materials")


def _materials_dir() -> Path:
    """Lê KUBO_MATERIALS_DIR do env; falha cedo se não configurado."""
    raw = os.environ.get("KUBO_MATERIALS_DIR", "").strip()
    if not raw:
        raise RuntimeError("KUBO_MATERIALS_DIR não configurado")
    path = Path(raw)
    if not path.is_dir():
        raise RuntimeError(f"KUBO_MATERIALS_DIR não é um diretório: {path}")
    return path


def _list_volume_files(volume: Path) -> set[str]:
    """Lista todos os arquivos (caminhos absolutos) no volume recursivamente."""
    return {str(p) for p in volume.rglob("*") if p.is_file()}


def _list_db_file_paths(db: Any) -> set[str]:
    """Lista todos os file_path da tabela material (global, sem filtro de tenant)."""
    rows = db.query("SELECT file_path FROM material;")
    return {r["file_path"] for r in rows if r.get("file_path")}


def find_orphans(volume: Path, db: Any) -> set[str]:
    """Devolve o conjunto de arquivos no volume sem registro no banco."""
    on_disk = _list_volume_files(volume)
    in_db = _list_db_file_paths(db)
    return on_disk - in_db


def remove_orphans(orphans: set[str]) -> int:
    """Remove os arquivos órfãos do volume (best-effort). Devolve o count removido."""
    removed = 0
    for path_str in sorted(orphans):
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
            _log.info("reconcile.orphan_removed", path=path_str)
        except OSError as exc:
            _log.warning("reconcile.orphan_unlink_failed", path=path_str, error=str(exc))
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcilia órfãos do volume de materiais.")
    parser.add_argument("--apply", action="store_true", help="Remove órfãos (default: DRY-RUN).")
    args = parser.parse_args(argv)

    volume = _materials_dir()
    with client.connect() as db:
        orphans = find_orphans(volume, db)

    if not orphans:
        _log.info("reconcile.no_orphans")
        print("Nenhum órfão encontrado.")
        return 0

    _log.info("reconcile.orphans_found", count=len(orphans))
    print(f"Órfãos encontrados: {len(orphans)}")
    for path_str in sorted(orphans):
        print(f"  {path_str}")

    if not args.apply:
        print("\nDRY-RUN. Use --apply para remover.")
        return 0

    removed = remove_orphans(orphans)
    print(f"\nRemovidos: {removed}/{len(orphans)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
