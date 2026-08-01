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
from typing import TYPE_CHECKING, Any, Literal

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
_ENTRY_SCOPE = f"study_plan = $plan AND {_MATERIAL_SCOPE}"
# `lesson` também aponta o plano por `study_plan`: mesmo recorte da lição planejada.
_LESSON_SCOPE = _ENTRY_SCOPE

# Único estado editável do plano (ADR-0043): ativar congela a meta.
_PROPOSED = "proposed"
# Único estado que produz lição (KUBO-137): o job da véspera só olha para estes.
_ACTIVE = "active"
# Estados do ciclo de vida (KUBO-138): pausado congela a régua, completo fecha o plano.
_PAUSED = "paused"
_COMPLETED = "completed"
# `seq` fora da faixa válida (1..N), usado como estacionamento nas trocas de posição:
# o índice `plan_entry_seq` é UNIQUE e não tolera duas lições no mesmo lugar nem por
# um passo intermediário.
_TEMP_SEQ = -1


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


# --- Tema e plano de estudo (KUBO-136) -------------------------------------------------
#
# Mesmo contrato do Material acima: keyword-only, `assert_membership` no topo, filtro por
# tenant E user. Um tema/plano de outro membro do mesmo tenant é invisível, não "negado".


@dataclass(frozen=True)
class Topic:
    """Tema de estudo: um material lido com um objetivo (1 material = 1 tema)."""

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    material: RecordID
    title: str
    created_at: datetime


@dataclass(frozen=True)
class StudyPlan:
    """Plano do tema: cadência + data-alvo derivada. Só `active` produz lição.

    `target_date` é recalculada a cada edição enquanto `proposed` e CONGELADA na
    ativação (ADR-0043): a meta é o que o dono aprovou, não o que a cadência diria
    depois.
    """

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    topic: RecordID
    status: str
    weekdays: list[str]
    target_date: datetime | None
    activated_at: datetime | None
    created_at: datetime
    # Pausa (KUBO-138): `paused_at` marca desde quando o plano está parado; `paused_days`
    # acumula os dias de estudo congelados por todas as pausas já retomadas — é o que
    # impede o atraso de crescer enquanto o dono não está estudando. Defaults porque a
    # maioria dos planos nunca pausa (e para não quebrar quem já constrói o dataclass).
    paused_at: datetime | None = None
    paused_days: int = 0


@dataclass(frozen=True)
class PlanEntry:
    """Uma lição planejada: posição (`seq`, contíguo 1..N) e capítulos que cobre."""

    id: RecordID
    study_plan: RecordID
    seq: int
    title: str
    chapters: list[RecordID]


@dataclass(frozen=True)
class PlanEntryInput:
    """Entrada de lição na gravação da proposta (sem id: a store cria)."""

    seq: int
    title: str
    chapter_ids: Sequence[RecordID]


def _topic_from_row(row: dict[str, Any]) -> Topic:
    """Constrói um `Topic` a partir de uma linha do banco."""
    return Topic(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        material=row["material"],
        title=row["title"],
        created_at=_as_datetime(row["created_at"]),
    )


def _plan_from_row(row: dict[str, Any]) -> StudyPlan:
    """Constrói um `StudyPlan` a partir de uma linha do banco."""
    target = row.get("target_date")
    activated = row.get("activated_at")
    paused = row.get("paused_at")
    return StudyPlan(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        topic=row["topic"],
        status=row["status"],
        weekdays=list(row.get("weekdays") or []),
        target_date=_as_datetime(target) if target is not None else None,
        activated_at=_as_datetime(activated) if activated is not None else None,
        created_at=_as_datetime(row["created_at"]),
        paused_at=_as_datetime(paused) if paused is not None else None,
        # Plano gravado antes da migração 0024 não tem o campo: zero é "nunca pausou".
        paused_days=int(row.get("paused_days") or 0),
    )


def _entry_from_row(row: dict[str, Any]) -> PlanEntry:
    """Constrói um `PlanEntry` a partir de uma linha do banco."""
    return PlanEntry(
        id=row["id"],
        study_plan=row["study_plan"],
        seq=int(row["seq"]),
        title=row["title"],
        chapters=list(row.get("chapters") or []),
    )


