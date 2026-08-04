"""Persistência do módulo Estudos (ADR-0047): Tema container de N Materiais.

Material é dado PESSOAL — escopo `user` DENTRO do tenant (não só tenant): toda
leitura filtra por `tenant_id` E `user_id`, então um material de outro membro do
mesmo tenant é invisível (get devolve None). Contrato KUBO-123: argumentos
keyword-only e `assert_membership` no topo de toda função pública.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import tenancy, transaction
from kubo.study.parsing import ParsedChapter, SectionPart, fallback_part

_log = structlog.get_logger(__name__)

# Teto de página das leituras (mesmo valor de `knowledge._MAX_PAGE`): a UI pede 50/100.
_MAX_PAGE = 100

_MATERIAL_SCOPE = "tenant_id = $tenant AND user_id = $user"
_TOPIC_NOT_FOUND_MSG = "tema não encontrado"

# Sentinel: caller omitted a field → preserve the existing value.
# `None` means "clear"; `_UNSET` means "don't touch" (same pattern as tenancy).
_UNSET: object = object()
_CHAPTER_SCOPE = f"material = $material AND {_MATERIAL_SCOPE}"


@dataclass(frozen=True)
class Material:
    """Material de estudo ingerido pelo dono (epub/PDF), com o arquivo original no volume.

    Exclusivo a um Tema (N:1, ADR-0047): `topic` aponta para o Tema que o contém.
    `summary` é gerado síncrono no upload (consumido por `mentor` e `planner`).
    """

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    topic: RecordID | None
    title: str
    fmt: str
    original_filename: str
    file_path: str
    size_bytes: int
    chapter_count: int
    summary: str | None
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


@dataclass(frozen=True)
class MaterialSection:
    """Uma seção tópica de um capítulo (KUBO-184, ADR-0048): persistida no upload.

    `chapter_seq` é populado em leitura (via join com material_chapter) — não é
    coluna persistida. `seq` é local ao capítulo (1-based).
    """

    id: RecordID
    material: RecordID
    material_chapter: RecordID
    seq: int
    title: str
    anchor_text: str
    content: str
    summary: str
    chapter_seq: int = 0


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
        topic=row.get("topic"),
        title=row["title"],
        fmt=row["fmt"],
        original_filename=row["original_filename"],
        file_path=row["file_path"],
        size_bytes=int(row["size_bytes"]),
        chapter_count=int(row["chapter_count"]),
        summary=row.get("summary"),
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


def _section_from_row(row: dict[str, Any], *, chapter_seq: int = 0) -> MaterialSection:
    """Constrói um `MaterialSection` a partir de uma linha do banco.

    `chapter_seq` é injetado pelo caller (via join com material_chapter) — não é
    coluna persistida em material_section.
    """
    return MaterialSection(
        id=row["id"],
        material=row["material"],
        material_chapter=row["material_chapter"],
        seq=int(row["seq"]),
        title=row["title"],
        anchor_text=row.get("anchor_text", ""),
        content=row["content"],
        summary=row.get("summary", ""),
        chapter_seq=chapter_seq,
    )


def create_material(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    title: str,
    fmt: str,
    original_filename: str,
    file_path: str,
    size_bytes: int,
    chapters: Sequence[ParsedChapter],
    sections: Mapping[int, Sequence[SectionPart]] | None,
    summary: str | None,
) -> Material:
    """Persiste material + capítulos + seções atomicamente e devolve o material.

    Exclusivo a um Tema (N:1, ADR-0047): `topic_id` é obrigatório. `summary` é
    gerado síncrono no upload (consumido por `mentor` e `planner`). `sections`
    mapeia `chapter.seq` → lista de `SectionPart` (particionamento do
    sectionizer, ADR-0048). `sectionize()` garante entrada para todo capítulo;
    capítulos sem entrada no dict recebem fallback via `fallback_part`.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    material_id = _fresh("material")
    statements = [
        "CREATE $material SET tenant_id = $tenant, user_id = $user, topic = $topic, "
        "title = $title, fmt = $fmt, original_filename = $filename, file_path = $path, "
        "size_bytes = $size, chapter_count = $count, summary = $summary"
    ]
    params: dict[str, Any] = {
        "material": material_id,
        "tenant": tenant_id,
        "user": user_id,
        "topic": topic_id,
        "title": title,
        "fmt": fmt,
        "filename": original_filename,
        "path": file_path,
        "size": size_bytes,
        "count": len(chapters),
        "summary": summary,
    }
    section_idx = 0
    for i, chapter in enumerate(chapters):
        chapter_id = _fresh("material_chapter")
        statements.append(
            f"CREATE $c{i} SET material = $material, tenant_id = $tenant, user_id = $user, "
            f"seq = $cs{i}, title = $ct{i}, part = $cp{i}, content = $cc{i}"
        )
        params |= {
            f"c{i}": chapter_id,
            f"cs{i}": chapter.seq,
            f"ct{i}": chapter.title,
            f"cp{i}": chapter.part,
            f"cc{i}": chapter.content,
        }
        # Sections do capítulo: dict entry ou fallback (1 section = capítulo inteiro).
        parts = (sections or {}).get(chapter.seq) or [fallback_part(chapter)]
        for j, part in enumerate(parts, start=1):
            statements.append(
                f"CREATE $s{section_idx} SET material_chapter = $c{i}, "
                f"material = $material, tenant_id = $tenant, user_id = $user, "
                f"seq = $ss{section_idx}, title = $st{section_idx}, "
                f"anchor_text = $sa{section_idx}, content = $sc{section_idx}, "
                f"summary = $sm{section_idx}"
            )
            params |= {
                f"s{section_idx}": _fresh("material_section"),
                f"ss{section_idx}": j,
                f"st{section_idx}": part.title,
                f"sa{section_idx}": part.anchor_text,
                f"sc{section_idx}": part.content,
                f"sm{section_idx}": part.summary,
            }
            section_idx += 1
    transaction.run_transaction(db, statements, params)

    material = get_material(db, tenant_id=tenant_id, user_id=user_id, material_id=material_id)
    if material is None:
        raise StoreError("material vanished during creation")
    _log.info(
        "store.material.created",
        material=str(material_id),
        fmt=fmt,
        chapters=len(chapters),
        sections=section_idx,
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


def list_all_chapters(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> list[MaterialChapter]:
    """Todos os capítulos de um material, ordenados por `seq` (sem paginação).

    Usado pelo planner (KUBO-164) que precisa da estrutura completa para agrupar.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM material_chapter WHERE {_CHAPTER_SCOPE} ORDER BY seq;",  # noqa: S608
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    return [_chapter_from_row(row) for row in rows]


def list_all_chapters_light(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> list[MaterialChapter]:
    """Como `list_all_chapters`, mas sem `content` — só estrutura (id, seq, title, part).

    Para rotas que só precisam do mapeamento seq→id ou de títulos (não do texto
    integral do capítulo). Evita transferir `content` desnecessariamente.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT id, material, seq, title, part FROM material_chapter "  # noqa: S608
        f"WHERE {_CHAPTER_SCOPE} ORDER BY seq;",
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    return [
        MaterialChapter(
            id=row["id"],
            material=row["material"],
            seq=row["seq"],
            title=row["title"],
            part=row.get("part"),
            content="",
        )
        for row in rows
    ]


# --- Sections (KUBO-184, ADR-0048) -------------------------------------------------------


def list_all_sections(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> list[MaterialSection]:
    """Todas as seções de um material (com content), ordenadas por (chapter.seq, section.seq).

    `chapter_seq` é populado via join em memória (o SurrealDB não resolve
    `material_chapter.seq` em SELECT direto). Use `list_all_sections_light`
    quando não precisar do `content` (prompt do planner, UI).
    """
    return _list_all_sections_impl(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material_id, light=False
    )


def list_all_sections_light(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> list[MaterialSection]:
    """Seções sem `content` — mais leve para prompt do planner e render de UI."""
    return _list_all_sections_impl(
        db, tenant_id=tenant_id, user_id=user_id, material_id=material_id, light=True
    )


def _list_all_sections_impl(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    material_id: RecordID,
    light: bool,
) -> list[MaterialSection]:
    select = (
        "SELECT id, material, material_chapter, seq, title, anchor_text, summary "
        "FROM material_section"
        if light
        else "SELECT * FROM material_section"
    )
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"{select} WHERE {_CHAPTER_SCOPE};",  # noqa: S608
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    if not rows:
        return []
    # Join: busca os seqs dos capítulos referenciados pelas sections.
    # RecordID não é hashable, então dedup por str() preservando o objeto.
    seen: set[str] = set()
    chapter_ids: list[RecordID] = []
    for row in rows:
        cid = row["material_chapter"]
        key = str(cid)
        if key not in seen:
            seen.add(key)
            chapter_ids.append(cid)
    ch_rows = db.query(
        "SELECT id, seq FROM material_chapter WHERE id IN $chapters "  # noqa: S608
        "AND tenant_id = $tenant AND user_id = $user;",
        {"chapters": chapter_ids, "tenant": tenant_id, "user": user_id},
    )
    chapter_seq_by_id = {str(r["id"]): int(r["seq"]) for r in ch_rows}
    sections = [
        _section_from_row(row, chapter_seq=chapter_seq_by_id.get(str(row["material_chapter"]), 0))
        for row in rows
    ]
    # Ordena por (chapter_seq, section.seq) — a ordem de leitura global.
    sections.sort(key=lambda s: (s.chapter_seq, s.seq))
    return sections


# --- Tema e plano de estudo (KUBO-136) -------------------------------------------------
#
# Mesmo contrato do Material acima: keyword-only, `assert_membership` no topo, filtro por
# tenant E user. Um tema/plano de outro membro do mesmo tenant é invisível, não "negado".


@dataclass(frozen=True)
class Topic:
    """Tema de estudo: container de N Materiais (ADR-0047), com estado explícito.

    Nasce vazio (`draft`) e o dono adiciona Materiais dentro dele. O nome é
    sugerido por `mentor` e editável inline em todos os estados não-arquivados.
    `focus` e `depth` são inferidos pelo mentor durante a conversa (KUBO-163).
    """

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    title: str
    state: str
    created_at: datetime
    focus: str | None = None
    depth: str | None = None


def _topic_from_row(row: dict[str, Any]) -> Topic:
    """Constrói um `Topic` a partir de uma linha do banco."""
    return Topic(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        title=row["title"],
        state=row["state"],
        created_at=_as_datetime(row["created_at"]),
        focus=row.get("focus"),
        depth=row.get("depth"),
    )


def create_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    title: str,
) -> Topic:
    """Cria um tema vazio em `draft` (ADR-0047): container de N Materiais.

    Sem `material_id`: o tema nasce vazio e o dono adiciona Materiais depois.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic_id = _fresh("topic")
    transaction.run_transaction(
        db,
        ["CREATE $topic SET tenant_id = $tenant, user_id = $user, title = $title, state = 'draft'"],
        {
            "topic": topic_id,
            "tenant": tenant_id,
            "user": user_id,
            "title": title,
        },
    )
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError("topic vanished during creation")
    _log.info("store.topic.created", topic=str(topic_id))
    return topic


def set_topic_name(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    title: str,
) -> None:
    """Atualiza o título do tema (editável em estados não-arquivados, ADR-0047 §3)."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    if topic.state == "archived":
        raise StoreError("tema arquivado não pode ser renomeado")
    transaction.run_transaction(
        db,
        [f"UPDATE $topic SET title = $title WHERE {_MATERIAL_SCOPE}"],  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id, "title": title},
    )


def get_topic(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, topic_id: RecordID
) -> Topic | None:
    """Lê um tema do usuário; None se não existe ou é de outro usuário."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM topic WHERE id = $topic AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    return _topic_from_row(rows[0]) if rows else None


def list_topics(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[Topic]:
    """Lista os temas ATIVOS do usuário (não-arquivados), mais recentes primeiro."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM topic WHERE {_MATERIAL_SCOPE} AND state != 'archived' "  # noqa: S608
        "ORDER BY created_at DESC;",
        {"tenant": tenant_id, "user": user_id},
    )
    return [_topic_from_row(row) for row in rows]


# --- Materiais dentro de um Tema (KUBO-162) ---------------------------------------------


def list_materials_by_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> list[Material]:
    """Lista os materiais de um Tema, mais recentes primeiro."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM material WHERE topic = $topic AND {_MATERIAL_SCOPE} "  # noqa: S608
        "ORDER BY created_at DESC;",
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    return [_material_from_row(row) for row in rows]


def count_materials_by_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> int:
    """Total de materiais de um Tema (para validação de limite)."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT count() FROM material WHERE topic = $topic AND {_MATERIAL_SCOPE} GROUP ALL;",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    return int(rows[0]["count"]) if rows else 0


def delete_material(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    material_id: RecordID,
) -> None:
    """Remove um material e seus capítulos do banco (o arquivo no volume é removido pela rota).

    StoreError se o material não existe ou é de outro usuário — não silêncio.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    material = get_material(db, tenant_id=tenant_id, user_id=user_id, material_id=material_id)
    if material is None:
        raise StoreError("material não encontrado")
    transaction.run_transaction(
        db,
        [
            f"DELETE FROM material_section WHERE {_CHAPTER_SCOPE}",  # noqa: S608
            f"DELETE FROM material_chapter WHERE {_CHAPTER_SCOPE}",  # noqa: S608
            f"DELETE FROM material WHERE id = $material AND {_MATERIAL_SCOPE}",  # noqa: S608
        ],
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    _log.info("store.material.deleted", material=str(material_id))


# --- Chat com mentor/planner (KUBO-163, ADR-0047 §6) ------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """Uma mensagem da conversa com mentor (Fase 1) ou planner (Fase 2)."""

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    topic: RecordID
    phase: str
    role: str
    content: str
    created_at: datetime


def _chat_from_row(row: dict[str, Any]) -> ChatMessage:
    """Constrói um `ChatMessage` a partir de uma linha do banco."""
    return ChatMessage(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        topic=row["topic"],
        phase=row["phase"],
        role=row["role"],
        content=row["content"],
        created_at=_as_datetime(row["created_at"]),
    )


_CHAT_SCOPE = f"topic = $topic AND phase = $phase AND {_MATERIAL_SCOPE}"


def create_chat_message(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    phase: str,
    role: str,
    content: str,
) -> ChatMessage:
    """Persiste uma mensagem da conversa e a devolve."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    msg_id = _fresh("study_chat")
    transaction.run_transaction(
        db,
        [
            "CREATE $msg SET tenant_id = $tenant, user_id = $user, topic = $topic, "
            "phase = $phase, role = $role, content = $content"
        ],
        {
            "msg": msg_id,
            "tenant": tenant_id,
            "user": user_id,
            "topic": topic_id,
            "phase": phase,
            "role": role,
            "content": content,
        },
    )
    rows = db.query(
        "SELECT * FROM study_chat WHERE id = $msg LIMIT 1;",
        {"msg": msg_id},
    )
    if not rows:
        raise StoreError("chat message vanished during creation")
    _log.info("store.chat.created", msg=str(msg_id), topic=str(topic_id), role=role)
    return _chat_from_row(rows[0])


def list_chat_messages(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    phase: str,
) -> list[ChatMessage]:
    """Lista as mensagens da conversa, em ordem cronológica (mais antigas primeiro)."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM study_chat WHERE {_CHAT_SCOPE} ORDER BY created_at ASC;",  # noqa: S608
        {"topic": topic_id, "phase": phase, "tenant": tenant_id, "user": user_id},
    )
    return [_chat_from_row(row) for row in rows]


def set_topic_fields(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    focus: str | None | object = _UNSET,
    depth: str | None | object = _UNSET,
) -> None:
    """Atualiza campos estruturados do Tema (focus, depth) inferidos pelo mentor.

    `_UNSET` (default) preserva o valor existente; `None` limpa o campo.
    Atualização parcial: só o campo presente é alterado.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    if topic.state == "archived":
        raise StoreError("tema arquivado é só leitura")
    sets: list[str] = []
    params: dict[str, Any] = {"topic": topic_id, "tenant": tenant_id, "user": user_id}
    if focus is not _UNSET:
        sets.append("focus = $focus")
        params["focus"] = focus
    if depth is not _UNSET:
        sets.append("depth = $depth")
        params["depth"] = depth
    if not sets:
        return  # nada a atualizar
    set_clause = ", ".join(sets)
    transaction.run_transaction(
        db,
        [f"UPDATE $topic SET {set_clause} WHERE {_MATERIAL_SCOPE}"],  # noqa: S608
        params,
    )


# --- Plano: transição de estado, proposta, cadência (KUBO-164) --------------------------


def set_topic_state(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    state: str,
) -> None:
    """Transiciona o estado do tema (draft → planning → scheduled → ...).

    Não valida a legalidade da transição aqui — a rota decide quais transições
    são permitidas. A store só persiste.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    transaction.run_transaction(
        db,
        [f"UPDATE $topic SET state = $state WHERE {_MATERIAL_SCOPE}"],  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id, "state": state},
    )
    _log.info("store.topic.state_changed", topic=str(topic_id), state=state)


def revert_to_draft_if_planning(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> bool:
    """Reverte planning → draft com CAS em `state = 'planning'`.

    Usado pelo auto-revert ao deletar o último Material (ADR-0047 Emenda 7).
    O CAS fecha a janela TOCTOU: se outra requisição ativou o plano
    (planning → scheduled) entre o delete e a reversão, o CAS falha e nada
    é persistido — devolve False para a rota tratar.

    Devolve True se reverteu, False se o estado mudou concorrentemente.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    result = db.query(
        f"UPDATE $topic SET state = 'draft' "  # noqa: S608
        f"WHERE {_MATERIAL_SCOPE} AND state = 'planning' RETURN id;",
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    if not result:
        return False
    _log.info("store.topic.auto_reverted", topic=str(topic_id))
    return True


@dataclass(frozen=True)
class StudyPlan:
    """Plano de estudo proposto pelo `planner` para um Tema (ADR-0047 §2)."""

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    topic: RecordID
    status: str
    weekdays: list[str]
    target_date: datetime | None
    activated_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class PlanEntry:
    """Uma lição do plano: título + seções (RecordIDs de material_section).

    KUBO-185: o átomo do plano mudou de capítulo para seção (ADR-0048).
    `sections` é uma lista de RecordIDs de `material_section`.
    """

    id: RecordID
    study_plan: RecordID
    tenant_id: RecordID
    user_id: RecordID
    seq: int
    title: str
    sections: list[RecordID]
    created_at: datetime


def _plan_from_row(row: dict[str, Any]) -> StudyPlan:
    return StudyPlan(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        topic=row["topic"],
        status=row["status"],
        weekdays=list(row.get("weekdays") or []),
        target_date=_as_datetime(row["target_date"]) if row.get("target_date") else None,
        activated_at=_as_datetime(row["activated_at"]) if row.get("activated_at") else None,
        created_at=_as_datetime(row["created_at"]),
    )


def _entry_from_row(row: dict[str, Any]) -> PlanEntry:
    return PlanEntry(
        id=row["id"],
        study_plan=row["study_plan"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        seq=row["seq"],
        title=row["title"],
        sections=list(row.get("sections") or []),
        created_at=_as_datetime(row["created_at"]),
    )


_PLAN_SCOPE = f"topic = $topic AND {_MATERIAL_SCOPE}"

_CREATE_LESSON_SQL = (
    "CREATE $lesson SET tenant_id = $tenant, user_id = $user, "
    "study_plan = $plan, plan_entry = $entry, "
    "scheduled_for = $when, "
    "concept = '', scenario = '', application = '', quiz = [], provenance = [];"
)


def save_plan_proposal(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    entries: Sequence[tuple[str, list[RecordID]]],
) -> tuple[StudyPlan, list[PlanEntry]]:
    """Persiste uma proposta de plano: 1 study_plan (proposed) + N plan_entries.

    Substitui o plano anterior se existir (1 plano por tema, índice UNIQUE).
    `entries` é uma lista de (title, chapter_record_ids) na ordem de estudo.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    # Remove plano anterior + entries (replace, não append).
    transaction.run_transaction(
        db,
        [
            f"DELETE FROM plan_entry WHERE study_plan IN "  # noqa: S608
            f"(SELECT id FROM study_plan WHERE {_PLAN_SCOPE});",
            f"DELETE FROM study_plan WHERE {_PLAN_SCOPE};",  # noqa: S608
        ],
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    plan_id = _fresh("study_plan")
    transaction.run_transaction(
        db,
        [
            "CREATE $plan SET tenant_id = $tenant, user_id = $user, topic = $topic, "
            "status = 'proposed', weekdays = []"
        ],
        {"plan": plan_id, "tenant": tenant_id, "user": user_id, "topic": topic_id},
    )
    for seq, (title, section_ids) in enumerate(entries, start=1):
        entry_id = _fresh("plan_entry")
        transaction.run_transaction(
            db,
            [
                "CREATE $entry SET study_plan = $plan, tenant_id = $tenant, "
                "user_id = $user, seq = $seq, title = $title, sections = $sections"
            ],
            {
                "entry": entry_id,
                "plan": plan_id,
                "tenant": tenant_id,
                "user": user_id,
                "seq": seq,
                "title": title,
                "sections": section_ids,
            },
        )
    # Lê tudo de volta (garante ordem e tipos).
    return get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)  # type: ignore[return-value]


def get_plan_for_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> tuple[StudyPlan | None, list[PlanEntry]]:
    """Lê o plano e as lições de um Tema; (None, []) se não há plano."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    plan_rows = db.query(
        f"SELECT * FROM study_plan WHERE {_PLAN_SCOPE} LIMIT 1;",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    if not plan_rows:
        return None, []
    plan = _plan_from_row(plan_rows[0])
    entry_rows = db.query(
        "SELECT * FROM plan_entry WHERE study_plan = $plan ORDER BY seq ASC;",  # noqa: S608
        {"plan": plan.id},
    )
    entries = [_entry_from_row(row) for row in entry_rows]
    return plan, entries


def set_plan_cadence(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    weekdays: Sequence[str],
) -> None:
    """Define a cadência (weekdays) e recalcula a data-alvo.

    A data-alvo é derivada (cadência + número de lições), nunca digitada:
    mudar a cadência recalcula o alvo pelo mesmo caminho (ADR-0043 §cadência).
    """
    from kubo.study.planning import compute_target_date

    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    # Valida dias e calcula data-alvo a partir de hoje.
    entry_rows = db.query(
        f"SELECT count() FROM plan_entry WHERE study_plan = $plan AND {_MATERIAL_SCOPE} "  # noqa: S608
        "GROUP ALL;",
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    lesson_count = int(entry_rows[0]["count"]) if entry_rows else 0
    target = compute_target_date(
        start=date.today(), weekdays=list(weekdays), lesson_count=lesson_count
    )
    transaction.run_transaction(
        db,
        [
            f"UPDATE $plan SET weekdays = $weekdays, target_date = $target "  # noqa: S608
            f"WHERE {_MATERIAL_SCOPE}"
        ],
        {
            "plan": plan_id,
            "tenant": tenant_id,
            "user": user_id,
            "weekdays": list(weekdays),
            "target": datetime(target.year, target.month, target.day),
        },
    )
    _log.info("store.plan.cadence_set", plan=str(plan_id), lessons=lesson_count)


def replace_plan_entries(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    entries: Sequence[tuple[str, list[RecordID]]],
) -> tuple[StudyPlan, list[PlanEntry]]:
    """Substitui as entries do plano atomicamente, preservando weekdays/status.

    Diferente de `save_plan_proposal` (que deleta e recria o `study_plan`), esta
    função mantém o registro do plano intacto — apenas remove as entries antigas
    e cria as novas numa única transação. `target_date` é recalculado com base no
    novo número de lições + weekdays existentes (ADR-0043 §cadência). Usada pelo
    chat incremental do planner (KUBO-165), onde a cadência definida manualmente
    não pode ser descartada a cada mensagem.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    plan, _ = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if plan is None:
        return save_plan_proposal(
            db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id, entries=entries
        )
    # Recalcula target_date com base no novo número de lições + weekdays atuais.
    # Sem cadência (weekdays=[]), não há data-alvo para calcular — deixa None.
    # O dono define cadência depois via set_plan_cadence, que recalcula target.
    stmts: list[str] = [
        f"DELETE FROM plan_entry WHERE study_plan = $plan AND {_MATERIAL_SCOPE}",  # noqa: S608
    ]
    params: dict[str, Any] = {
        "plan": plan.id,
        "tenant": tenant_id,
        "user": user_id,
    }
    if plan.weekdays:
        from kubo.study.planning import compute_target_date

        target = compute_target_date(
            start=date.today(), weekdays=list(plan.weekdays), lesson_count=len(entries)
        )
        stmts.append(f"UPDATE $plan SET target_date = $target WHERE {_MATERIAL_SCOPE}")  # noqa: S608
        params["target"] = datetime(target.year, target.month, target.day)
    else:
        stmts.append(f"UPDATE $plan SET target_date = NONE WHERE {_MATERIAL_SCOPE}")  # noqa: S608
    for i, (title, section_ids) in enumerate(entries, start=1):
        entry_id = _fresh("plan_entry")
        stmts.append(
            f"CREATE $entry_{i} SET study_plan = $plan, tenant_id = $tenant, "
            f"user_id = $user, seq = $seq_{i}, title = $title_{i}, "
            f"sections = $sections_{i}"
        )
        params[f"entry_{i}"] = entry_id
        params[f"seq_{i}"] = i
        params[f"title_{i}"] = title
        params[f"sections_{i}"] = section_ids
    transaction.run_transaction(db, stmts, params)
    _log.info("store.plan.entries_replaced", plan=str(plan.id), entries=len(entries))
    return get_plan_for_topic(  # type: ignore[return-value]
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id
    )


def swap_plan_entries(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    entry_a: RecordID,
    entry_b: RecordID,
) -> None:
    """Troca os seqs de duas entries num único transaction (atômico).

    Usa offset temporário (seq + 1000) para evitar colisão com o índice UNIQUE
    `(study_plan, seq)` — atualizar in-place entraria em conflito.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    # Lê os seqs atuais (valida ownership via scope).
    rows = db.query(
        f"SELECT id, seq FROM plan_entry WHERE (id = $a OR id = $b) "  # noqa: S608
        f"AND study_plan = $plan AND {_MATERIAL_SCOPE};",
        {"a": entry_a, "b": entry_b, "plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    if len(rows) != 2:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    seqs = {str(r["id"]): r["seq"] for r in rows}
    seq_a, seq_b = seqs[str(entry_a)], seqs[str(entry_b)]
    # Swap atômico: A→temp, B→seq_a, A→seq_b.
    transaction.run_transaction(
        db,
        [
            f"UPDATE $a SET seq = seq + 1000 WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE};",
            f"UPDATE $b SET seq = $seq_a WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE};",
            f"UPDATE $a SET seq = $seq_b WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE};",
        ],
        {
            "a": entry_a,
            "b": entry_b,
            "plan": plan_id,
            "tenant": tenant_id,
            "user": user_id,
            "seq_a": seq_a,
            "seq_b": seq_b,
        },
    )
    _log.info("store.plan.swapped", plan=str(plan_id), a=str(entry_a), b=str(entry_b))


def remove_section_from_entry(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    entry_id: RecordID,
    section_id: RecordID,
) -> bool:
    """Remove uma seção de uma lição (edição manual, KUBO-165/185).

    Rejeita a remoção da última seção (devolve False) — não esvazia lições,
    porque `PlanLesson` exige `min_length=1`. A contagem é lida antes da remoção
    e o `UPDATE` usa `array::complement` num único statement transacional.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT array::len(sections) AS n FROM plan_entry WHERE id = $entry "  # noqa: S608
        f"AND {_MATERIAL_SCOPE};",
        {"entry": entry_id, "tenant": tenant_id, "user": user_id},
    )
    if not rows:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    count = rows[0].get("n", 0)
    if count <= 1:
        # Última seção: não esvazia a lição.
        return False
    transaction.run_transaction(
        db,
        [
            f"UPDATE $entry SET sections = array::complement(sections, [$sec]) "  # noqa: S608
            f"WHERE {_MATERIAL_SCOPE};"
        ],
        {"entry": entry_id, "sec": section_id, "tenant": tenant_id, "user": user_id},
    )
    _log.info("store.plan.section_removed", entry=str(entry_id), section=str(section_id))
    return True


# --- Ativação + scheduler + imutabilidade (KUBO-166) -------------------------------------


def activate_plan(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> None:
    """Ativa o plano do tema: planning → scheduled.

    Seta `study_plan.status='active'` + `activated_at=now` e `topic.state='scheduled'`.
    Atômico numa transação — se qualquer statement falha, nada é persistido.
    Não valida a legalidade da transição aqui (a rota decide); só persiste.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    plan, _ = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if plan is None:
        raise StoreError("plano não encontrado para o tema")
    transaction.run_transaction(
        db,
        [
            f"UPDATE $plan SET status = 'active', activated_at = time::now() "  # noqa: S608
            f"WHERE {_PLAN_SCOPE};",
            f"UPDATE $topic SET state = 'scheduled' WHERE {_MATERIAL_SCOPE};",  # noqa: S608
        ],
        {
            "plan": plan.id,
            "topic": topic_id,
            "tenant": tenant_id,
            "user": user_id,
        },
    )
    _log.info("store.plan.activated", topic=str(topic_id))


def deactivate_plan(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> None:
    """Reverte ativação: scheduled → planning.

    Seta `study_plan.status='proposed'` + `activated_at=NONE` e
    `topic.state='planning'` numa transação atômica. Ambos os UPDATEs têm CAS
    (`AND status = 'active'` no plano, `AND state = 'scheduled'` no tema) — se
    o scheduler já transicionou para `running`, nenhum UPDATE aplica e a rota
    devolve 400 (`_TOPIC_FROZEN`). A rota checa `state == 'scheduled'` antes
    da chamada; o CAS protege contra a janela TOCTOU entre o check e o write.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    plan, _ = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if plan is None:
        raise StoreError("plano não encontrado para o tema")
    transaction.run_transaction(
        db,
        [
            f"UPDATE $plan SET status = 'proposed', activated_at = NONE "  # noqa: S608
            f"WHERE {_PLAN_SCOPE} AND status = 'active';",
            f"UPDATE $topic SET state = 'planning' WHERE {_MATERIAL_SCOPE} "  # noqa: S608
            f"AND state = 'scheduled';",
        ],
        {
            "plan": plan.id,
            "topic": topic_id,
            "tenant": tenant_id,
            "user": user_id,
        },
    )
    _log.info("store.plan.deactivated", topic=str(topic_id))


def list_topics_by_state(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    state: str,
) -> list[Topic]:
    """Lista temas em um estado específico (escopo user dentro do tenant).

    Usado pelo scheduler para encontrar temas em 'scheduled' e 'running'.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM topic WHERE state = $state AND {_MATERIAL_SCOPE};",  # noqa: S608
        {"state": state, "tenant": tenant_id, "user": user_id},
    )
    return [_topic_from_row(r) for r in rows]


def create_lesson(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    plan_entry_id: RecordID,
    scheduled_for: datetime,
) -> RecordID:
    """Cria um registro de lição para um plan_entry num dia específico.

    O conteúdo da lição (concept, scenario, application, quiz) é gerado pelo
    scheduler (KUBO-168 traz a geração com IA). Aqui cria o registro com
    campos vazios — o scheduler preenche. O índice UNIQUE lesson_plan_day
    impede duplicata por dia.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    lesson_id = _fresh("lesson")
    transaction.run_transaction(
        db,
        [_CREATE_LESSON_SQL],
        {
            "lesson": lesson_id,
            "tenant": tenant_id,
            "user": user_id,
            "plan": plan_id,
            "entry": plan_entry_id,
            "when": scheduled_for,
        },
    )
    _log.info(
        "store.lesson.created",
        lesson=str(lesson_id),
        plan=str(plan_id),
        scheduled_for=scheduled_for.isoformat(),
    )
    return lesson_id


def fill_lesson(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    lesson_id: RecordID,
    concept: str,
    scenario: str,
    application: str,
    recap: str | None,
    quiz: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
) -> None:
    """Preenche uma lição vazia com o conteúdo gerado pelo Tutor (KUBO-168).

    UPDATE no registro com os campos de IA. Se a lição já tem conteúdo
    (re-tentativa após sucesso), o UPDATE sobrescreve — idempotente.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    result = db.query(
        f"UPDATE $lesson SET concept = $concept, scenario = $scenario, "  # noqa: S608
        f"application = $application, recap = $recap, quiz = $quiz, "
        f"provenance = $provenance WHERE id = $lesson AND {_MATERIAL_SCOPE} RETURN id;",
        {
            "lesson": lesson_id,
            "tenant": tenant_id,
            "user": user_id,
            "concept": concept,
            "scenario": scenario,
            "application": application,
            "recap": recap,
            "quiz": quiz,
            "provenance": provenance,
        },
    )
    if not result:
        raise StoreError("lesson not found or not owned by user")
    _log.info("store.lesson.filled", lesson=str(lesson_id))


def get_sections_for_entry(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    entry: PlanEntry,
) -> list[MaterialSection]:
    """Busca as MaterialSection referenciadas por um plan_entry (KUBO-185).

    O plan_entry.sections é uma lista de RecordIDs de material_section.
    Retorna as seções na ordem dos RecordIDs (que é a ordem de estudo
    definida pelo planner). `chapter_seq` é populado via join com
    material_chapter (não é coluna persistida em material_section).
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    if not entry.sections:
        return []
    rows = db.query(
        f"SELECT * FROM material_section WHERE id IN $sections "  # noqa: S608
        f"AND {_MATERIAL_SCOPE};",
        {"sections": entry.sections, "tenant": tenant_id, "user": user_id},
    )
    if not rows:
        return []
    # Join: busca os seqs dos capítulos referenciados pelas sections.
    seen: set[str] = set()
    chapter_ids: list[RecordID] = []
    for row in rows:
        cid = row["material_chapter"]
        key = str(cid)
        if key not in seen:
            seen.add(key)
            chapter_ids.append(cid)
    ch_rows = db.query(
        "SELECT id, seq FROM material_chapter WHERE id IN $chapters "  # noqa: S608
        "AND tenant_id = $tenant AND user_id = $user;",
        {"chapters": chapter_ids, "tenant": tenant_id, "user": user_id},
    )
    chapter_seq_by_id = {str(r["id"]): int(r["seq"]) for r in ch_rows}
    by_id: dict[str, MaterialSection] = {
        str(r["id"]): _section_from_row(
            r, chapter_seq=chapter_seq_by_id.get(str(r["material_chapter"]), 0)
        )
        for r in rows
    }
    # Reordena conforme a ordem dos RecordIDs no plan_entry.
    return [by_id[str(sid)] for sid in entry.sections if str(sid) in by_id]


def count_lessons_for_plan(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
) -> int:
    """Conta lições preenchidas (com concept) para um plano.

    Lições placeholder (sem conteúdo gerado pelo Tutor) não contam — o
    scheduler re-tenta a mesma entrada até o Tutor preencher, em vez de
    avançar e deixar a lição permanentemente vazia (KUBO-168).
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT count() FROM lesson WHERE study_plan = $plan "  # noqa: S608
        f"AND concept != NONE AND {_MATERIAL_SCOPE} GROUP ALL;",
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    return rows[0]["count"] if rows else 0


def get_pending_lesson_for_entry(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    plan_entry_id: RecordID,
) -> RecordID | None:
    """Busca uma lição placeholder (sem concept) para re-tentar o fill.

    Se o Tutor falhou e deixou uma lição vazia, o scheduler re-tenta: em vez
    de criar nova (bateria na UNIQUE), busca a existente e chama fill_lesson
    novamente (KUBO-168).
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT id FROM lesson WHERE study_plan = $plan "  # noqa: S608
        f"AND plan_entry = $entry AND concept = NONE "
        f"AND {_MATERIAL_SCOPE} LIMIT 1;",
        {"plan": plan_id, "entry": plan_entry_id, "tenant": tenant_id, "user": user_id},
    )
    return rows[0]["id"] if rows else None


def transition_to_running(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    plan_id: RecordID,
    plan_entry_id: RecordID,
    scheduled_for: datetime,
) -> RecordID | None:
    """Transição atômica scheduled → running + cria 1ª lição (KUBO-166).

    Cria a lição, transiciona o tema para `running` e o plano para
    `status='running'` (congelado) numa única transação. Se qualquer
    statement falha, nada é persistido — evita o estado inconsistente de
    lição criada com tema ainda em `scheduled`. O CAS `AND state =
    'scheduled'` garante que não transiciona um tema que já está em
    `running` (idempotente). O `plan.status='running'` permite que
    `deactivate_plan` faça CAS em `status='active'` (só reverte se o
    plano ainda não foi congelado pelo scheduler).
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    if topic.state == "running":
        return  # idempotente: já transicionado (scheduler re-rodou)
    lesson_id = _fresh("lesson")
    transaction.run_transaction(
        db,
        [
            _CREATE_LESSON_SQL,
            f"UPDATE $plan SET status = 'running' WHERE {_PLAN_SCOPE} "  # noqa: S608
            f"AND status = 'active';",
            f"UPDATE $topic SET state = 'running' WHERE {_MATERIAL_SCOPE} "  # noqa: S608
            f"AND state = 'scheduled';",
        ],
        {
            "lesson": lesson_id,
            "tenant": tenant_id,
            "user": user_id,
            "plan": plan_id,
            "entry": plan_entry_id,
            "when": scheduled_for,
            "topic": topic_id,
        },
    )
    _log.info(
        "store.lesson.transition_to_running",
        lesson=str(lesson_id),
        topic=str(topic_id),
        scheduled_for=scheduled_for.isoformat(),
    )
    return lesson_id


# --- KUBO-167: archive / unarchive / delete / progress ----------------------------------


@dataclass(frozen=True)
class TopicDeleteSummary:
    """Contagem de dependentes para confirmação reforçada de delete (KUBO-167)."""

    materials: int
    plan_entries: int
    lessons: int
    chat_messages: int


@dataclass(frozen=True)
class TopicProgress:
    """Progresso do tema: lições concluídas / total + próxima lição (KUBO-167)."""

    done: int
    total: int
    next_lesson_id: RecordID | None = None
    next_lesson_date: datetime | None = None


def archive_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> None:
    """Arquiva um tema: state → 'archived', grava archived_from com estado anterior.

    O scheduler não gera lições para archived (filtra por 'scheduled'/'running').
    Desarquivar restaura o estado anterior via `unarchive_topic`.

    CAS em `state = $prev` fecha a janela TOCTOU com o scheduler: se o job
    `study_transition` transicionou para `running` entre o `get_topic` e o
    UPDATE, o CAS falha e nada é persistido (caller decide o que fazer).
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    if topic.state == "archived":
        raise StoreError("tema já está arquivado")
    result = db.query(
        f"UPDATE $topic SET state = 'archived', archived_from = $prev "  # noqa: S608
        f"WHERE {_MATERIAL_SCOPE} AND state = $prev RETURN id;",
        {"topic": topic_id, "tenant": tenant_id, "user": user_id, "prev": topic.state},
    )
    if not result:
        raise StoreError("state changed concurrently — archive aborted")
    _log.info("store.topic.archived", topic=str(topic_id), archived_from=topic.state)


def unarchive_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> None:
    """Desarquiva um tema: restaura estado de archived_from e limpa o campo.

    CAS em `state = 'archived'` fecha TOCTOU: se outro processo desarquivou
    primeiro, o UPDATE não aplica.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    if topic.state != "archived":
        raise StoreError("tema não está arquivado")
    # Lê archived_from do banco (não está no dataclass Topic).
    rows = db.query(
        f"SELECT archived_from FROM topic WHERE id = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    prev = rows[0]["archived_from"] if rows and rows[0]["archived_from"] else "draft"
    result = db.query(
        f"UPDATE $topic SET state = $prev, archived_from = NONE "  # noqa: S608
        f"WHERE {_MATERIAL_SCOPE} AND state = 'archived' RETURN id;",
        {"topic": topic_id, "tenant": tenant_id, "user": user_id, "prev": prev},
    )
    if not result:
        raise StoreError("state changed concurrently — unarchive aborted")
    _log.info("store.topic.unarchived", topic=str(topic_id), restored_to=prev)


def list_archived_topics(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
) -> list[Topic]:
    """Lista os temas ARQUIVADOS do usuário, mais recentes primeiro."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM topic WHERE {_MATERIAL_SCOPE} AND state = 'archived' "  # noqa: S608
        "ORDER BY created_at DESC;",
        {"tenant": tenant_id, "user": user_id},
    )
    return [_topic_from_row(row) for row in rows]


def delete_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> None:
    """Deleta um tema e todos os dependentes (cascade total, KUBO-167).

    Remove: study_log, study_chat, lessons, plan_entries, study_plan,
    material_sections, material_chapters, materials, topic. Arquivos no
    volume são removidos pela rota (best-effort). Tudo numa única transação
    atômica — falha no meio reverte tudo (não deixa tema órfão sem dependentes).
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError(_TOPIC_NOT_FOUND_MSG)
    # Busca IDs de materials, plans e lessons antes da transação (subqueries em
    # transação SurrealDB não veem estado intermediário corretamente).
    mat_rows = db.query(
        f"SELECT id FROM material WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    material_ids = [r["id"] for r in mat_rows]
    plan_rows = db.query(
        f"SELECT id FROM study_plan WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    plan_ids = [r["id"] for r in plan_rows]
    lesson_ids: list[RecordID] = []
    for pid in plan_ids:
        l_rows = db.query(
            f"SELECT id FROM lesson WHERE study_plan = $plan AND {_MATERIAL_SCOPE};",  # noqa: S608
            {"plan": pid, "tenant": tenant_id, "user": user_id},
        )
        lesson_ids.extend(r["id"] for r in l_rows)
    # Monta statements por ID (subquery em transação não funciona).
    # Nomes de parâmetro usam contador estável (não o ID do registro) — IDs
    # podem conter caracteres inválidos para identificadores SurrealQL.
    stmts: list[str] = []
    for i, _mid in enumerate(material_ids):
        stmts.append(f"DELETE FROM material_section WHERE material = $m_{i};")  # noqa: S608
        stmts.append(f"DELETE FROM material_chapter WHERE material = $m_{i};")  # noqa: S608
    for i, _pid in enumerate(plan_ids):
        stmts.append(f"DELETE FROM plan_entry WHERE study_plan = $p_{i};")  # noqa: S608
        stmts.append(f"DELETE FROM lesson WHERE study_plan = $p_{i};")  # noqa: S608
    for i, _lid in enumerate(lesson_ids):
        stmts.append(f"DELETE FROM study_log WHERE lesson = $l_{i};")  # noqa: S608
    stmts.extend(
        [
            f"DELETE FROM study_chat WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
            f"DELETE FROM study_plan WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
            f"DELETE FROM material WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
            f"DELETE FROM topic WHERE id = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
        ]
    )
    params: dict[str, Any] = {"topic": topic_id, "tenant": tenant_id, "user": user_id}
    for i, mid in enumerate(material_ids):
        params[f"m_{i}"] = mid
    for i, pid in enumerate(plan_ids):
        params[f"p_{i}"] = pid
    for i, lid in enumerate(lesson_ids):
        params[f"l_{i}"] = lid
    transaction.run_transaction(db, stmts, params)
    _log.info("store.topic.deleted", topic=str(topic_id))


def get_topic_delete_summary(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> TopicDeleteSummary:
    """Contagem de dependentes do tema para confirmação reforçada de delete."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    params = {"topic": topic_id, "tenant": tenant_id, "user": user_id}
    mat_rows = db.query(
        f"SELECT count() FROM material WHERE topic = $topic AND {_MATERIAL_SCOPE} GROUP ALL;",  # noqa: S608
        params,
    )
    # Busca plan_ids antes (subquery em SurrealDB não funciona).
    plan_rows = db.query(
        f"SELECT id FROM study_plan WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
        params,
    )
    plan_ids = [r["id"] for r in plan_rows]
    entry_count = 0
    lesson_count = 0
    # Tema tem 1 plano (1:1); usa o primeiro se existir.
    if plan_ids:
        pid = plan_ids[0]
        e_rows = db.query(
            f"SELECT count() FROM plan_entry WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE} GROUP ALL;",
            {"plan": pid, "tenant": tenant_id, "user": user_id},
        )
        entry_count = e_rows[0]["count"] if e_rows else 0
        l_rows = db.query(
            f"SELECT count() FROM lesson WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE} GROUP ALL;",
            {"plan": pid, "tenant": tenant_id, "user": user_id},
        )
        lesson_count = l_rows[0]["count"] if l_rows else 0
    chat_rows = db.query(
        f"SELECT count() FROM study_chat WHERE topic = $topic AND {_MATERIAL_SCOPE} GROUP ALL;",  # noqa: S608
        params,
    )
    return TopicDeleteSummary(
        materials=mat_rows[0]["count"] if mat_rows else 0,
        plan_entries=entry_count,
        lessons=lesson_count,
        chat_messages=chat_rows[0]["count"] if chat_rows else 0,
    )


def get_topic_progress(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
) -> TopicProgress:
    """Progresso do tema: lições concluídas / total de entries + próxima lição.

    - total = número de plan_entries do plano do tema (0 se sem plano).
    - done = número de lessons com study_log (concluídas).
    - next_lesson_id = primeira lesson sem study_log, ordenada por scheduled_for.

    Otimizado: 1 query de entries + 1 query de lessons + 1 query de study_logs
    (batch) — sem N+1 por lição.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    params = {"topic": topic_id, "tenant": tenant_id, "user": user_id}
    # Busca o plano do tema (1:1) + total de entries.
    plan_rows = db.query(
        f"SELECT id FROM study_plan WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
        params,
    )
    if not plan_rows:
        return TopicProgress(done=0, total=0, next_lesson_id=None)
    plan_id = plan_rows[0]["id"]
    entry_rows = db.query(
        f"SELECT count() FROM plan_entry WHERE study_plan = $plan "  # noqa: S608
        f"AND {_MATERIAL_SCOPE} GROUP ALL;",
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    total = entry_rows[0]["count"] if entry_rows else 0
    if total == 0:
        return TopicProgress(done=0, total=0, next_lesson_id=None)
    # Lessons do plano (ordenadas por scheduled_for) + study_logs (batch).
    lessons = db.query(
        f"SELECT id, scheduled_for FROM lesson WHERE study_plan = $plan "  # noqa: S608
        f"AND {_MATERIAL_SCOPE} ORDER BY scheduled_for;",
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    if not lessons:
        return TopicProgress(done=0, total=total, next_lesson_id=None)
    lesson_ids = [r["id"] for r in lessons]
    # Busca study_logs do tenant/user em batch (set em memória). O filtro final
    # `lid in done_ids` só conta lições deste plano (lesson_ids), então logs de
    # outros temas não afetam o resultado — apenas evita N+1 por lição.
    log_rows = db.query(
        f"SELECT lesson FROM study_log WHERE {_MATERIAL_SCOPE};",  # noqa: S608
        {"tenant": tenant_id, "user": user_id},
    )
    done_ids = {str(r["lesson"]) for r in log_rows}
    done = sum(1 for lid in lesson_ids if str(lid) in done_ids)
    next_row = next((r for r in lessons if str(r["id"]) not in done_ids), None)
    next_lesson_id = next_row["id"] if next_row else None
    next_lesson_date = next_row["scheduled_for"] if next_row else None
    return TopicProgress(
        done=done,
        total=total,
        next_lesson_id=next_lesson_id,
        next_lesson_date=next_lesson_date,
    )


def get_topics_progress_batch(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_ids: list[RecordID],
) -> dict[str, TopicProgress]:
    """Progresso em lote para a lista de temas — reutiliza o set de study_logs
    (1 query global) em vez de N queries por tema. Retorna dict indexado por
    str(topic_id) → TopicProgress.
    """
    if not topic_ids:
        return {}
    # Busca study_logs do tenant/user uma vez (set em memória).
    log_rows = db.query(
        f"SELECT lesson FROM study_log WHERE {_MATERIAL_SCOPE};",  # noqa: S608
        {"tenant": tenant_id, "user": user_id},
    )
    done_ids = {str(r["lesson"]) for r in log_rows}
    result: dict[str, TopicProgress] = {}
    for tid in topic_ids:
        # Busca o plano do tema (1:1).
        plan_rows = db.query(
            f"SELECT id FROM study_plan WHERE topic = $topic AND {_MATERIAL_SCOPE};",  # noqa: S608
            {"topic": tid, "tenant": tenant_id, "user": user_id},
        )
        if not plan_rows:
            result[str(tid)] = TopicProgress(done=0, total=0)
            continue
        plan_id = plan_rows[0]["id"]
        entry_rows = db.query(
            f"SELECT count() FROM plan_entry WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE} GROUP ALL;",
            {"plan": plan_id, "tenant": tenant_id, "user": user_id},
        )
        total = entry_rows[0]["count"] if entry_rows else 0
        if total == 0:
            result[str(tid)] = TopicProgress(done=0, total=0)
            continue
        lessons = db.query(
            f"SELECT id, scheduled_for FROM lesson WHERE study_plan = $plan "  # noqa: S608
            f"AND {_MATERIAL_SCOPE} ORDER BY scheduled_for;",
            {"plan": plan_id, "tenant": tenant_id, "user": user_id},
        )
        if not lessons:
            result[str(tid)] = TopicProgress(done=0, total=total)
            continue
        lesson_ids = [r["id"] for r in lessons]
        done = sum(1 for lid in lesson_ids if str(lid) in done_ids)
        next_row = next((r for r in lessons if str(r["id"]) not in done_ids), None)
        result[str(tid)] = TopicProgress(
            done=done,
            total=total,
            next_lesson_id=next_row["id"] if next_row else None,
            next_lesson_date=next_row["scheduled_for"] if next_row else None,
        )
    return result
