"""Navegação do módulo Estudos: o grupo na sidebar e as duas telas novas.

Unit com a store stubada — a persistência é assunto de `tests/store/test_study*.py`.
Aqui ficam a FORMA da nav (grupo próprio, ordem e posição entre Conhecimento e
Trabalho), o glifo de cada item e a leitura de `/study/topics` e `/study/new`.

O teste de glifo é parametrizado sobre a NAV inteira, não sobre os itens novos: o
macro `nav_icon` é um if/elif SEM else, então uma chave errada renderiza vazio em
silêncio — o item apareceria na sidebar sem ícone e nada falharia.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.nav import GROUP_KNOWLEDGE, GROUP_STUDY, GROUP_WORK, NAV
from kubo.api.rendering import _GROUP_TO_MOBILE_TAB, templates
from kubo.store.study import StudyPlan, Topic

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_CREATED = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _topic(key: str, title: str) -> Topic:
    return Topic(
        id=RecordID("topic", key),
        tenant_id=_TENANT,
        user_id=_USER,
        material=RecordID("material", "m1"),
        title=title,
        created_at=_CREATED,
    )


def _plan(status: str, target: datetime | None = None) -> StudyPlan:
    return StudyPlan(
        id=RecordID("study_plan", "p1"),
        tenant_id=_TENANT,
        user_id=_USER,
        topic=RecordID("topic", "t1"),
        status=status,
        weekdays=["mon", "tue", "wed", "thu", "fri"],
        target_date=target,
        activated_at=None,
        created_at=_CREATED,
    )


def _content(html: str) -> str:
    """Só o conteúdo da página, sem a sidebar — a NAV inteira vive em TODO HTML.

    Sem este corte, `href="/study/new"` (e os outros itens do grupo) seriam encontrados
    na sidebar e as asserções de tela passariam com o conteúdo apagado. Mesmo molde do
    fatiamento de `<aside>` em tests/api/test_shell.py.
    """
    return html[html.find("</aside>") :]


@pytest.fixture(autouse=True)
def stub_study_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leituras vazias por padrão; cada teste com dado sobrescreve o que precisa."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: None
    )


# --- NAV --------------------------------------------------------------------------------


def test_nav_has_study_group_with_three_items_in_order() -> None:
    """O grupo Estudos traz Materiais → Planos → Novo estudo, nessa ordem."""
    assert [(i["label"], i["route"]) for i in NAV if i["group"] == GROUP_STUDY] == [
        ("Materiais", "/study/materials"),
        ("Planos", "/study/topics"),
        ("Novo estudo", "/study/new"),
    ]


def test_study_group_sits_between_knowledge_and_work() -> None:
    """Posição do grupo na sidebar: Conhecimento → Estudos → Trabalho.

    A ordem dos GRUPOS é derivada da ordem dos itens (o header do grupo é renderizado
    na 1ª ocorrência), então o teste olha a sequência de grupos distintos.
    """
    groups = [i["group"] for i in NAV if i["group"] is not None]
    distinct = [g for pos, g in enumerate(groups) if pos == 0 or groups[pos - 1] != g]

    assert distinct.index(GROUP_KNOWLEDGE) < distinct.index(GROUP_STUDY)
    assert distinct.index(GROUP_STUDY) < distinct.index(GROUP_WORK)


def test_old_top_level_study_item_is_gone() -> None:
    """O item de topo "Estudos" some — quem representa o módulo agora é o grupo."""
    assert "Estudos" not in [i["label"] for i in NAV]


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


def test_more_page_lists_the_three_study_items(authed_client: TestClient) -> None:
    """Os 3 itens do grupo aparecem na LISTA da tab "Mais", juntos e na ordem da NAV."""
    body = _content(authed_client.get("/more").text)

    positions = [
        body.find('href="/study/materials"'),
        body.find('href="/study/topics"'),
        body.find('href="/study/new"'),
    ]
    assert all(pos != -1 for pos in positions), "item de Estudos ausente na lista de /more"
    assert positions == sorted(positions)


# --- Materiais (renomeada) --------------------------------------------------------------


def test_materials_page_header_says_materiais(authed_client: TestClient) -> None:
    """A tela antiga de "Estudos" passa a se chamar Materiais."""
    html = authed_client.get("/study/materials").text

    assert '<h1 class="text-2xl font-semibold tracking-tight">Materiais</h1>' in html


# --- Planos -----------------------------------------------------------------------------


def test_topics_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/study/topics", follow_redirects=False).status_code == 303


def test_topics_page_lists_topics_with_status_and_target(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cada tema aparece com título, badge do estado do plano, data-alvo e link."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_topics",
        lambda db, **kw: [_topic("t1", "Manual de Kubo")],
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic",
        lambda db, **kw: _plan("active", datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)),
    )

    resp = authed_client.get("/study/topics")
    body = _content(resp.text)

    assert resp.status_code == 200
    assert "Manual de Kubo" in body
    assert "Plano ativo" in body
    assert "Aug 20" in body  # data-alvo na tz de apresentação
    assert 'href="/study/topics/t1"' in body


def test_topics_page_marks_topic_without_plan(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema sem plano proposto é dito assim — não fica sem estado nenhum."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_topics",
        lambda db, **kw: [_topic("t2", "Livro sem plano")],
    )

    body = _content(authed_client.get("/study/topics").text)

    assert "Livro sem plano" in body
    assert "Sem plano" in body


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("proposed", "Proposta em revisão"),
        ("active", "Plano ativo"),
        ("paused", "Pausado"),
        ("completed", "Concluído"),
    ],
)
def test_topics_page_shows_every_plan_status(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, status: str, label: str
) -> None:
    """Os 4 estados do plano têm rótulo próprio — os MESMOS da tela do tema."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [_topic("t1", "Tema")]
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: _plan(status)
    )

    assert label in _content(authed_client.get("/study/topics").text)