def create_topic(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    material_id: RecordID,
    title: str,
) -> Topic:
    """Cria o tema de um material do usuário.

    Material de outro usuário é INEXISTENTE daqui (StoreError, não "negado"); material
    que já tem tema também falha — o índice UNIQUE é a regra "1 material = 1 tema".
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    material = get_material(db, tenant_id=tenant_id, user_id=user_id, material_id=material_id)
    if material is None:
        raise StoreError("material não encontrado")
    topic_id = _fresh("topic")
    transaction.run_transaction(
        db,
        [
            "CREATE $topic SET tenant_id = $tenant, user_id = $user, "
            "material = $material, title = $title"
        ],
        {
            "topic": topic_id,
            "tenant": tenant_id,
            "user": user_id,
            "material": material_id,
            "title": title,
        },
    )
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError("topic vanished during creation")
    _log.info("store.topic.created", topic=str(topic_id), material=str(material_id))
    return topic


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


def get_topic_for_material(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, material_id: RecordID
) -> Topic | None:
    """Tema do material, se já existe — o que decide entre 'Criar tema' e 'Ver tema'."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM topic WHERE material = $material AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"material": material_id, "tenant": tenant_id, "user": user_id},
    )
    return _topic_from_row(rows[0]) if rows else None


def list_topics(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[Topic]:
    """Lista os temas do usuário no tenant, mais recentes primeiro."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM topic WHERE {_MATERIAL_SCOPE} ORDER BY created_at DESC;",  # noqa: S608
        {"tenant": tenant_id, "user": user_id},
    )
    return [_topic_from_row(row) for row in rows]


def save_plan_proposal(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    topic_id: RecordID,
    weekdays: Sequence[str],
    target_date: datetime | None,
    entries: Sequence[PlanEntryInput],
) -> StudyPlan:
    """Grava (ou substitui) a proposta de plano do tema, atomicamente.

    Repropor sobre um plano `proposed` apaga plano e lições e recria — propor de novo é
    recomeçar a curadoria, não acumular lições órfãs. Sobre plano já ativado é StoreError:
    o plano ativo é o compromisso congelado, e reescrevê-lo apagaria o que o dono aprovou.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    topic = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise StoreError("tema não encontrado")
    existing = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if existing is not None and existing.status != _PROPOSED:
        raise StoreError("plano já ativado não pode ser reproposto")
    plan_id = _fresh("study_plan")
    params: dict[str, Any] = {
        "plan": plan_id,
        "topic": topic_id,
        "tenant": tenant_id,
        "user": user_id,
        "weekdays": list(weekdays),
        "target": target_date,
    }
    statements: list[str] = []
    if existing is not None:
        # O plano antigo é APAGADO (id novo), não reaproveitado: as lições da proposta
        # anterior somem com ele, e nada da curadoria descartada sobrevive por engano.
        statements += [
            f"DELETE plan_entry WHERE study_plan = $old AND {_MATERIAL_SCOPE}",  # noqa: S608
            f"DELETE study_plan WHERE id = $old AND {_MATERIAL_SCOPE}",  # noqa: S608
        ]
        params["old"] = existing.id
    statements.append(
        "CREATE $plan SET tenant_id = $tenant, user_id = $user, topic = $topic, "
        f"status = '{_PROPOSED}', weekdays = $weekdays, target_date = $target"
    )
    for i, entry in enumerate(entries):
        statements.append(
            f"CREATE $e{i} SET study_plan = $plan, tenant_id = $tenant, user_id = $user, "
            f"seq = $es{i}, title = $et{i}, chapters = $ec{i}"
        )
        params |= {
            f"e{i}": _fresh("plan_entry"),
            f"es{i}": entry.seq,
            f"et{i}": entry.title,
            f"ec{i}": list(entry.chapter_ids),
        }
    transaction.run_transaction(db, statements, params)

    plan = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_id)
    if plan is None:
        raise StoreError("study plan vanished during creation")
    _log.info("store.study_plan.proposed", plan=str(plan_id), lessons=len(entries))
    return plan


