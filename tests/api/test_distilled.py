"""Testes das rotas de Destilados (9.4): autoescape via rota REAL (a defesa central
de XSS), degradação da busca, paginação prev/next, detalhe + proveniência, 404.

A store e o embedder são mockados — estes são testes de rota (unit), não de
integração. O que importa aqui é o comportamento da rota + o HTML renderizado."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.errors import ConfigError, EmbeddingError
from kubo.store.knowledge import (
    DistilledListItem,
    DistilledView,
    ProvenanceItem,
    RunRef,
    SearchHit,
)

# Payload de XSS: se aparecer CRU no HTML, o autoescape falhou. base.html tem
# <script src=...> legítimo, então a asserção é sobre ESTE payload específico.
_XSS = "<script>alert('xss')</script>"


def _card(rid: str, summary: str, *, title: str = "t") -> DistilledListItem:
    """Card de destilado para os mocks de rota — só os campos que a lista renderiza."""
    return DistilledListItem(
        id=RecordID("distilled", rid),
        summary=summary,
        title=title,
        source_canonical="https://x/feed",
        source_kind="rss",
        created_at="2026-07-12T00:00:00Z",
    )


@contextmanager
def _fake_connect(cfg: Any = None) -> Any:
    """connect() falso: as funções da store estão mockadas e ignoram o db."""
    yield object()


class _FakeEmbedder:
    """Embedder falso: devolve um vetor fixo, sem tocar a rede."""

    @classmethod
    def from_env(cls, **_kw: Any) -> "_FakeEmbedder":
        return cls()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768 for _ in texts]


@pytest.fixture
def patch_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutraliza a conexão real e o embedder; cada teste mocka a leitura que usa."""
    monkeypatch.setattr("kubo.api.routes.distilled.client.connect", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.distilled.GeminiEmbedder", _FakeEmbedder)


def _view(summary: str, items: list[ProvenanceItem] | None = None) -> DistilledView:
    return DistilledView(
        id=RecordID("distilled", "x1"),
        summary=summary,
        claims=[],
        items=items or [],
        runs=[RunRef(worker="feed", status="ok")],
        entities=[],
    )


# ---- autoescape via rota real (critério de aceite incortável) ----


def test_list_escapes_summary(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lista renderiza summary hostil escapado (não injeta <script>)."""
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.list_distilled",
        lambda db, **kw: [_card("x1", _XSS)],
    )
    html = authed_client.get("/distilled").text
    assert "&lt;script&gt;alert(&#39;xss&#39;)" in html
    assert _XSS not in html


def test_list_card_shows_title_source_and_date(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrofit M5/E3: o card do browse mostra título do item, fonte e data (não só summary)."""
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.list_distilled",
        lambda db, **kw: [_card("x1", "resumo", title="Título do Post")],
    )
    html = authed_client.get("/distilled").text
    assert "Título do Post" in html
    assert "https://x/feed" in html  # fonte (canonical)


def test_list_escapes_title(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O título vem de item.title (conteúdo coletado, hostil): renderizado escapado."""
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.list_distilled",
        lambda db, **kw: [_card("x1", "resumo", title=_XSS)],
    )
    html = authed_client.get("/distilled").text
    assert _XSS not in html
    assert "&lt;script&gt;" in html


def test_search_partial_escapes_summary(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O partial da busca renderiza summary hostil escapado."""
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.search",
        lambda db, **kw: [
            SearchHit(
                distilled=RecordID("distilled", "x1"), chunk=RecordID("chunk", "c"), score=0.1
            )
        ],
    )
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.read_distilled", lambda db, rid: _view(_XSS)
    )
    html = authed_client.get("/distilled/search", params={"q": "algo"}).text
    assert "&lt;script&gt;" in html
    assert _XSS not in html


