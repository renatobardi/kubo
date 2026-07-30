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
from typing import Any, Literal

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

# Único estado editável do plano (ADR-0043): ativar congela a meta.
_PROPOSED = "proposed"
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
    tenancy.assert_membership(db, user_id=user_id, tenant_id=tenant_id)
    rows = db.query(
        f"SELECT * FROM study_plan WHERE id = $plan AND {_MATERIAL_SCOPE} LIMIT 1;",  # noqa: S608
        {"plan": plan_id, "tenant": tenant_id, "user": user_id},
    )
    if not rows:
        raise StoreError("plano não encontrado")
    plan = _plan_from_row(rows[0])
    if plan.status != _PROPOSED:
        raise StoreError("plano já ativado não pode ser editado")
    return plan


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
