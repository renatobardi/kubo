"""Navegação do módulo Estudos (ADR-0047, KUBO-161): grupo na sidebar.

Unit com a store stubada. Aqui ficam a FORMA da nav (grupo próprio, posição entre
Conhecimento e Trabalho), o glifo de cada item e a leitura de `/study/topics`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.nav import GROUP_KNOWLEDGE, GROUP_STUDY, GROUP_WORK, NAV
from kubo.api.rendering import _GROUP_TO_MOBILE_TAB, templates
from kubo.store.study import Topic, TopicProgress

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_CREATED = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _topic(key: str, title: str, state: str = "draft") -> Topic:
    return Topic(
        id=RecordID("topic", key),
        tenant_id=_TENANT,
        user_id=_USER,
        title=title,
        state=state,
        created_at=_CREATED,
    )


def _content(html: str) -> str:
    """Só o conteúdo da página, sem a sidebar — a NAV inteira vive em TODO HTML."""
    return html[html.find("</aside>") :]


@pytest.fixture(autouse=True)
def stub_study_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leituras vazias por padrão; cada teste com dado sobrescreve o que precisa."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_progress",
        lambda db, **kw: TopicProgress(done=0, total=0),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topics_progress_batch",
        lambda db, **kw: {},
    )


# --- NAV --------------------------------------------------------------------------------


def test_nav_has_study_group_with_single_item() -> None:
    """O grupo Estudos tem um único item (Estudos → /study/topics)."""
    assert [(i["label"], i["route"]) for i in NAV if i["group"] == GROUP_STUDY] == [
        ("Estudos", "/study/topics"),
    ]


def test_study_group_sits_between_knowledge_and_work() -> None:
    """Posição do grupo na sidebar: Conhecimento → Estudos → Trabalho."""
    groups = [i["group"] for i in NAV if i["group"] is not None]
    distinct = [g for pos, g in enumerate(groups) if pos == 0 or groups[pos - 1] != g]

    assert distinct.index(GROUP_KNOWLEDGE) < distinct.index(GROUP_STUDY)
    assert distinct.index(GROUP_STUDY) < distinct.index(GROUP_WORK)


@pytest.mark.parametrize("icon", sorted({i["icon"] for i in NAV}))
def test_every_nav_icon_renders_a_glyph(icon: str) -> None:
    """Todo `icon` da NAV casa um ramo do macro — chave desconhecida sairia vazia."""
    rendered = templates.env.from_string(
        '{% import "_macros.html" as m %}{{ m.nav_icon(icon) }}'
    ).render(icon=icon)

    assert "<svg" in rendered, f"glifo ausente para o ícone {icon}"


def test_study_group_maps_to_the_more_mobile_tab() -> None:
    """Estudos não ganha aba própria: cai na tab "Mais" (padrão dos grupos sem tab)."""
    assert _GROUP_TO_MOBILE_TAB.get(GROUP_STUDY) == "more"


def test_more_page_lists_the_study_item(authed_client: TestClient) -> None:
    """O item de Estudos aparece na LISTA da tab "Mais"."""
    body = _content(authed_client.get("/more").text)
    assert 'href="/study/topics"' in body


# --- Lista de Temas ---------------------------------------------------------------------


def test_topics_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/study/topics", follow_redirects=False).status_code == 303


def test_topics_page_lists_topics_with_name_and_state(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cada tema aparece com título, badge do estado e link."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_topics",
        lambda db, **kw: [_topic("t1", "Agentic Coding", "draft")],
    )

    resp = authed_client.get("/study/topics")
    body = _content(resp.text)

    assert resp.status_code == 200
    assert "Agentic Coding" in body
    assert "Rascunho" in body
    assert 'href="/study/topics/t1"' in body


def test_topics_page_empty_state(authed_client: TestClient) -> None:
    """Sem nenhum tema, a tela oferece começar um estudo novo."""
    body = _content(authed_client.get("/study/topics").text)

    assert "Nenhum estudo ainda" in body


def test_topics_page_has_new_study_button(authed_client: TestClient) -> None:
    """A lista tem o botão 'Novo estudo' (POST /study/topics)."""
    body = _content(authed_client.get("/study/topics").text)
    assert 'action="/study/topics"' in body
    assert "Novo estudo" in body


# --- Portão -----------------------------------------------------------------------------


def test_topics_page_denies_when_session_is_unresolvable(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cookie válido mas sessão irresolúvel (usuário sumiu do tenant) é 403 em texto."""
    monkeypatch.setattr("kubo.api.routes.study.resolve_session", lambda request, db: None)

    resp = authed_client.get("/study/topics")

    assert resp.status_code == 403
    assert "Acesso negado" in resp.text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
