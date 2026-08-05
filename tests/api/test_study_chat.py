"""KUBO-163 — Chat com mentor: SSE streaming, persistência, sugestões.

Testes de rota com store/mentor mockados. O COMPORTAMENTO de persistência
vive nos testes de integração da store; aqui ficam o molde das rotas (CSRF,
sessão, SSE, validação de estado) e a renderização do template.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import ChatMessage, Material, Topic
from kubo.study.mentor import MentorReply

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_TOPIC_ID = RecordID("topic", "abc123")


def _topic(**kw: object) -> Topic:
    base: dict[str, object] = {
        "id": _TOPIC_ID,
        "tenant_id": _TENANT,
        "user_id": _USER,
        "title": "Estudo de Agentic Coding",
        "state": "draft",
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Topic(**base)  # type: ignore[arg-type]


def _material(**kw: object) -> Material:
    base: dict[str, object] = {
        "id": RecordID("material", "mat1"),
        "tenant_id": _TENANT,
        "user_id": _USER,
        "topic": _TOPIC_ID,
        "title": "Manual de Kubo",
        "fmt": "epub",
        "original_filename": "manual.epub",
        "file_path": "/data/materials/manual.epub",
        "size_bytes": 1024,
        "chapter_count": 3,
        "summary": "Um guia sobre agentes.",
        "created_at": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Material(**base)  # type: ignore[arg-type]


def _chat_msg(role: str, content: str, msg_id: str = "msg1") -> ChatMessage:
    return ChatMessage(
        id=RecordID("study_chat", msg_id),
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        phase="draft",
        role=role,
        content=content,
        created_at=datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc),
    )


@pytest.fixture(autouse=True)
def stub_chat_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stubs comuns para rotas de chat: store mockado, persona mockada."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.client.connect", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_materials_by_topic",
        lambda db, **kw: [_material()],
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_ready_materials_by_topic",
        lambda db, **kw: 1,
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chat_messages", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.create_chat_message",
        lambda db, **kw: _chat_msg(kw.get("role", "user"), kw.get("content", "")),
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_fields", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.resolve_persona",
        lambda *a, **kw: type(
            "P", (), {"prompt": "Você é o mentor.", "model": "anthropic/claude-haiku-4-5"}
        )(),
    )

    # Mentor.stream_chat mockado: yields chunks fixos.
    def _fake_stream(self: Any, **kw: Any) -> Any:
        yield "Olá! "
        yield "Posso ajudar."

    monkeypatch.setattr("kubo.api.routes.study.Mentor.stream_chat", _fake_stream)
    # extract_reply mockado: devolve reply sem sugestões.
    monkeypatch.setattr(
        "kubo.api.routes.study.extract_reply",
        lambda text: MentorReply(text=text),
    )


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form da lista de Temas."""
    html = authed_client.get("/study/topics").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


# --- POST /topics/{key}/chat (SSE) ------------------------------------------------------


def test_chat_returns_sse_stream(authed_client: TestClient) -> None:
    """POST /chat devolve text/event-stream com chunks do mentor."""
    resp = authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Quero estudar agentes.", "csrf": _csrf(authed_client)},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    body = resp.text
    # SSE events: data: {...}\n\n
    assert "data:" in body


