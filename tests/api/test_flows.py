"""Rotas de Fluxos — unit (ADR-0018 §V/§VI): rendering, CSRF e motivo obrigatório.

Store stubada (fake connect da conftest + funções monkeypatchadas): estes testes fixam o
COMPORTAMENTO da casca — a lista renderiza, o board mostra o GateSheet com o relatório em
TEXTO PLANO (autoescape, nunca markdown→HTML), o CSRF barra POST sem token, a rejeição sem
motivo dá 400. O caminho de ESCRITA real (kubo_rw + read-back) é o teste de integração à
parte (o footgun do no-op silencioso).
"""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.flows import FlowBoardView, FlowListRow, FlowTaskCard, GateContext, GateSource

_BOARD = FlowBoardView(
    id="flow:f1",
    question="O que dizem sobre memória?",
    template="analysis-review",
    states=["created", "analyzing", "awaiting_review", "delivered", "rejected", "failed"],
    tasks=[
        FlowTaskCard(id="task:a1", state="awaiting_review", persona="analista", is_gate=False),
        FlowTaskCard(id="task:g1", state="awaiting_review", persona="humano", is_gate=True),
    ],
)
_GATE = GateContext(
    flow=RecordID("flow", "f1"),
    analyst_task=RecordID("task", "a1"),
    gate_task=RecordID("task", "g1"),
    question="O que dizem sobre memória?",
    content="Relatório com <b>tag</b> hostil e\nquebra de linha.",
    sources=[GateSource(id="distilled:d1", title="Rust ownership")],
)


def _csrf_from(html: str) -> str:
    """Extrai o token CSRF do hidden input renderizado no board."""
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf hidden input ausente no board"
    return m.group(1)


def test_list_page_renders(monkeypatch: pytest.MonkeyPatch, authed_client: TestClient) -> None:
    """A lista renderiza uma linha por flow com nome, template e status."""
    row = FlowListRow(
        id="flow:f1",
        question="O que dizem sobre memória?",
        template="analysis-review",
        status="aguardando",
        gate_open=True,
        cast=["analista", "humano"],
        tasks_open=2,
        created_at="2026-07-14T12:00:00Z",
    )
    monkeypatch.setattr("kubo.api.routes.flows.list_flows", lambda db, **kw: [row])
    monkeypatch.setattr("kubo.api.routes.flows.count_flows", lambda db: 1)

    resp = authed_client.get("/flows")
    assert resp.status_code == 200
    assert "O que dizem sobre memória?" in resp.text
    assert "analysis-review" in resp.text


def test_board_renders_gatesheet_with_plain_text(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """O board mostra o GateSheet; o relatório é TEXTO PLANO — a tag hostil vem ESCAPADA
    (`&lt;b&gt;`), nunca como `<b>` (ADR-0016 §II: markdown→HTML proibido)."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _GATE)

    resp = authed_client.get("/flows/f1")
    assert resp.status_code == 200
    assert "Decisão de gate" in resp.text
    assert "&lt;b&gt;tag&lt;/b&gt;" in resp.text  # escapado
    assert "<b>tag</b>" not in resp.text  # NUNCA interpretado
    assert 'name="csrf"' in resp.text  # token presente para os POSTs


def test_approve_without_csrf_is_403(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """POST de escrita sem token CSRF válido → 403 (sessão sem token = falha fechada)."""
    resp = authed_client.post(
        "/flows/gate/approve", data={"task": "task:g1", "csrf": "bogus"}, follow_redirects=False
    )
    assert resp.status_code == 403


def test_reject_without_reason_is_400(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """Rejeição SEM motivo → 400 (motivo obrigatório, nunca cortável). Precisa do CSRF válido
    para chegar à validação do motivo — obtido do board renderizado."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _GATE)
    csrf = _csrf_from(authed_client.get("/flows/f1").text)

    resp = authed_client.post(
        "/flows/gate/reject",
        data={"task": "task:g1", "csrf": csrf, "reason": "   "},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_write_routes_require_login(client: TestClient) -> None:
    """Sem sessão, os POSTs de escrita caem no guard de login (redirect 303 a /login)."""
    resp = client.post("/flows/gate/approve", data={"task": "task:g1"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
