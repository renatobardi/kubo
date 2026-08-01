"""KUBO-161 — Store de Tema: criação vazia, lista, rename, estados.

Integração (SurrealDB real). O Tema nasce SEM material (container de N Materiais,
ADR-0047) e tem `state` explícito (`draft` → `planning` → `scheduled` → `running`
→ `archived`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store import client, migrations, tenancy
from kubo.store.study import (
    create_topic,
    get_topic,
    list_topics,
    set_topic_name,
)

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_topics"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois."""
    cfg = replace(client.config(), database=_STUDY_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_STUDY_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_STUDY_DB};")


def _other_member(db: Any, tenant_id: RecordID) -> RecordID:
    """Segundo usuário, MEMBRO DO MESMO TENANT — o vizinho que não pode ver o tema."""
    other = tenancy.create_user(db, firebase_uid="other-topic-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")
    return other.id


# --- Criação de Tema vazio ---------------------------------------------------------------


def test_create_topic_without_material(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Tema nasce vazio (sem material), em estado `draft`."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Meu estudo")

    assert topic.title == "Meu estudo"
    assert topic.state == "draft"
    assert topic.tenant_id == tenant_id
    assert topic.user_id == user_id


def test_create_topic_is_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro do MESMO tenant não vê o tema do dono."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Privado")
    other_id = _other_member(db, tenant_id)

    assert get_topic(db, tenant_id=tenant_id, user_id=other_id, topic_id=topic.id) is None
    assert list_topics(db, tenant_id=tenant_id, user_id=other_id) == []


def test_list_topics_returns_newest_first(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Lista vem mais recentes primeiro (por created_at DESC)."""
    first = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Primeiro")
    second = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Segundo")

    topics = list_topics(db, tenant_id=tenant_id, user_id=user_id)
    assert [t.id for t in topics] == [second.id, first.id]


def test_list_topics_excludes_archived(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Temas arquivados não aparecem na lista de ativos (ADR-0047 §5)."""
    active = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Ativo")
    archived = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Arquivado")
    # Marca o segundo como archived diretamente no banco (sem rota de arquivar nesta fatia).
    db.query("UPDATE $topic SET state = 'archived';", {"topic": archived.id})

    topics = list_topics(db, tenant_id=tenant_id, user_id=user_id)

    assert [t.id for t in topics] == [active.id]


# --- Rename ------------------------------------------------------------------------------


def test_set_topic_name_updates_title(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Rename atualiza o título do tema."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Velho nome")
    set_topic_name(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, title="Novo nome")

    updated = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.title == "Novo nome"


def test_set_topic_name_scoped_to_owner(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Outro membro não pode renomear tema alheio (StoreError, não silêncio)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Meu tema")
    other_id = _other_member(db, tenant_id)

    with pytest.raises(StoreError):
        set_topic_name(
            db, tenant_id=tenant_id, user_id=other_id, topic_id=topic.id, title="Hackeado"
        )


def test_set_topic_name_rejects_archived(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Tema arquivado é só leitura — rename levanta StoreError (ADR-0047 §3)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Arquivado")
    db.query("UPDATE $topic SET state = 'archived';", {"topic": topic.id})

    with pytest.raises(StoreError, match="arquivado"):
        set_topic_name(
            db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, title="Novo nome"
        )
