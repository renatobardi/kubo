"""Estudos com sessão fora do workspace (KUBO-141): 403 legível, nunca 500.

O escopo estrito do módulo é deliberado (ADR-0047): a store chama `assert_membership`
mesmo para superadmin. O que era bug é a TRADUÇÃO — `MembershipRequiredError` subia
sem ninguém capturar e virava 500 na cara do dono. Aqui prova-se o 403 em uma leitura
de lista, um detalhe e uma escrita; o ponto de tradução é único, então as demais rotas
herdam o mesmo caminho.
"""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from kubo.errors import MembershipRequiredError

# Corpo EXATO esperado: a rota devolve PlainTextResponse, então igualdade pega tanto
# mudança de texto quanto conteúdo extra grudado na recusa.
_NOT_A_MEMBER = "Estudos é pessoal: sua conta não pertence a este workspace."


def _refuse(*args: object, **kwargs: object) -> object:
    """Stub da store no papel de `assert_membership`: recusa por falta de membership."""
    raise MembershipRequiredError("user does not belong to tenant")


@pytest.fixture(autouse=True)
def stub_study_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leituras vazias por padrão."""
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)


def _csrf(authed_client: TestClient) -> str:
    """Lê o token CSRF do form da lista de Temas — ANTES de qualquer stub que recuse."""
    html = authed_client.get("/study/topics").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


def test_list_refuses_with_403_when_not_a_member(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leitura de lista: recusa da store vira 403 explicando o caso, não 500."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_topics", _refuse)

    resp = authed_client.get("/study/topics")

    assert resp.status_code == 403
    assert resp.text == _NOT_A_MEMBER


def test_detail_refuses_with_403_when_not_a_member(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detalhe: a recusa vem da leitura do tema, e não pode virar 500."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", _refuse)

    resp = authed_client.get("/study/topics/abc123")

    assert resp.status_code == 403
    assert resp.text == _NOT_A_MEMBER


def test_write_refuses_with_403_when_not_a_member(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escrita (criar tema vazio): a recusa da store também é 403, não 500."""
    csrf = _csrf(authed_client)
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_topic", _refuse)

    resp = authed_client.post(
        "/study/topics",
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert resp.text == _NOT_A_MEMBER