def get_plan_for_topic(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, topic_id: RecordID
) -> StudyPlan | None:
    """Plano do tema (1 tema = 1 plano); None enquanto nada foi proposto."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM study_plan WHERE topic = $topic AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"topic": topic_id, "tenant": tenant_id, "user": user_id},
    )
    return _plan_from_row(rows[0]) if rows else None


def list_plan_entries(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> list[PlanEntry]:
    """Lições do plano ordenadas por `seq`, sem paginação (o domínio limita a 200)."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM plan_entry WHERE {_ENTRY_SCOPE} ORDER BY seq;",  # noqa: S608
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    return [_entry_from_row(row) for row in rows]


def _editable_plan(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> StudyPlan:
    """Plano do usuário em estado editável, ou StoreError legível.

    Plano de outro usuário e plano inexistente falham igual (invisível é invisível);
    plano já ativado falha com outra mensagem — a edição pertence à fase de proposta,
    e mexer no plano ativo apagaria o compromisso que o dono já assumiu.
    """
    return _plan_in_status(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        expected=_PROPOSED,
        refused="plano já ativado não pode ser editado",
    )


def remove_plan_entry(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    seq: int,
    new_target: datetime | None,
) -> None:
    """Remove uma lição do plano proposto e re-sequencia as demais (1..N contíguo).

    `new_target` vem pronto de quem chama: o cálculo da data-alvo é do domínio
    (`kubo.study.planning`), não da store. Plano não-proposto é StoreError.

    `seq` que não existe no plano não faz nada (mesma postura do `move` na ponta da
    lista): a re-sequenciação abaixo desloca TODAS as lições acima de `seq`, então um
    `seq` fora da faixa (0, negativo, ou maior que N) deslocaria o plano inteiro sem
    apagar nada — corrompendo a contiguidade 1..N em silêncio, porque nenhum passo
    viola o UNIQUE. Verificado contra o banco antes de existir esta cerca.
    """
    _editable_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    occupied = {
        entry.seq
        for entry in list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    }
    if seq not in occupied:
        return
    # Re-sequenciar em dois passos pelo NEGATIVO: `plan_entry_seq` é UNIQUE, e decrementar
    # direto faria a lição N+1 aterrissar sobre a N enquanto a N ainda não saiu do lugar.
    # A faixa negativa está fora do domínio dos `seq` válidos, então nenhum passo colide.
    transaction.run_transaction(
        db,
        [
            f"DELETE plan_entry WHERE {_ENTRY_SCOPE} AND seq = $seq",  # noqa: S608
            f"UPDATE plan_entry SET seq = -seq WHERE {_ENTRY_SCOPE} AND seq > $seq",  # noqa: S608
            f"UPDATE plan_entry SET seq = (-seq) - 1 WHERE {_ENTRY_SCOPE} AND seq < 0",  # noqa: S608
            f"UPDATE study_plan SET target_date = $target WHERE id = $plan AND {_MATERIAL_SCOPE}",  # noqa: S608
        ],
        {
            "plan": plan_id,
            "tenant": tenant_id,
            "user": user_id,
            "seq": seq,
            "target": new_target,
        },
    )
    _log.info("store.study_plan.entry_removed", plan=str(plan_id), seq=seq)


def move_plan_entry(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    seq: int,
    direction: Literal["up", "down"],
) -> None:
    """Troca a lição de posição com a vizinha, dentro da transação.

    Mover a primeira para cima (ou a última para baixo) não faz nada — é o fim da lista,
    não um erro. Plano não-proposto é StoreError.
    """
    _editable_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    neighbour = seq - 1 if direction == "up" else seq + 1
    occupied = {
        entry.seq
        for entry in list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    }
    if seq not in occupied or neighbour not in occupied:
        # Fim da lista: sem a checagem, a troca "cega" gravaria a lição num `seq` fora
        # da faixa (o vizinho que não existe), quebrando a contiguidade 1..N.
        return
    # Mesmo motivo do negativo em `remove_plan_entry`: a troca passa por um `seq`
    # temporário fora da faixa para não esbarrar no índice UNIQUE no meio do caminho.
    transaction.run_transaction(
        db,
        [
            f"UPDATE plan_entry SET seq = $temp WHERE {_ENTRY_SCOPE} AND seq = $seq",  # noqa: S608
            f"UPDATE plan_entry SET seq = $seq WHERE {_ENTRY_SCOPE} AND seq = $other",  # noqa: S608
            f"UPDATE plan_entry SET seq = $other WHERE {_ENTRY_SCOPE} AND seq = $temp",  # noqa: S608
        ],
        {
            "plan": plan_id,
            "tenant": tenant_id,
            "user": user_id,
            "seq": seq,
            "other": neighbour,
            "temp": _TEMP_SEQ,
        },
    )
    _log.info("store.study_plan.entry_moved", plan=str(plan_id), seq=seq, direction=direction)


def set_plan_cadence(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    weekdays: Sequence[str],
    new_target: datetime | None,
) -> None:
    """Troca os dias da semana do plano proposto e grava a nova data-alvo."""
    _editable_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    transaction.run_transaction(
        db,
        [
            "UPDATE study_plan SET weekdays = $weekdays, target_date = $target "  # noqa: S608
            f"WHERE id = $plan AND {_MATERIAL_SCOPE}"
        ],
        {
            "plan": plan_id,
            "tenant": tenant_id,
            "user": user_id,
            "weekdays": list(weekdays),
            "target": new_target,
        },
    )
    _log.info("store.study_plan.cadence_set", plan=str(plan_id), weekdays=len(list(weekdays)))


def activate_plan(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> StudyPlan:
    """Ativa o plano proposto: carimba `activated_at` e congela a data-alvo.

    Ativar um plano já ativo é StoreError legível — o duplo clique não pode reiniciar a
    meta que já está valendo.
    """
    plan = _editable_plan(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    transaction.run_transaction(
        db,
        [
            "UPDATE study_plan SET status = 'active', activated_at = time::now() "  # noqa: S608
            f"WHERE id = $plan AND {_MATERIAL_SCOPE}"
        ],
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    active = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=plan.topic)
    if active is None:
        raise StoreError("study plan vanished during activation")
    _log.info("store.study_plan.activated", plan=str(plan_id))
    return active


# --- Lição e registro de estudo (KUBO-137) ---------------------------------------------
#
# Mesmo contrato das seções acima: keyword-only, `assert_membership` no topo, filtro por
# tenant E user. A Lição é gerada pelo job da véspera e lida na UI pelo dono; o Registro
# de estudo é o que ele respondeu, e alimenta a recapitulação da lição seguinte.

if TYPE_CHECKING:
    # Só para TIPO: `kubo.study.tutor` importa `MaterialChapter` DAQUI, então um import
    # em runtime fecharia o ciclo (módulo parcialmente inicializado) e derrubaria tudo
    # que depende da store de Estudos.
    from kubo.study.tutor import LessonOutput


@dataclass(frozen=True)
class Lesson:
    """A lição de um dia de estudo: os blocos gerados e o quiz, congelados no banco.

    `quiz` é lista de dicts (campo FLEXIBLE) porque a lição é um SNAPSHOT: a correção
    de uma lição antiga tem que usar o quiz como ele foi gerado, mesmo que o modelo da
    persona mude depois.
    """

    id: RecordID
    tenant_id: RecordID
    user_id: RecordID
    study_plan: RecordID
    plan_entry: RecordID
    scheduled_for: datetime
    concept: str
    scenario: str
    application: str
    recap: str | None
    quiz: list[dict[str, Any]]
    created_at: datetime
    provenance: list[RecordID] | None = None


@dataclass(frozen=True)
class StudyLog:
    """Registro de estudo: o que o dono respondeu, quanto acertou e como reagiu."""

    id: RecordID
    lesson: RecordID
    answers: list[int]
    correct_count: int
    reaction: str | None
    completed_at: datetime


def _lesson_from_row(row: dict[str, Any]) -> Lesson:
    """Constrói uma `Lesson` a partir de uma linha do banco."""
    return Lesson(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        study_plan=row["study_plan"],
        plan_entry=row["plan_entry"],
        scheduled_for=_as_datetime(row["scheduled_for"]),
        concept=row["concept"],
        scenario=row["scenario"],
        application=row["application"],
        recap=row.get("recap"),
        quiz=_quiz_of(row),
        provenance=list(row.get("provenance") or []),
        created_at=_as_datetime(row["created_at"]),
    )


def _quiz_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Quiz congelado da lição (campo FLEXIBLE) como lista de dicts."""
    raw: list[dict[str, Any]] = list(row.get("quiz") or [])
    return [dict(item) for item in raw]


def _log_from_row(row: dict[str, Any]) -> StudyLog:
    """Constrói um `StudyLog` a partir de uma linha do banco."""
    answers: list[Any] = list(row.get("answers") or [])
    return StudyLog(
        id=row["id"],
        lesson=row["lesson"],
        answers=[int(a) for a in answers],
        correct_count=int(row["correct_count"]),
        reaction=row.get("reaction"),
        completed_at=_as_datetime(row["completed_at"]),
    )


def list_chapters_by_ids(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    chapter_ids: Sequence[RecordID],
) -> list[MaterialChapter]:
    """Capítulos do usuário pelos ids pedidos, NA ORDEM em que foram pedidos.

    É como o job da véspera carrega os capítulos de uma lição do plano (que aponta
    capítulos por id, possivelmente fora de ordem de `seq`). Id de outro usuário
    simplesmente não volta — invisível é invisível, não "negado".
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    ids = list(chapter_ids)
    if not ids:
        return []
    rows = db.query(
        f"SELECT * FROM material_chapter WHERE id IN $ids AND {_MATERIAL_SCOPE};",  # noqa: S608
        {"ids": ids, "tenant": tenant_id, "user": user_id},
    )
    # Chaveado por `str(id)`: `RecordID` compara por igualdade mas NÃO é hashable
    # (verificado contra o SDK) — usá-lo como chave de dict/set explode em runtime.
    by_id = {str(row["id"]): _chapter_from_row(row) for row in rows}
    return [by_id[key] for key in (str(chapter_id) for chapter_id in ids) if key in by_id]


def list_active_plans(db: Any, *, tenant_id: RecordID, user_id: RecordID) -> list[StudyPlan]:
    """Planos ATIVOS do usuário no tenant — os únicos que produzem lição."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM study_plan WHERE status = '{_ACTIVE}' AND {_MATERIAL_SCOPE} "  # noqa: S608
        "ORDER BY created_at;",
        {"tenant": tenant_id, "user": user_id},
    )
    return [_plan_from_row(row) for row in rows]


def create_lesson(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    entry_id: RecordID,
    scheduled_for: datetime,
    output: LessonOutput,
    provenance: Sequence[RecordID] | None = None,
) -> Lesson:
    """Persiste a lição de um dia do plano e devolve o registro criado.

    (plano, dia) é UNIQUE: uma segunda geração para o mesmo dia é StoreError legível.
    É o que torna o job da véspera idempotente — várias janelas de retry no mesmo dia
    não podem render duas lições para o mesmo dia de estudo.

    `provenance` são os capítulos do material que originaram o conteúdo da lição.
    Quando omitido, o banco preenche com o default vazio para lições antigas.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    lesson_id = _fresh("lesson")
    transaction.run_transaction(
        db,
        [
            "CREATE $lesson SET tenant_id = $tenant, user_id = $user, study_plan = $plan, "
            "plan_entry = $entry, scheduled_for = $day, concept = $concept, "
            "scenario = $scenario, application = $application, recap = $recap, "
            "provenance = $provenance, quiz = $quiz"
        ],
        {
            "lesson": lesson_id,
            "tenant": tenant_id,
            "user": user_id,
            "plan": plan_id,
            "entry": entry_id,
            "day": scheduled_for,
            "concept": output.concept,
            "scenario": output.scenario,
            "application": output.application,
            "recap": output.recap,
            "provenance": list(provenance) if provenance is not None else [],
            "quiz": [item.model_dump() for item in output.quiz],
        },
    )
    lesson = get_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson_id)
    if lesson is None:
        raise StoreError("lesson vanished during creation")
    _log.info(
        "store.lesson.created",
        lesson=str(lesson_id),
        plan=str(plan_id),
        day=scheduled_for.date().isoformat(),
    )
    return lesson


def get_lesson(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, lesson_id: RecordID
) -> Lesson | None:
    """Lê uma lição do usuário; None se não existe ou é de outro usuário."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM lesson WHERE id = $lesson AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"lesson": lesson_id, "tenant": tenant_id, "user": user_id},
    )
    return _lesson_from_row(rows[0]) if rows else None


def get_lesson_for_day(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    scheduled_for: datetime,
) -> Lesson | None:
    """Lição do plano marcada para um dia; None quando ainda não foi gerada."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM lesson WHERE {_LESSON_SCOPE} AND scheduled_for = $day LIMIT 1;",  # noqa: S608
        {"plan": plan_id, "day": scheduled_for, "tenant": tenant_id, "user": user_id},
    )
    return _lesson_from_row(rows[0]) if rows else None


def list_lessons(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    limit: int,
    start: int,
) -> list[Lesson]:
    """Página do histórico de lições do plano, mais recentes primeiro."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    page = max(1, min(int(limit), _MAX_PAGE))
    offset = max(0, int(start))
    # Mesmo quirk de LIMIT/START de `list_chapters`: literais já clampados, nunca
    # conteúdo vindo de fora.
    query = (
        f"SELECT * FROM lesson WHERE {_LESSON_SCOPE} "  # noqa: S608
        f"ORDER BY scheduled_for DESC LIMIT {page} START {offset};"
    )
    rows = db.query(query, {"plan": plan_id, "tenant": tenant_id, "user": user_id})
    return [_lesson_from_row(row) for row in rows]


def count_lessons(db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID) -> int:
    """Total de lições já geradas para o plano."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT count() FROM lesson WHERE {_LESSON_SCOPE} GROUP ALL;",  # noqa: S608
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    return int(rows[0]["count"]) if rows else 0


def next_unlessoned_entry(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> PlanEntry | None:
    """Menor `seq` do plano que ainda não virou lição; None quando o plano acabou.

    A ORDEM DO PLANO NUNCA MUDA (ADR-0043): o que decide a próxima lição é o `seq`
    aprovado pelo dono, não o desempenho nem a data.
    """
    entries = list_plan_entries(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    rows = db.query(
        f"SELECT plan_entry FROM lesson WHERE {_LESSON_SCOPE};",  # noqa: S608
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    lessoned = {str(row["plan_entry"]) for row in rows}
    return next((entry for entry in entries if str(entry.id) not in lessoned), None)


def complete_lesson(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    lesson_id: RecordID,
    answers: Sequence[int],
    correct_count: int,
    reaction: str | None,
) -> StudyLog:
    """Grava o registro de estudo da lição e devolve o que ficou no banco.

    Um registro POR LIÇÃO (índice UNIQUE): concluir de novo é StoreError legível — o
    segundo envio não pode reescrever o desempenho que já alimentou a recapitulação.
    `correct_count` vem pronto de quem chama: a correção é contra o quiz da lição, e
    quem tem o quiz em mãos é a rota.
    """
    lesson = get_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson_id)
    if lesson is None:
        # Lição de outro usuário e lição inexistente falham IGUAL, e falham ANTES de
        # qualquer escrita: um registro gravado aqui carimbaria desempenho na lição alheia.
        raise StoreError("lição não encontrada")
    transaction.run_transaction(
        db,
        [
            "CREATE $log SET tenant_id = $tenant, user_id = $user, lesson = $lesson, "
            "answers = $answers, correct_count = $correct, reaction = $reaction"
        ],
        {
            "log": _fresh("study_log"),
            "tenant": tenant_id,
            "user": user_id,
            "lesson": lesson.id,
            "answers": [int(answer) for answer in answers],
            "correct": int(correct_count),
            "reaction": reaction,
        },
    )
    log = get_log_for_lesson(db, tenant_id=tenant_id, user_id=user_id, lesson_id=lesson.id)
    if log is None:
        raise StoreError("study log vanished during creation")
    _log.info(
        "store.study_log.created",
        lesson=str(lesson.id),
        correct=int(correct_count),
        reaction=reaction,
    )
    return log


def get_log_for_lesson(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, lesson_id: RecordID
) -> StudyLog | None:
    """Registro de estudo da lição; None enquanto ela não foi concluída."""
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM study_log WHERE lesson = $lesson AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"lesson": lesson_id, "tenant": tenant_id, "user": user_id},
    )
    return _log_from_row(rows[0]) if rows else None


def recent_misses(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID, limit: int = 10
) -> list[str]:
    """Enunciados errados nos registros mais recentes do plano, do mais novo ao mais velho.

    É a matéria-prima da recapitulação: a lição seguinte abre pelo que o dono errou.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    scope = {"plan": plan_id, "tenant": tenant_id, "user": user_id}
    lessons = db.query(
        f"SELECT * FROM lesson WHERE {_LESSON_SCOPE};",  # noqa: S608
        scope,
    )
    if not lessons:
        return []
    quizzes = {str(row["id"]): _quiz_of(row) for row in lessons}
    logs = db.query(
        f"SELECT * FROM study_log WHERE lesson IN $lessons AND {_MATERIAL_SCOPE} "  # noqa: S608
        "ORDER BY completed_at DESC;",
        {
            "lessons": [row["id"] for row in lessons],
            "tenant": tenant_id,
            "user": user_id,
        },
    )
    misses: list[str] = []
    for row in logs:
        log = _log_from_row(row)
        misses.extend(_missed_questions(quizzes.get(str(log.lesson), []), log.answers))
        if len(misses) >= limit:
            break
    return misses[:limit]


def _missed_questions(quiz: Sequence[dict[str, Any]], answers: Sequence[int]) -> list[str]:
    """Enunciados das questões cuja resposta marcada não é a certa.

    Questão sem resposta correspondente (registro mais curto que o quiz) NÃO conta como
    erro: o que alimenta a recapitulação é o que o dono errou, não o que faltou casar.
    """
    return [
        str(item.get("question", ""))
        for item, answer in zip(quiz, answers, strict=False)
        if answer != item.get("answer_index")
    ]


# --- Ciclo de vida do plano (KUBO-138) -------------------------------------------------
#
# Mesmo contrato das seções acima: keyword-only, `assert_membership` no topo, filtro por
# tenant E user. `paused_days` é ACUMULADOR: o cálculo de quantos dias congelar é do
# domínio (`kubo.study.progress`), a store só soma o que recebe — mesma divisão de
# `new_target` nas edições da proposta.


def _plan_in_status(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    expected: str,
    refused: str,
) -> StudyPlan:
    """Plano do usuário no estado exigido, ou StoreError legível.

    Molde de `_editable_plan`, com o estado como PARÂMETRO: plano de outro usuário e
    plano inexistente falham igual (invisível é invisível); estado errado falha com a
    mensagem da transição que o chamou. A leitura é a mesma da escrita seguinte, então
    a transição nunca depende do estado que o chamador imaginou ter.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM study_plan WHERE id = $plan AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    if not rows:
        raise StoreError("plano não encontrado")
    plan = _plan_from_row(rows[0])
    if plan.status != expected:
        raise StoreError(refused)
    return plan


def _reload_plan(db: Any, *, tenant_id: RecordID, user_id: RecordID, plan: StudyPlan) -> StudyPlan:
    """Relê o plano depois da transição — o que volta é o que ficou no banco."""
    stored = get_plan_for_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=plan.topic)
    if stored is None:
        raise StoreError("study plan vanished during transition")
    return stored