def test_detail_escapes_summary_and_neutralizes_hostile_url(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O detalhe escapa summary/título hostis E não transforma uma URL `javascript:`
    coletada num href clicável (o autoescape não filtra esquema de URL)."""
    hostile_item = ProvenanceItem(
        external_id="e1",
        url="javascript:alert(1)",
        title="<img src=x onerror=alert(1)>",
        source_canonical="https://feed",
        source_title=None,
        source_kind="rss",
    )
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.read_distilled",
        lambda db, rid: _view(_XSS, items=[hostile_item]),
    )
    html = authed_client.get("/distilled/x1").text
    # summary + título escapados
    assert "&lt;script&gt;" in html
    assert _XSS not in html
    assert "<img src=x onerror" not in html
    # url javascript: NUNCA vira href clicável
    assert 'href="javascript:' not in html


# ---- degradação da busca (E-f) ----


def test_search_degrades_without_embedder(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem GEMINI_API_KEY (ConfigError), a busca devolve alerta tinted, 200, sem crash."""

    def _raise(**_kw: Any) -> Any:
        raise ConfigError("sem key")

    monkeypatch.setattr(_FakeEmbedder, "from_env", staticmethod(_raise))
    resp = authed_client.get("/distilled/search", params={"q": "algo"})
    assert resp.status_code == 200
    assert "indisponível" in resp.text.lower()


def test_search_degrades_on_embedding_error(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha de rede do embedder (EmbeddingError) também degrada, não explode."""

    def _boom(self: Any, texts: Any) -> Any:
        raise EmbeddingError("timeout")

    monkeypatch.setattr(_FakeEmbedder, "embed", _boom)
    resp = authed_client.get("/distilled/search", params={"q": "algo"})
    assert resp.status_code == 200
    assert "indisponível" in resp.text.lower()


def test_search_empty_query_is_empty_partial(authed_client: TestClient, patch_store: None) -> None:
    """Query vazia não chama embedder nem store — partial vazio, 200."""
    resp = authed_client.get("/distilled/search", params={"q": "   "})
    assert resp.status_code == 200
    assert "indisponível" not in resp.text.lower()


# ---- paginação / detalhe / 404 ----


def test_list_pagination_total_and_next(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paginação 0011: 'página X de Y · N no total' + seletor 50/100 + 'Próximos' quando
    há mais que uma página; sem 'Anteriores' na 1ª. Total = 120 → 3 páginas de 50."""
    rows = [_card(f"x{i}", f"s{i}") for i in range(50)]
    monkeypatch.setattr("kubo.api.routes.distilled.knowledge.list_distilled", lambda db, **kw: rows)
    monkeypatch.setattr("kubo.api.routes.distilled.knowledge.count_distilled", lambda db: 120)
    html = authed_client.get("/distilled").text
    assert "página 1 de 3" in html
    assert "120 no total" in html
    assert "por página" in html and ">100<" in html  # seletor de tamanho
    assert "Próximos" in html
    assert "Anteriores" not in html  # start=0 não tem página anterior


def test_detail_404_for_unknown_id(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Id inexistente: 404 com a tela de não-encontrado."""
    monkeypatch.setattr("kubo.api.routes.distilled.knowledge.read_distilled", lambda db, rid: None)
    resp = authed_client.get("/distilled/inexistente")
    assert resp.status_code == 404
    assert "não encontrado" in resp.text.lower()


def test_detail_renders_provenance_chain(
    authed_client: TestClient, patch_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O detalhe mostra a cadeia de proveniência (item → source) e o run produtor."""
    item = ProvenanceItem(
        external_id="e1",
        url="https://example.com/post",
        title="Um post",
        source_canonical="https://feed",
        source_title="Meu Feed",
        source_kind="rss",
    )
    monkeypatch.setattr(
        "kubo.api.routes.distilled.knowledge.read_distilled",
        lambda db, rid: _view("resumo", items=[item]),
    )
    html = authed_client.get("/distilled/x1").text
    assert "Um post" in html
    assert "Meu Feed" in html
    assert 'href="https://example.com/post"' in html
    assert "feed" in html  # worker do run


def test_no_template_uses_safe_filter() -> None:
    """Proibição executável (ADR-0014): NENHUM template usa `|safe`. Conteúdo coletado
    (summary/claim/título/url) é hostil; `|safe` desligaria o autoescape e armaria XSS."""
    from pathlib import Path

    templates_dir = Path(__file__).resolve().parents[2] / "kubo" / "api" / "templates"
    offenders = [
        p.relative_to(templates_dir)
        for p in templates_dir.rglob("*.html")
        if "|safe" in p.read_text(encoding="utf-8").replace(" ", "")
    ]
    assert offenders == [], f"templates com |safe (proibido): {offenders}"


def test_distilled_routes_require_auth(client: TestClient) -> None:
    """Sem sessão, as rotas de Destilados redirecionam pro login (guard)."""
    assert client.get("/distilled", follow_redirects=False).status_code == 303
    assert client.get("/distilled/x1", follow_redirects=False).status_code == 303


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
