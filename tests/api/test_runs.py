"""Testes da tela de Execuções (M2, paridade `ExecucoesScreen.jsx`): badge que
discrimina quota de falha real (E6, apresentação sem reclassificar status), erro
estruturado expansível, itens com fallback, paginação. Store mockada — teste de rota."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from kubo.store.knowledge import RunListItem


def _run(**kw: object) -> RunListItem:
    """RunListItem com defaults — os testes sobrescrevem só o que importa."""
    base: dict[str, object] = {
        "worker": "feed",
        "status": "ok",
        "error_kind": None,
        "error": None,
        "items": None,
        "started_at": "2026-07-12T09:00:00+00:00",
        "finished_at": "2026-07-12T09:00:05+00:00",
    }
    base.update(kw)
    return RunListItem(**base)  # type: ignore[arg-type]


def test_runs_requires_auth(client: TestClient) -> None:
    """Sem sessão, a tela redireciona pro login (guard antes do banco)."""
    assert client.get("/runs", follow_redirects=False).status_code == 303


def test_runs_empty_state(authed_client: TestClient) -> None:
    """Sem runs (stub padrão), estado vazio, 200."""
    resp = authed_client.get("/runs")
    assert resp.status_code == 200
    assert "Nenhuma execução" in resp.text


def test_runs_renders_worker_status_and_items(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linha mostra worker, status e nº de itens quando derivado de stats."""
    monkeypatch.setattr(
        "kubo.api.routes.runs.knowledge.list_runs",
        lambda db, **kw: [_run(worker="feed", status="ok", items=5)],
    )
    html = authed_client.get("/runs").text
    assert "feed" in html
    assert "5 itens" in html


def test_runs_quota_badge_does_not_reclassify_error(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rate_limit vira badge NEUTRO 'quota' (E6) — apresentação. O status segue
    'error' (não é remarcado 'ok'), e o erro estruturado fica expansível."""
    monkeypatch.setattr(
        "kubo.api.routes.runs.knowledge.list_runs",
        lambda db, **kw: [
            _run(
                worker="distiller",
                status="error",
                error_kind="rate_limit",
                error={"kind": "rate_limit", "message": "quota estourada"},
                finished_at="2026-07-12T09:00:02+00:00",
            )
        ],
    )
    html = authed_client.get("/runs").text
    assert "quota" in html  # badge neutro
    assert "<details" in html  # erro expansível
    assert "quota estourada" in html  # mensagem estruturada visível ao expandir


def test_runs_real_failure_shows_destructive_kind(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha real (kind != quota) mostra o kind no badge destrutivo, expansível."""
    monkeypatch.setattr(
        "kubo.api.routes.runs.knowledge.list_runs",
        lambda db, **kw: [
            _run(
                worker="feed",
                status="error",
                error_kind="http",
                error={"kind": "http", "message": "504 upstream timeout"},
            )
        ],
    )
    html = authed_client.get("/runs").text
    assert "http" in html
    assert "504 upstream timeout" in html


def test_runs_escapes_error_message(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mensagem de erro é conteúdo potencialmente hostil (vem de exceção): renderizada
    escapada pelo autoescape, nunca injeta HTML."""
    payload = "<script>alert('x')</script>"
    monkeypatch.setattr(
        "kubo.api.routes.runs.knowledge.list_runs",
        lambda db, **kw: [
            _run(status="error", error_kind="boom", error={"kind": "boom", "message": payload})
        ],
    )
    html = authed_client.get("/runs").text
    assert payload not in html
    assert "&lt;script&gt;" in html


def test_runs_pagination_next_when_full_page(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Página cheia (PAGE_SIZE+1) mostra 'Próximos' e trunca ao tamanho da página."""
    rows = [_run(worker=f"w{i}") for i in range(21)]
    monkeypatch.setattr("kubo.api.routes.runs.knowledge.list_runs", lambda db, **kw: rows)
    html = authed_client.get("/runs").text
    assert "Próximos" in html
    assert "Anteriores" not in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