def pause_plan(db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID) -> StudyPlan:
    """Pausa o plano ATIVO: grava `paused_at` e para a geração da véspera e o sino.

    Qualquer outro estado é StoreError legível — pausar um plano proposto ou já
    completo não tem significado, e deixar passar carimbaria `paused_at` num plano
    que nunca contou dias.
    """
    plan = _plan_in_status(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        expected=_ACTIVE,
        refused="plano não está ativo",
    )
    transaction.run_transaction(
        db,
        [
            f"UPDATE study_plan SET status = '{_PAUSED}', paused_at = time::now() "  # noqa: S608
            f"WHERE id = $plan AND {_MATERIAL_SCOPE}"
        ],
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    _log.info("store.study_plan.paused", plan=str(plan_id))
    return _reload_plan(db, tenant_id=tenant_id, user_id=user_id, plan=plan)


def resume_plan(
    db: Any,
    *,
    tenant_id: RecordID,
    user_id: RecordID,
    plan_id: RecordID,
    paused_days_add: int,
    new_target: datetime | None,
) -> StudyPlan:
    """Retoma o plano pausado: soma os dias congelados, limpa `paused_at`, grava o alvo.

    `paused_days_add` é somado ao acumulador (não substitui): duas pausas seguidas
    congelam a régua duas vezes. Plano não-pausado é StoreError.
    """
    plan = _plan_in_status(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        expected=_PAUSED,
        refused="plano não está pausado",
    )
    # A soma é feita AQUI, sobre o plano que a guarda acabou de ler, e não numa expressão
    # SurrealQL sobre o próprio campo: o valor gravado fica explícito no parâmetro.
    total_paused = plan.paused_days + max(0, int(paused_days_add))
    transaction.run_transaction(
        db,
        [
            f"UPDATE study_plan SET status = '{_ACTIVE}', paused_at = NONE, "  # noqa: S608
            f"paused_days = $days, target_date = $target WHERE id = $plan AND {_MATERIAL_SCOPE}"
        ],
        {
            "plan": plan_id,
            "tenant": tenant_id,
            "user": user_id,
            "days": total_paused,
            "target": new_target,
        },
    )
    _log.info("store.study_plan.resumed", plan=str(plan_id), paused_days=total_paused)
    return _reload_plan(db, tenant_id=tenant_id, user_id=user_id, plan=plan)


def complete_plan(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> StudyPlan:
    """Fecha o plano ATIVO cujas lições acabaram — transição feita pelo job do sino."""
    plan = _plan_in_status(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        plan_id=plan_id,
        expected=_ACTIVE,
        refused="plano não está ativo",
    )
    transaction.run_transaction(
        db,
        [
            f"UPDATE study_plan SET status = '{_COMPLETED}' "  # noqa: S608
            f"WHERE id = $plan AND {_MATERIAL_SCOPE}"
        ],
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    _log.info("store.study_plan.completed", plan=str(plan_id))
    return _reload_plan(db, tenant_id=tenant_id, user_id=user_id, plan=plan)


def _plan_lesson_ids(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> list[RecordID]:
    """Ids das lições do plano — o recorte por usuário das duas leituras de conclusão.

    Lição de outro usuário nunca entra na lista, então o registro de estudo dele
    também não: um corte só por tenant somaria o avanço do colega ao do dono.
    """
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT id FROM lesson WHERE {_LESSON_SCOPE};",  # noqa: S608
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    return [row["id"] for row in rows]


def count_completed_lessons(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> int:
    """Quantas lições do plano têm registro de estudo — o `done` da régua de progresso.

    NÃO é `count_lessons`: aquela conta lição GERADA, esta conta lição ESTUDADA.
    """
    lessons = _plan_lesson_ids(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    if not lessons:
        return 0
    rows = db.query(
        f"SELECT count() FROM study_log WHERE lesson IN $lessons AND {_MATERIAL_SCOPE} "  # noqa: S608
        "GROUP ALL;",
        {"lessons": lessons, "tenant": tenant_id, "user": user_id},
    )
    return int(rows[0]["count"]) if rows else 0


def completion_dates(
    db: Any, *, tenant_id: RecordID, user_id: RecordID, plan_id: RecordID
) -> list[datetime]:
    """`completed_at` dos registros do plano, do mais antigo ao mais novo.

    Devolve datetime em UTC (o storage); a conversão para o dia LOCAL do dono é de
    quem chama — o domínio raciocina em `date`, a store nunca inventa timezone.
    """
    lessons = _plan_lesson_ids(db, tenant_id=tenant_id, user_id=user_id, plan_id=plan_id)
    if not lessons:
        return []
    rows = db.query(
        f"SELECT completed_at FROM study_log WHERE lesson IN $lessons AND {_MATERIAL_SCOPE} "  # noqa: S608
        "ORDER BY completed_at;",
        {"lessons": lessons, "tenant": tenant_id, "user": user_id},
    )
    return [_as_datetime(row["completed_at"]) for row in rows]
