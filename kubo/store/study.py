"""Persistência do módulo Estudos (ADR-0043): Material e seus capítulos.

Material é dado PESSOAL — escopo `user` DENTRO do tenant (não só tenant): toda
leitura filtra por `tenant_id` E `user_id`, então um material de outro membro do
mesmo tenant é invisível (get devolve None). Contrato KUBO-123: argumentos
keyword-only e `assert_membership` no topo de toda função pública.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from surrealdb import RecordID

from kubo.study.parsing import ParsedChapter


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
    """Persiste material + capítulos atomicamente e devolve o material criado."""
    raise NotImplementedError


def list_materials(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[Material]:
    """Lista os materiais do usuário no tenant, mais recentes primeiro."""
    raise NotImplementedError


def get_material(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> Material | None:
    """Lê um material do usuário; None se não existe ou é de outro usuário."""
    raise NotImplementedError


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
    raise NotImplementedError


def count_chapters(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> int:
    """Total de capítulos de um material do usuário."""
    raise NotImplementedError
