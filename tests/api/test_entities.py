"""Testes da tela de Entidades (M4, paridade tab `ConhecimentoScreen.jsx`, E2): lista
tipada com contagem de menções e detalhe com os destilados que mencionam. Sem
sparkline e sem relações (E2). Nome de entidade é hostil → escapado. Store mockada."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.knowledge import DistilledListItem, EntityListItem, EntityView


def _entity(**kw: object) -> EntityListItem:
    base: dict[str, object] = {
        "id": RecordID("entity", "e1"),
        "name": "Python",
        "kind": "tecnologia",
        "mentions": 3,
    }
    base.update(kw)
    return EntityListItem(**base)  # type: ignore[arg-type]


def _card(rid: str = "d1", title: str = "Post A") -> DistilledListItem:
    return DistilledListItem(
        id=RecordID("distilled", rid),
        summary="resumo",
        title=title,
        source_canonical="https://x/feed",
        source_kind="rss",
        created_at="2026-07-12T09:00:00+00:00",
    )


def test_entities_requires_auth(client: TestClient) -> None:
    assert client.get("/entities", follow_redirects=False).status_code == 303


def test_entities_empty_state(authed_client: TestClient) -> None:
    resp = authed_client.get("/entities")
    assert resp.status_code == 200
    assert "Nenhuma entidade" in resp.text


def test_entities_list_shows_name_kind_and_mentions(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lista mostra nome, badge de tipo e contagem de menções (E2)."""
    monkeypatch.setattr(
        "kubo.api.routes.entities.knowledge.list_entities",
        lambda db, **kw: [_entity(name="Python", kind="tecnologia", mentions=7)],
    )
    html = authed_client.get("/entities").text
    assert "Python" in html
    assert "tecnologia" in html
    assert "7 menções" in html


def test_entities_list_has_no_sparkline_or_relations(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2: nada de sparkline (svg de tendência) nem 'relações' na lista — corte por
    dado inexistente. Sanidade: não vaza a palavra 'relações' nem um <svg ... polyline>."""
    monkeypatch.setattr(
        "kubo.api.routes.entities.knowledge.list_entities",
        lambda db, **kw: [_entity()],
    )
    html = authed_client.get("/entities").text
    assert "Relações" not in html
    assert "polyline" not in html  # sparkline do mockup usa polyline


def test_entities_list_escapes_name(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nome de entidade é conteúdo derivado de LLM (hostil): escapado."""
    payload = "<script>alert('x')</script>"
    monkeypatch.setattr(
        "kubo.api.routes.entities.knowledge.list_entities",
        lambda db, **kw: [_entity(name=payload)],
    )
    html = authed_client.get("/entities").text
    assert payload not in html
    assert "&lt;script&gt;" in html


def test_entity_detail_shows_mentioning_distilled(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O detalhe mostra a entidade + os destilados que a mencionam (cards com título)."""
    monkeypatch.setattr(
        "kubo.api.routes.entities.knowledge.read_entity",
        lambda db, eid: EntityView(
            id=RecordID("entity", "e1"),
            name="Python",
            kind="tecnologia",
            mentions=1,
            distilled=[_card(title="Título do Post")],
        ),
    )
    html = authed_client.get("/entities/e1").text
    assert "Python" in html
    assert "Título do Post" in html
    assert "/distilled/d1" in html  # card linka pro destilado


def test_entities_search_filters_via_store(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busca passa `q` pra store (server-side); a barra reflete o termo ativo."""
    seen: dict[str, object] = {}

    def _list(db: object, **kw: object) -> list[EntityListItem]:
        seen.update(kw)
        return [_entity(name="Python")]

    monkeypatch.setattr("kubo.api.routes.entities.knowledge.list_entities", _list)
    monkeypatch.setattr("kubo.api.routes.entities.knowledge.count_entities", lambda db, **kw: 1)
    html = authed_client.get("/entities", params={"q": "pyth"}).text
    assert seen.get("query") == "pyth"  # q chegou na store
    assert 'value="pyth"' in html  # barra reflete o termo


def test_entities_has_view_toggle_and_pagination(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lista tem o view toggle (lista/grid2) e a paginação com total."""
    monkeypatch.setattr(
        "kubo.api.routes.entities.knowledge.list_entities",
        lambda db, **kw: [_entity(name=f"E{i}") for i in range(50)],
    )
    monkeypatch.setattr("kubo.api.routes.entities.knowledge.count_entities", lambda db, **kw: 90)
    html = authed_client.get("/entities").text
    assert 'data-view-group="entities"' in html  # container do view toggle
    assert 'data-view-btn="entities:grid2"' in html  # botão grid2
    assert "página 1 de 2" in html and "90 no total" in html


def test_entity_detail_404_for_unknown_id(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kubo.api.routes.entities.knowledge.read_entity", lambda db, eid: None)
    resp = authed_client.get("/entities/nope")
    assert resp.status_code == 404
    assert "não encontrada" in resp.text.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
