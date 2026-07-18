"""Testes da tela de Fontes (paridade `FontesScreen.jsx`): lista com kind, itens acumulados
e badge de recência factual (E4) + a ação de escrita "Adicionar fonte" (#105). Store mockada:
o COMPORTAMENTO da escrita (create_source, duplicata) vive no test_sources_write (integração);
aqui ficam a leitura e o MOLDE da rota de escrita (CSRF/validação/fail-fast) — ADR-0018."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.knowledge import SourceDetail, SourceStat


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form 'Adicionar fonte' renderizado (semeia a sessão de quebra)."""
    html = authed_client.get("/sources").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Fontes"
    return m.group(1)


def _src(**kw: object) -> SourceStat:
    base: dict[str, object] = {
        "id": RecordID("source", "s1"),
        "canonical": "https://x/feed",
        "kind": "rss",
        "title": None,
        "items": 0,
        "last_collected_at": None,
    }
    base.update(kw)
    return SourceStat(**base)  # type: ignore[arg-type]


def _iso_days_ago(days: int) -> str:
    return str(datetime.now(timezone.utc) - timedelta(days=days))


def test_sources_requires_auth(client: TestClient) -> None:
    """Sem sessão, redireciona pro login."""
    assert client.get("/sources", follow_redirects=False).status_code == 303


def test_sources_empty_state(authed_client: TestClient) -> None:
    """Sem fontes (stub padrão), estado vazio, 200."""
    resp = authed_client.get("/sources")
    assert resp.status_code == 200
    assert "Nenhuma fonte" in resp.text


def test_sources_lists_kind_items_and_recency(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linha mostra nome, kind, itens acumulados e o badge de recência factual (E4)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [
            _src(
                canonical="https://y/@canal",
                kind="youtube",
                title="Canal X",
                items=42,
                last_collected_at=_iso_days_ago(3),
            )
        ],
    )
    html = authed_client.get("/sources").text
    assert "Canal X" in html
    assert "youtube" in html
    assert "42 itens" in html
    assert "há 3d" in html  # recência factual


def test_sources_badge_sem_coleta_when_never_collected(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fonte sem coleta nenhuma → badge 'sem coleta' (E4: 2º estado)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [_src(last_collected_at=None, items=0)],
    )
    assert "sem coleta" in authed_client.get("/sources").text


def test_sources_has_add_form(authed_client: TestClient) -> None:
    """A tela deixou de ser read-only (#105): tem a ação 'Adicionar fonte' com os dois kinds
    e o form POST /sources protegido por CSRF."""
    html = authed_client.get("/sources").text
    assert "Adicionar fonte" in html
    assert 'action="/sources"' in html and 'method="post"' in html
    assert 'name="csrf"' in html
    assert 'value="rss"' in html and 'value="github-repo"' in html