def test_chat_persists_user_and_assistant_messages(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rota persiste a mensagem do dono e a resposta do mentor."""
    persisted: list[dict[str, Any]] = []

    def _track_create(db: Any, **kw: Any) -> ChatMessage:
        persisted.append(kw)
        return _chat_msg(kw.get("role", "user"), kw.get("content", ""))

    monkeypatch.setattr("kubo.api.routes.study.study_store.create_chat_message", _track_create)

    authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Quero estudar agentes.", "csrf": _csrf(authed_client)},
    )

    roles = [p["role"] for p in persisted]
    assert "user" in roles
    assert "assistant" in roles
    user_msg = next(p for p in persisted if p["role"] == "user")
    assert user_msg["content"] == "Quero estudar agentes."


def test_chat_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Olá.", "csrf": "invalid"},
    )
    assert resp.status_code == 403


def test_chat_requires_draft_state(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat só funciona em draft; planning devolve 400."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="planning")
    )
    resp = authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Olá.", "csrf": _csrf(authed_client)},
    )
    assert resp.status_code == 400


def test_chat_requires_at_least_one_material(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat sem materiais no Tema devolve 400 (chat desabilitado até ≥1 Material ready)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_ready_materials_by_topic", lambda db, **kw: 0
    )
    resp = authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Olá.", "csrf": _csrf(authed_client)},
    )
    assert resp.status_code == 400


def test_chat_topic_not_found(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tema inexistente devolve 404."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    resp = authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Olá.", "csrf": _csrf(authed_client)},
    )
    assert resp.status_code == 404


def test_chat_sends_suggestion_events(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sugestões do mentor são enviadas no evento SSE 'done' como JSON."""
    monkeypatch.setattr(
        "kubo.api.routes.study.extract_reply",
        lambda text: MentorReply(
            text=text,
            suggested_name="Agentes em Python",
            suggested_focus="Sistemas agênticos",
            suggested_depth="aprofundado",
        ),
    )

    resp = authed_client.post(
        "/study/topics/abc123/chat",
        data={"message": "Sugere algo.", "csrf": _csrf(authed_client)},
    )

    body = resp.text
    # O evento 'done' carrega as sugestões como JSON.
    assert "event: done" in body
    assert "Agentes em Python" in body
    assert "Sistemas ag" in body  # focus (JSON-escaped)
    assert "aprofundado" in body


# --- POST /topics/{key}/fields (apply focus/depth) --------------------------------------


def test_set_fields_updates_focus(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /fields atualiza focus do Tema (parcial — depth preserva)."""
    called: dict[str, Any] = {}

    def _track_fields(db: Any, **kw: Any) -> None:
        called.update(kw)

    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_fields", _track_fields)

    resp = authed_client.post(
        "/study/topics/abc123/fields",
        data={"field": "focus", "value": "Sistemas agênticos", "csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert called["focus"] == "Sistemas agênticos"
    # depth não está nos kwargs → store preserva (sentinela _UNSET)
    assert "depth" not in called


def test_set_fields_updates_depth(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /fields atualiza depth do Tema (parcial — focus preserva)."""
    called: dict[str, Any] = {}

    def _track_fields(db: Any, **kw: Any) -> None:
        called.update(kw)

    monkeypatch.setattr("kubo.api.routes.study.study_store.set_topic_fields", _track_fields)

    resp = authed_client.post(
        "/study/topics/abc123/fields",
        data={"field": "depth", "value": "aprofundado", "csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert called["depth"] == "aprofundado"
    assert "focus" not in called


def test_set_fields_requires_csrf(authed_client: TestClient) -> None:
    """CSRF inválido devolve 403."""
    resp = authed_client.post(
        "/study/topics/abc123/fields",
        data={"field": "focus", "value": "X", "csrf": "invalid"},
    )
    assert resp.status_code == 403


def test_set_fields_rejects_invalid_depth(authed_client: TestClient) -> None:
    """Profundidade fora dos valores válidos devolve 400."""
    resp = authed_client.post(
        "/study/topics/abc123/fields",
        data={"field": "depth", "value": "absurdo", "csrf": _csrf(authed_client)},
    )
    assert resp.status_code == 400


def test_set_fields_rejects_invalid_field(authed_client: TestClient) -> None:
    """Campo fora de focus/depth devolve 400."""
    resp = authed_client.post(
        "/study/topics/abc123/fields",
        data={"field": "title", "value": "X", "csrf": _csrf(authed_client)},
    )
    assert resp.status_code == 400


def test_set_fields_rejects_non_draft(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /fields fora de draft devolve 400 (Tema congelado)."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic(state="planning")
    )
    resp = authed_client.post(
        "/study/topics/abc123/fields",
        data={"field": "focus", "value": "X", "csrf": _csrf(authed_client)},
        follow_redirects=False,
    )
    assert resp.status_code == 400
