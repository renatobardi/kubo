"""Persistência do módulo Estudos (ADR-0043): Material e seus capítulos.

Material é dado PESSOAL — escopo `user` DENTRO do tenant (não só tenant): toda
leitura filtra por `tenant_id` E `user_id`, então um material de outro membro do
mesmo tenant é invisível (get devolve None). Contrato KUBO-123: argumentos
keyword-only e `assert_membership` no topo de toda função pública.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import tenancy, transaction
from kubo.study.parsing import ParsedChapter

_log = structlog.get_logger(__name__)

# Teto de página das leituras (mesmo valor de `knowledge._MAX_PAGE`): a UI pede 50/100.
_MAX_PAGE = 100

_MATERIAL_SCOPE = "tenant_id = $tenant AND user_id = $user"
_CHAPTER_SCOPE = f"material = $material AND {_MATERIAL_SCOPE}"


@dataclass(frozen=True)
class Material:
    """Material de estudo ingerido pelo dono (epub/PDF), com o arquivo original no volume."""

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    title: str
    fmt: str
    original_filename: str
    file_path: str
    size_bytes: int
    chapter_count: int
    created_at: datetime


@dataclass(frozen=True)
class MaterialChapter:
    """Um capítulo do material, na ordem de leitura (`seq` 1-based)."""

    id: RecordID
    material: RecordID
    seq: int
    title: str
    part: str | None
    content: str


def _fresh(table: str) -> RecordID:
    """Novo id surrogate para uma tabela (mesmo idioma de `knowledge._fresh`)."""
    return RecordID(table, secrets.token_hex(16))


def _as_datetime(value: Any) -> datetime:
    """Normaliza um datetime vindo do SurrealDB (datetime ou string ISO)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise StoreError(f"invalid datetime value: {type(value).__name__}")


def _material_from_row(row: dict[str, Any]) -> Material:
    """Constrói um `Material` a partir de uma linha do banco."""
    return Material(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        title=row["title"],
        fmt=row["fmt"],
        original_filename=row["original_filename"],
        file_path=row["file_path"],
        size_bytes=int(row["size_bytes"]),
        chapter_count=int(row["chapter_count"]),
        created_at=_as_datetime(row["created_at"]),
    )


def _chapter_from_row(row: dict[str, Any]) -> MaterialChapter:
    """Constrói um `MaterialChapter` a partir de uma linha do banco."""
    return MaterialChapter(
        id=row["id"],
        material=row["material"],
        seq=int(row["seq"]),
        title=row["title"],
        part=row.get("part"),
        content=row["content"],
    )


def create_material(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    title: str,
    fmt: str,
    original_filename: str,
    file_path: str,
    size_bytes: int,
    chapters: Sequence[ParsedChapter],
) -> Material:
    """Persiste material + capítulos atomicamente e devolve o material criado.

    Tudo numa transação: um `seq` repetido viola o índice UNIQUE e reverte também o
    material — a alternativa deixaria um material fantasma sem capítulos, apontando
    para um arquivo que o dono acha que foi ingerido.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    material_id = _fresh("material")
    statements = [
        "CREATE $material SET tenant_id = $tenant, user_id = $user, title = $title, "
        "fmt = $fmt, original_filename = $filename, file_path = $path, "
        "size_bytes = $size, chapter_count = $count"
    ]
    params: dict[str, Any] = {
        "material": material_id,
        "tenant": tenant_id,
        "user": user_id,
        "title": title,
        "fmt": fmt,
        "filename": original_filename,
        "path": file_path,
        "size": size_bytes,
        "count": len(chapters),
    }
    for i, chapter in enumerate(chapters):
        statements.append(
            f"CREATE $c{i} SET material = $material, tenant_id = $tenant, user_id = $user, "
            f"seq = $cs{i}, title = $ct{i}, part = $cp{i}, content = $cc{i}"
        )
        params |= {
            f"c{i}": _fresh("material_chapter"),
            f"cs{i}": chapter.seq,
            f"ct{i}": chapter.title,
            f"cp{i}": chapter.part,
            f"cc{i}": chapter.content,
        }
    transaction.run_transaction(db, statements, params)

    material = get_material(db, tenant_id=tenant_id, user_id=user_id, material_id=material_id)
    if material is None:
        raise StoreError("material vanished during creation")
    _log.info(
        "store.material.created",
        material=str(material_id),
        fmt=fmt,
        chapters=len(chapters),
    )
    return material


def list_materials(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[Material]:
    """Lista os materiais do usuário no tenant, mais recentes primeiro."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM material WHERE {_MATERIAL_SCOPE} ORDER BY created_at DESC;",  # noqa: S608
        {"tenant": tenant_id, "user": user_id},
    )
    return [_material_from_row(row) for row in rows]


def get_material(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> Material | None:
    """Lê um material do usuário; None se não existe ou é de outro usuário."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM material WHERE id = $material AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    return _material_from_row(rows[0]) if rows else None


def list_chapters(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    material_id: RecordID,
    limit: int,
    start: int,
) -> list[MaterialChapter]:
    """Página de capítulos de um material do usuário, ordenados por `seq`."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    page = max(1, min(int(limit), _MAX_PAGE))
    offset = max(0, int(start))
    # LIMIT/START não aceitam bind param nesta versão do SurrealDB (o parser exige
    # literal — mesmo quirk documentado em `knowledge.list_distilled`). Os dois são
    # ints já clampados aqui, não conteúdo coletado: interpolar é seguro.
    query = (
        f"SELECT * FROM material_chapter WHERE {_CHAPTER_SCOPE} "  # noqa: S608
        f"ORDER BY seq LIMIT {page} START {offset};"
    )
    rows = db.query(query, {"material": material_id, "tenant": tenant_id, "user": user_id})
    return [_chapter_from_row(row) for row in rows]


def count_chapters(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> int:
    """Total de capítulos de um material do usuário."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT count() FROM material_chapter WHERE {_CHAPTER_SCOPE} GROUP ALL;",  # noqa: S608
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    return int(rows[0]["count"]) if rows else 0
