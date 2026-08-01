"""Persistência do módulo Estudos (ADR-0047): Tema container de N Materiais.

Material é dado PESSOAL — escopo `user` DENTRO do tenant (não só tenant): toda
leitura filtra por `tenant_id` E `user_id`, então um material de outro membro do
mesmo tenant é invisível (get devolve None). Contrato KUBO-123: argumentos
keyword-only e `assert_membership` no topo de toda função pública.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
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
    summary: str | None,
) -> Material:
    """Persiste material + capítulos atomicamente e devolve o material criado.

    Exclusivo a um Tema (N:1, ADR-0047): `topic_id` é obrigatório. `summary` é
    gerado síncrono no upload (consumido por `mentor` e `planner`).
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
        raise StoreError("tema não encontrado")
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
        raise StoreError("tema não encontrado")
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
        raise StoreError("tema não encontrado")
    transaction.run_transaction(
        db,
        [f"UPDATE $topic SET state = $state WHERE {_MATERIAL_SCOPE}"],  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id, "state": state},
    )
    _log.info("store.topic.state_changed", topic=str(topic_id), state=state)


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
    """Uma lição do plano: título + capítulos (RecordIDs de material_chapter)."""

    id: RecordID
    study_plan: RecordID
    tenant_id: RecordID
    user_id: RecordID
    seq: int
    title: str
    chapters: list[RecordID]
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
        chapters=list(row.get("chapters") or []),
        created_at=_as_datetime(row["created_at"]),
    )


_PLAN_SCOPE = f"topic = $topic AND {_MATERIAL_SCOPE}"


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
    for seq, (title, chapter_ids) in enumerate(entries, start=1):
        entry_id = _fresh("plan_entry")
        transaction.run_transaction(
            db,
            [
                "CREATE $entry SET study_plan = $plan, tenant_id = $tenant, "
                "user_id = $user, seq = $seq, title = $title, chapters = $chapters"
            ],
            {
                "entry": entry_id,
                "plan": plan_id,
                "tenant": tenant_id,
                "user": user_id,
                "seq": seq,
                "title": title,
                "chapters": chapter_ids,
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
