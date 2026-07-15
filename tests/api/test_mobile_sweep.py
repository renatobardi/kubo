"""Varredura por tela (0019 marco 19.4): telas de detalhe usam o mecanismo
mobile_back_href/mobile_title (provado no board, 19.3); Destilados tem busca sticky
em mobile (único caso, sacrifício de timebox pré-declarado)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.knowledge import DistilledView, EntityView, RunRef


@contextmanager
def _fake_connect(cfg: Any = None) -> Any:
    yield object()


def _mobile_header_block(html: str) -> str:
    start = html.find("text-[1.875rem]")
    header_start = html.rfind("<header", 0, start)
    return html[header_start : html.find("</header>", start) + len("</header>")]


def test_distilled_detail_mobile_header_shows_item_title(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O header mobile do detalhe de Destilado mostra o título real (1ª linha do
    summary aqui, sem item bruto) e chevron-voltar pra /distilled."""
    monkeypatch.setattr("kubo.api.routes.distilled.client.connect", _fake_connect)
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.related_distilled", lambda db, rid, **kw: []
    )
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.read_distilled",
        lambda db, rid: DistilledView(
            id=RecordID("distilled", "x1"),
            summary="Resumo do destilado sobre memória",
            claims=[],
            items=[],
            runs=[RunRef(worker="feed", status="ok")],
            entities=[],
        ),
    )
    html = authed_client.get("/distilled/x1").text
    header = _mobile_header_block(html)
    assert "Resumo do destilado sobre memória" in header
    assert 'href="/distilled"' in header


def test_entity_detail_mobile_header_shows_entity_name(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O header mobile do detalhe de Entidade mostra o nome da entidade (não o rótulo
    genérico 'Entidades') e chevron-voltar pra /entities."""
    monkeypatch.setattr(
        "kubo.api.routes.entities.knowledge.read_entity",
        lambda db, eid: EntityView(
            id=RecordID("entity", "e1"), name="Python", kind="tecnologia", mentions=3, distilled=[]
        ),
    )
    html = authed_client.get("/entities/e1").text
    header = _mobile_header_block(html)
    assert "Python" in header
    assert ">Entidades<" not in header
    assert 'href="/entities"' in header


def test_distilled_search_form_is_sticky_on_mobile(authed_client: TestClient) -> None:
    """A busca de Destilados gruda no topo em mobile (max-md:sticky) — único screen com
    esse tratamento (sacrifício de timebox pré-declarado, marco 19.4)."""
    html = authed_client.get("/distilled").text
    form_start = html.find("<form")
    form_tag = html[form_start : html.find(">", form_start)]
    assert "max-md:sticky" in form_tag
    assert "max-md:top-0" in form_tag


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
