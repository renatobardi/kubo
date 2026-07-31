"""Estudos com sessão fora do workspace (KUBO-141): 403 legível, nunca 500.

O escopo estrito do módulo é deliberado (ADR-0043): a store chama `assert_membership`
mesmo para superadmin. O que era bug é a TRADUÇÃO — `MembershipRequiredError` subia
sem ninguém capturar e virava 500 na cara do dono. Aqui prova-se o 403 em uma leitura
de lista, um detalhe e uma escrita; o ponto de tradução é único, então as demais rotas
herdam o mesmo caminho.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.errors import MembershipRequiredError
from kubo.store.study import Material

_MATERIAL_ID = RecordID("material", "abc123")
_NOT_A_MEMBER = "não pertence a este workspace"


def _material() -> Material:
    return Material(
        id=_MATERIAL_ID,
        tenant_id=RecordID("tenant", "breakglass"),
        user_id=RecordID("user", "breakglass-owner"),
        title="Manual de Kubo",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/materials/t/u/abc123.epub",
        size_bytes=1024,
        chapter_count=2,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def _refuse(*args: object, **kwargs: object) -> object:
    """Stub da store no papel de `assert_membership`: recusa por falta de membership."""
    raise MembershipRequiredError("user does not belong to tenant")


@pytest.fixture(autouse=True)
def stub_study_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Leituras vazias por padrão e volume de materiais no tmp do teste."""
    monkeypatch.setenv("KUBO_MATERIALS_DIR", str(tmp_path))
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_material", lambda db, **kw: None)


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form de envio — ANTES de qualquer stub que recuse a leitura."""
    html = authed_client.get("/study/materials").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


def test_list_refuses_with_403_when_not_a_member(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leitura de lista: recusa da store vira 403 explicando o caso, não 500."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", _refuse)

    resp = authed_client.get("/study/materials")

    assert resp.status_code == 403
    assert _NOT_A_MEMBER in resp.text


def test_detail_refuses_with_403_when_not_a_member(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detalhe: a recusa vem da leitura do material, e não pode virar 500."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_material", _refuse)

    resp = authed_client.get(f"/study/materials/{_MATERIAL_ID.id}")

    assert resp.status_code == 403
    assert _NOT_A_MEMBER in resp.text


def test_write_refuses_with_403_when_not_a_member(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escrita (criar tema): a recusa da store também é 403, não 500."""
    csrf = _csrf(authed_client)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_topic", _refuse)

    resp = authed_client.post(
        f"/study/materials/{_MATERIAL_ID.id}/topic",
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert _NOT_A_MEMBER in resp.text
