"""KUBO-163 — Store de chat: conversa persistida com mentor (Fase 1, draft).

Integração (SurrealDB real). Mensagens são salvas em `study_chat` e listadas
para reconstruir a conversa ao reabrir o Tema. Escopo `user` dentro do tenant.
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
    create_chat_message,
    create_topic,
    list_chat_messages,
    set_topic_fields,
)

pytestmark = pytest.mark.integration

_STUDY_DB = "test_study_chat"


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


# --- create_chat_message + list_chat_messages -------------------------------------------


def test_create_and_list_chat_messages(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Mensagens são persistidas e listadas em ordem cronológica."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        phase="draft",
        role="user",
        content="Quero estudar agentic coding.",
    )
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        phase="draft",
        role="assistant",
        content="Ótimo! Vamos definir o foco.",
    )

    messages = list_chat_messages(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, phase="draft"
    )
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Quero estudar agentic coding."
    assert messages[1].role == "assistant"


def test_list_chat_messages_scoped_to_topic(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Mensagens de um Tema não vazam para outro."""
    topic_a = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="A")
    topic_b = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="B")
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_a.id,
        phase="draft",
        role="user",
        content="Mensagem A",
    )
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic_b.id,
        phase="draft",
        role="user",
        content="Mensagem B",
    )

    messages_a = list_chat_messages(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic_a.id, phase="draft"
    )
    assert len(messages_a) == 1
    assert messages_a[0].content == "Mensagem A"


def test_list_chat_messages_scoped_to_phase(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Mensagens da fase draft não aparecem na fase planning (e vice-versa)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        phase="draft",
        role="user",
        content="Mensagem draft",
    )
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        phase="planning",
        role="user",
        content="Mensagem planning",
    )

    draft_msgs = list_chat_messages(
        db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id, phase="draft"
    )
    assert len(draft_msgs) == 1
    assert draft_msgs[0].content == "Mensagem draft"


def test_list_chat_messages_scoped_to_owner(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Outro membro do mesmo tenant não vê mensagens alheias."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Privado")
    create_chat_message(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        phase="draft",
        role="user",
        content="Privado",
    )
    other = tenancy.create_user(db, firebase_uid="other-chat-uid")
    tenancy.create_membership(db, user_id=other.id, tenant_id=tenant_id, role="member")

    messages = list_chat_messages(
        db, tenant_id=tenant_id, user_id=other.id, topic_id=topic.id, phase="draft"
    )
    assert messages == []


# --- set_topic_fields (focus, depth) ----------------------------------------------------


def test_set_topic_fields_updates_focus_and_depth(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """set_topic_fields atualiza focus e depth no Tema."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    set_topic_fields(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        focus="Sistemas agênticos",
        depth="aprofundado",
    )
    from kubo.store.study import get_topic

    updated = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.focus == "Sistemas agênticos"
    assert updated.depth == "aprofundado"


def test_set_topic_fields_partial_preserves_other_field(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """set_topic_fields com _UNSET preserva o campo não passado (não clobber)."""
    from kubo.store.study import _UNSET, get_topic

    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    # Seta os dois campos primeiro.
    set_topic_fields(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        focus="Rust",
        depth="aprofundado",
    )
    # Atualiza só focus — depth deve sobreviver (sentinela _UNSET).
    set_topic_fields(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        focus="Python",
        depth=_UNSET,
    )
    updated = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.focus == "Python"
    assert updated.depth == "aprofundado"  # preservado, não clobber


def test_set_topic_fields_clears_field_with_none(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """set_topic_fields com None limpa o campo (diferente de _UNSET)."""
    from kubo.store.study import get_topic

    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    set_topic_fields(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        focus="Rust",
        depth="aprofundado",
    )
    # None limpa focus — depth deve sobreviver.
    set_topic_fields(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        topic_id=topic.id,
        focus=None,
    )
    updated = get_topic(db, tenant_id=tenant_id, user_id=user_id, topic_id=topic.id)
    assert updated is not None
    assert updated.focus is None
    assert updated.depth == "aprofundado"


def test_set_topic_fields_rejects_archived(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Tema arquivado é só leitura — set_topic_fields recusa (StoreError)."""
    topic = create_topic(db, tenant_id=tenant_id, user_id=user_id, title="Tema")
    # Forçar estado archived diretamente no banco.
    db.query("UPDATE $topic SET state = 'archived';", {"topic": topic.id})
    with pytest.raises(StoreError):
        set_topic_fields(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            topic_id=topic.id,
            focus="X",
            depth=None,
        )