def test_create_rejects_bad_csrf(authed_client: TestClient) -> None:
    """Sem CSRF válido, a escrita é recusada (403) antes de qualquer toque na store."""
    resp = authed_client.post(
        "/sources",
        data={"kind": "rss", "canonical": "https://x/feed", "csrf": "deadbeef"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_create_rejects_invalid_input(authed_client: TestClient) -> None:
    """Entrada inválida (kind fora do conjunto) é barrada na borda pydantic → 400, sem escrita."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={"kind": "bogus", "canonical": "https://x/feed", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_create_rejects_rss_without_scheme(authed_client: TestClient) -> None:
    """RSS sem esquema http(s) → 400 (validação da canonical na borda), sem escrita."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={"kind": "rss", "canonical": "not-a-url", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_create_rejects_rss_without_host(authed_client: TestClient) -> None:
    """RSS com esquema mas SEM host (`https://`) é barrado (validação estrutural) → 400."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={"kind": "rss", "canonical": "https://", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_create_rejects_github_url_with_query(authed_client: TestClient) -> None:
    """github-repo com query/fragment é barrado — `?x=1` não pode virar parte do repo (400)."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={
            "kind": "github-repo",
            "canonical": "https://github.com/owner/repo?x=1",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_create_rejects_non_github_host(authed_client: TestClient) -> None:
    """github-repo com host que não é github.com é barrado — sem reescrever `evil.com/o/r`
    num repo 'válido' (validação estrutural do host via urlparse) → 400."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={"kind": "github-repo", "canonical": "https://evil.com/owner/repo", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_create_does_not_reflect_submitted_input(authed_client: TestClient) -> None:
    """A submissão inválida NÃO é ecoada de volta na tela (nem o campo, nem o notice): o aviso
    é texto estático/`format_validation_error` (só loc+msg, nunca `input`) e o form não
    repopula valores. Prova de não-reflexão — fecha a superfície de XSS refletido na borda."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={"kind": "rss", "canonical": "javascript:alert(1)//pwn", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "alert(1)" not in resp.text  # nem cru nem escapado — simplesmente não reflete


def test_create_without_writer_credential_is_503(authed_client: TestClient) -> None:
    """Fail-fast do molde ADR-0018: sem a credencial kubo_rw (env ausente no teste), a escrita
    é indisponível (503) — o resto da UI (kubo_ro) segue vivo. Entrada VÁLIDA para chegar ao
    connect_rw (a validação já passou)."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources",
        data={"kind": "rss", "canonical": "https://x/feed", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 503


def test_sources_has_client_search_and_view_toggle(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fontes tem busca client-side (filterList + data-search por linha) e view toggle
    de 3 modos (lista/grid2/squares)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [_src(canonical="https://x/feed", kind="rss", title="Feed X", items=3)],
    )
    html = authed_client.get("/sources").text
    assert "filterList(" in html  # busca client-side ligada
    assert "data-search=" in html  # texto buscável por fonte
    assert 'data-view-btn="sources:squares"' in html  # 3º modo (referência)


def test_sources_orders_collected_before_never(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fontes que coletaram vêm antes das que nunca coletaram (eixo de recência)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [
            _src(canonical="never://src", last_collected_at=None),
            _src(canonical="fresh://src", last_collected_at=_iso_days_ago(1)),
        ],
    )
    html = authed_client.get("/sources").text
    assert html.index("fresh://src") < html.index("never://src")


def test_normalize_canonical_still_validates_rss() -> None:
    """rss segue validado estruturalmente: sem esquema http(s) → ValueError (regressão-guard)."""
    from kubo.api.routes.sources import _normalize_canonical

    with pytest.raises(ValueError, match="feed"):
        _normalize_canonical("rss", "sem-esquema")


def test_normalize_canonical_github_normalizes() -> None:
    """github-repo continua indo pela forma de-facto do worker."""
    from kubo.api.routes.sources import _normalize_canonical

    assert _normalize_canonical("github-repo", "owner/repo/") == "https://github.com/owner/repo"


def test_normalize_canonical_passes_through_unknown_kind() -> None:
    """kind SEM normalizador dedicado (ex.: `youtube` legado) NÃO é tratado como rss: a canonical
    passa crua (só trim), senão editar só o título de uma fonte legada falharia sob o regime errado
    (achado do CodeRabbit — a lista mostra `youtube` e a edição não restringe o kind)."""
    from kubo.api.routes.sources import _normalize_canonical

    assert _normalize_canonical("youtube", "  youtube.com/@canal  ") == "youtube.com/@canal"


def _detail(**kw: object) -> SourceDetail:
    base: dict[str, object] = {
        "id": RecordID("source", "s1"),
        "kind": "rss",
        "canonical": "https://x/feed",
        "title": "Feed X",
        "tags": [],
        "enabled": True,
        "archived_at": None,
    }
    base.update(kw)
    return SourceDetail(**base)  # type: ignore[arg-type]


def test_edit_page_prefills_current_values(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A página de edição (#106) pré-preenche title/canonical/tags do Cadastro e mostra o kind
    como READONLY (mudar o kind trocaria o coletor — não é editável). Sem pré-preencher tags,
    salvar apagaria as tags existentes (full-replace)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.get_source",
        lambda db, sid: _detail(title="Feed X", canonical="https://x/feed", tags=["python", "ml"]),
    )
    html = authed_client.get("/sources/s1/edit").text
    assert 'value="Feed X"' in html
    assert 'value="https://x/feed"' in html
    assert "python, ml" in html  # tags pré-preenchidas
    assert "Feed RSS" in html  # kind visível (rótulo humano), read-only
    assert "não editável" in html  # kind não é editável
    assert 'name="csrf"' in html


def test_edit_page_absent_source_redirects_to_list(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET numa fonte que não existe (ou foi apagada) volta para a lista — não 500."""
    monkeypatch.setattr("kubo.api.routes.sources.knowledge.get_source", lambda db, sid: None)
    resp = authed_client.get("/sources/ghost/edit", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/sources"


def test_edit_rejects_bad_csrf(authed_client: TestClient) -> None:
    """Sem CSRF válido, a edição é recusada (403) antes de qualquer toque na store."""
    resp = authed_client.post(
        "/sources/s1/edit",
        data={"title": "X", "tags": "", "canonical": "https://x/feed", "csrf": "deadbeef"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_edit_rejects_too_many_tags(authed_client: TestClient) -> None:
    """Tags além do teto são barradas na borda pydantic (input renderizável tem cap, ADR-0018
    §VI) → 400, sem tocar a store."""
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources/s1/edit",
        data={
            "title": "X",
            "tags": ",".join(f"t{i}" for i in range(50)),
            "canonical": "https://x/feed",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_edit_stale_source_is_409(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST numa fonte que saiu do estado editável (inexistente/arquivada) → 409 (staleness
    semântica, molde ADR-0018), sem abrir a conexão de escrita."""
    monkeypatch.setattr("kubo.api.routes.sources.knowledge.get_source", lambda db, sid: None)
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources/ghost/edit",
        data={"title": "X", "tags": "", "canonical": "https://x/feed", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 409


def test_edit_without_writer_credential_is_503(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-fast do molde ADR-0018: com a fonte editável (pré-check RO passa) mas sem a
    credencial kubo_rw (env ausente no teste), a escrita é indisponível (503)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.get_source",
        lambda db, sid: _detail(kind="rss", canonical="https://x/feed"),
    )
    csrf = _csrf(authed_client)
    resp = authed_client.post(
        "/sources/s1/edit",
        data={"title": "Novo", "tags": "a,b", "canonical": "https://x/feed", "csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 503


def test_list_has_edit_link_per_source(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cada fonte na lista tem um caminho para editar (a tela deixou de ser só-leitura no #106)."""
    monkeypatch.setattr(
        "kubo.api.routes.sources.knowledge.sources_with_stats",
        lambda db: [_src(id=RecordID("source", "abc"), canonical="https://x/feed")],
    )
    html = authed_client.get("/sources").text
    assert "/sources/abc/edit" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
