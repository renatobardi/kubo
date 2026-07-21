"""Escrita de Destinos pela rota REAL (KUBO-43, molde ADR-0018).

Integração: SurrealDB real + usuário kubo_rw EDITOR + app FastAPI real.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.api.app import create_app
from kubo.store import client, migrations
from kubo.store.client import connect as _real_connect
from tests.api.conftest import UI_PASSWORD

pytestmark = pytest.mark.integration

_DB = "test_destinations_write"
_RW_PASS = secrets.token_urlsafe(24)


@pytest.fixture
def app_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """App real apontado a um db efêmero com kubo_rw."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        root.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        try:
            yield create_app()
        finally:
            root.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _login_csrf(app: Any) -> tuple[TestClient, str]:
    """Autentica e devolve (client, csrf) lido do form de destinos."""
    tc = TestClient(app)
    login = tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', tc.get("/destinations").text)
    assert m, "csrf ausente no form de Destinos"
    return tc, m.group(1)


def _destinations(db_name: str = _DB) -> list[dict[str, Any]]:
    """Lê COMO ROOT os destinos gravados (incluindo id)."""
    with _real_connect(replace(client.config(), database=db_name)) as root:
        return root.query("SELECT id, name, kind, channel, address, enabled FROM destination;")


def test_create_via_real_route_lands_in_the_graph(app_db: Any) -> None:
    """Cadastrar pela rota real grava o destino normalizado no banco."""
    tc, csrf = _login_csrf(app_db)
    resp = tc.post(
        "/destinations",
        data={
            "name": "Renato",
            "kind": "pessoa",
            "channel": "telegram",
            "address": "  123456  ",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    rows = _destinations()
    assert len(rows) == 1
    assert rows[0]["address"] == "123456"
    assert rows[0]["enabled"] is True


def test_create_telegram_normalizes_non_digits(app_db: Any) -> None:
    """Telegram: endereço é normalizado (só dígitos, hífen opcional no início)."""
    tc, csrf = _login_csrf(app_db)
    tc.post(
        "/destinations",
        data={
            "name": "Grupo",
            "kind": "pessoa",
            "channel": "telegram",
            "address": "  -100 1234-5678  ",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    rows = _destinations()
    assert rows[0]["address"] == "-10012345678"


def test_create_email_is_allowed_and_normalizes(app_db: Any) -> None:
    """Criação com canal e-mail é permitida e o endereço é normalizado para lowercase."""
    tc, csrf = _login_csrf(app_db)
    resp = tc.post(
        "/destinations",
        data={
            "name": "Email",
            "kind": "pessoa",
            "channel": "email",
            "address": "Owner@Example.COM",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    rows = _destinations()
    assert len(rows) == 1
    assert rows[0]["channel"] == "email"
    assert rows[0]["address"] == "owner@example.com"


def test_duplicate_is_soft_warning_without_second_record(app_db: Any) -> None:
    """Duplicata reabre a tela com aviso SOFT (409) e NÃO grava segundo record."""
    tc, csrf = _login_csrf(app_db)
    data = {
        "name": "A",
        "kind": "pessoa",
        "channel": "telegram",
        "address": "111",
        "csrf": csrf,
    }
    first = tc.post("/destinations", data=data, follow_redirects=False)
    assert first.status_code == 303
    again = tc.post("/destinations", data=data, follow_redirects=False)
    assert again.status_code == 409
    assert "already registered" in again.text.lower()
    assert len(_destinations()) == 1


def test_csrf_is_required_for_create(app_db: Any) -> None:
    """POST sem CSRF válido é 403."""
    tc = TestClient(app_db)
    tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    resp = tc.post(
        "/destinations",
        data={"name": "X", "kind": "pessoa", "channel": "telegram", "address": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def _create_destination_via_route(tc: TestClient, csrf: str, **data: str) -> str:
    """Cadastra pela rota e devolve o key do id."""
    resp = tc.post("/destinations", data={**data, "csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    rows = _destinations()
    by_name = [r for r in rows if r["name"] == data["name"]]
    assert len(by_name) == 1
    return by_name[0]["id"].id


def _edit_csrf(tc: TestClient, did: str) -> str:
    """Lê o CSRF do form de edição de um destino."""
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', tc.get(f"/destinations/{did}/edit").text)
    assert m, "csrf ausente no form de edição"
    return m.group(1)


def test_edit_via_real_route_updates_and_normalizes(app_db: Any) -> None:
    """Editar pela rota real atualiza nome/endereço no MESMO record."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    resp = tc.post(
        f"/destinations/{key}/edit",
        data={"name": "A2", "address": " 222 ", "csrf": _edit_csrf(tc, key)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    rows = _destinations()
    assert len(rows) == 1
    assert rows[0]["name"] == "A2"
    assert rows[0]["address"] == "222"


def test_edit_to_duplicate_is_soft_409(app_db: Any) -> None:
    """Editar o endereço para o de OUTRO destino reabre com aviso SOFT."""
    tc, csrf = _login_csrf(app_db)
    a = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    _create_destination_via_route(
        tc, csrf, name="B", kind="pessoa", channel="telegram", address="222"
    )
    resp = tc.post(
        f"/destinations/{a}/edit",
        data={"name": "A", "address": "222", "csrf": _edit_csrf(tc, a)},
        follow_redirects=False,
    )
    assert resp.status_code == 409
    by_name = {r["name"]: r["address"] for r in _destinations()}
    assert by_name == {"A": "111", "B": "222"}


def test_edit_archived_is_stale_via_real_route(app_db: Any) -> None:
    """Destino arquivado saiu do estado editável: POST reabre com staleness (409)."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    with _real_connect(replace(client.config(), database=_DB)) as root:
        root.query(
            "UPDATE $d SET enabled = false, archived_at = time::now();",
            {"d": RecordID("destination", key)},
        )
    resp = tc.post(
        f"/destinations/{key}/edit",
        data={"name": "A2", "address": "222", "csrf": _edit_csrf(tc, key)},
        follow_redirects=False,
    )
    assert resp.status_code == 409


# ── Ciclo de vida pela rota REAL ─────────────────────────────────────────────


def _state(key: str) -> dict[str, Any]:
    """Lê enabled/archived_at de um destino."""
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query(
            "SELECT enabled, archived_at FROM $d;",
            {"d": RecordID("destination", key)},
        )
    return rows[0]


def test_disable_then_enable_via_real_route(app_db: Any) -> None:
    """Pausar/retomar pela rota real altera `enabled` sem tocar `archived_at`."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    resp = tc.post(f"/destinations/{key}/disable", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    st = _state(key)
    assert st["enabled"] is False and st.get("archived_at") is None

    resp = tc.post(f"/destinations/{key}/enable", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    st = _state(key)
    assert st["enabled"] is True and st.get("archived_at") is None


def test_archive_then_restore_via_real_route(app_db: Any) -> None:
    """Arquivar/reativar pela rota real alterna os dois campos atômicos."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    resp = tc.post(f"/destinations/{key}/archive", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    st = _state(key)
    assert st["enabled"] is False and st.get("archived_at") is not None

    resp = tc.post(f"/destinations/{key}/restore", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    st = _state(key)
    assert st["enabled"] is True and st.get("archived_at") is None


def test_delete_zero_dispatches_via_real_route(app_db: Any) -> None:
    """Apagar de vez um destino SEM dispatches remove o record (303)."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    resp = tc.post(f"/destinations/{key}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 303
    assert len(_destinations()) == 0


def test_delete_with_dispatches_is_blocked_via_real_route(app_db: Any) -> None:
    """Apagar um destino COM dispatches é impedido (409); a tela orienta a arquivar."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    with _real_connect(replace(client.config(), database=_DB)) as root:
        root.query(
            "CREATE dispatch SET destination = $d, channel = 'telegram', status = 'ok', "
            "watermark = time::now(), item_count = 0, items = [];",
            {"d": RecordID("destination", key)},
        )
    resp = tc.post(f"/destinations/{key}/delete", data={"csrf": csrf}, follow_redirects=False)
    assert resp.status_code == 409
    assert "arquive" in resp.text.lower()
    assert len(_destinations()) == 1


def test_delete_page_get_renders_confirmation_via_real_route(app_db: Any) -> None:
    """A tela de confirmação de apagar oferece o POST destrutivo com CSRF."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    html = tc.get(f"/destinations/{key}/delete").text
    assert f'action="/destinations/{key}/delete"' in html
    assert "Apagar de vez" in html


def _last_digest_dispatch(key: str) -> dict[str, Any] | None:
    """Último dispatch de digest do destino, ou None."""
    with _real_connect(replace(client.config(), database=_DB)) as root:
        rows = root.query(
            "SELECT * FROM dispatch WHERE destination = $d AND artifact = 'digest' "
            "ORDER BY watermark DESC LIMIT 1;",
            {"d": RecordID("destination", key)},
        )
    return rows[0] if rows else None


def test_enable_with_recent_resets_watermark(app_db: Any) -> None:
    """Retomar com mode=recente grava dispatch zero-item com watermark=now."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    tc.post(f"/destinations/{key}/disable", data={"csrf": csrf}, follow_redirects=False)

    resp = tc.post(
        f"/destinations/{key}/enable",
        data={"csrf": csrf, "mode": "recente"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    dispatch = _last_digest_dispatch(key)
    assert dispatch is not None
    assert dispatch["status"] == "ok"
    assert dispatch["item_count"] == 0
    assert dispatch["watermark"] is not None


def test_enable_default_backlog_does_not_reset(app_db: Any) -> None:
    """Retomar sem mode não toca o watermark."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    tc.post(f"/destinations/{key}/disable", data={"csrf": csrf}, follow_redirects=False)

    resp = tc.post(
        f"/destinations/{key}/enable",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _last_digest_dispatch(key) is None


def test_restore_with_recent_resets_watermark(app_db: Any) -> None:
    """Restaurar arquivado com mode=recente grava dispatch zero-item."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    tc.post(f"/destinations/{key}/archive", data={"csrf": csrf}, follow_redirects=False)

    resp = tc.post(
        f"/destinations/{key}/restore",
        data={"csrf": csrf, "mode": "recente"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    dispatch = _last_digest_dispatch(key)
    assert dispatch is not None
    assert dispatch["status"] == "ok"
    assert dispatch["item_count"] == 0


def test_invalid_reactivation_mode_is_treated_as_backlog(app_db: Any) -> None:
    """Mode desconhecido cai no default backlog, sem reset."""
    tc, csrf = _login_csrf(app_db)
    key = _create_destination_via_route(
        tc, csrf, name="A", kind="pessoa", channel="telegram", address="111"
    )
    tc.post(f"/destinations/{key}/disable", data={"csrf": csrf}, follow_redirects=False)

    resp = tc.post(
        f"/destinations/{key}/enable",
        data={"csrf": csrf, "mode": "nonsense"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _last_digest_dispatch(key) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