def test_topics_page_empty_state_points_to_new_study(authed_client: TestClient) -> None:
    """Sem nenhum tema, a tela oferece o caminho de começar um estudo novo."""
    body = _content(authed_client.get("/study/topics").text)

    assert "Nenhum tema ainda" in body
    assert 'href="/study/new"' in body


# --- Novo estudo ------------------------------------------------------------------------


def test_new_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/study/new", follow_redirects=False).status_code == 303


def test_new_page_explains_three_steps(authed_client: TestClient) -> None:
    """A tela explica o fluxo: enviar material → criar tema → propor e ativar o plano."""
    resp = authed_client.get("/study/new")

    assert resp.status_code == 200
    assert "Enviar o material" in resp.text
    assert "Criar o tema" in resp.text
    assert "Propor e ativar o plano" in resp.text


def test_new_page_posts_to_the_existing_upload_route(authed_client: TestClient) -> None:
    """O envio é o MESMO POST /study/materials — nenhuma rota de escrita nova."""
    body = _content(authed_client.get("/study/new").text)

    assert 'action="/study/materials"' in body
    assert 'enctype="multipart/form-data"' in body
    assert re.search(r'name="csrf" value="[0-9a-f]+"', body), "csrf ausente no form"


# --- Portão das duas telas novas --------------------------------------------------------


@pytest.mark.parametrize("route", ["/study/topics", "/study/new"])
def test_pages_deny_when_session_is_unresolvable(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """Cookie válido mas sessão irresolúvel (usuário sumiu do tenant) é 403 em texto.

    O 303 do middleware cobre o anônimo; este é o outro portão — o do molde do módulo,
    que decide o que acontece DEPOIS de o cookie passar.
    """
    monkeypatch.setattr("kubo.api.routes.study.resolve_session", lambda request, db: None)

    resp = authed_client.get(route)

    assert resp.status_code == 403
    assert "Acesso negado" in resp.text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
