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
    counterpart_task=RecordID("task", "a1"),
    gate_task=RecordID("task", "g1"),
    gate_state="awaiting_review",
    question="O que dizem sobre memória?",
    content="Relatório com <b>tag</b> hostil e\nquebra de linha.",
    deliverable_kind="report",
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


_PR_BOARD = FlowBoardView(
    id="flow:d1",
    question="add a hello() function",
    template="dev-mini",
    states=["created", "implementing", "review", "done", "rejected", "failed"],
    tasks=[
        FlowTaskCard(id="task:c1", state="review", persona="dev", is_gate=False),
        FlowTaskCard(id="task:h1", state="review", persona="humano", is_gate=True),
    ],
)
_PR_GATE = GateContext(
    flow=RecordID("flow", "d1"),
    counterpart_task=RecordID("task", "c1"),
    gate_task=RecordID("task", "h1"),
    gate_state="review",
    question="add a hello() function",
    content="Implementei com <script>alert(1)</script> na prosa.",
    deliverable_kind="pr",
    sources=[],
    pr_url="https://github.com/owner/kubo-forge/pull/9",
    pr_number=9,
)


def test_board_pr_gate_renders_link_and_plain_summary(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """No gate de PR o GateSheet mostra o link ESTRUTURAL do PR (pr_url) e o resumo do agente em
    TEXTO PLANO (a tag hostil ESCAPADA), sem a seção de fontes (PR não tem consults)."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _PR_BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _PR_GATE)

    resp = authed_client.get("/flows/d1")
    assert resp.status_code == 200
    assert "https://github.com/owner/kubo-forge/pull/9" in resp.text  # link do PR (estrutural)
    assert "&lt;script&gt;" in resp.text  # resumo escapado
    assert "<script>alert(1)</script>" not in resp.text  # NUNCA interpretado
    assert "Fontes consultadas" not in resp.text  # PR não tem fontes


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


_PROMO_BOARD = FlowBoardView(
    id="flow:d1",
    question="add a hello() function",
    template="dev-mini",
    states=["created", "implementing", "review", "done", "promoted", "rejected", "failed"],
    tasks=[
        FlowTaskCard(id="task:c1", state="done", persona="dev", is_gate=False),
        FlowTaskCard(id="task:h2", state="done", persona="humano", is_gate=True),
    ],
)
_PROMO_GATE = GateContext(
    flow=RecordID("flow", "d1"),
    counterpart_task=RecordID("task", "c1"),
    gate_task=RecordID("task", "h2"),
    gate_state="done",
    question="add a hello() function",
    content="Implementei com <script>alert(1)</script> na prosa.",
    deliverable_kind="pr",
    sources=[],
    pr_url="https://github.com/owner/kubo-forge/pull/9",
    pr_number=9,
)


def test_board_promotion_gate_renders_worker_name_form_no_reject(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """O gate de promoção (ADR-0021 §9) mostra o input de worker_name e o botão 'Confirmar
    promoção', SEM form de rejeição (approve-only — não se rejeita um merge)."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _PROMO_BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _PROMO_GATE)

    resp = authed_client.get("/flows/d1")
    assert resp.status_code == 200
    assert "Confirmar promoção" in resp.text
    assert 'name="worker_name"' in resp.text
    assert "/flows/gate/promote" in resp.text
    assert "/flows/gate/reject" not in resp.text


def test_promote_without_csrf_is_403(authed_client: TestClient) -> None:
    """POST de promoção sem CSRF válido → 403 (mesma disciplina de approve/reject)."""
    resp = authed_client.post(
        "/flows/gate/promote",
        data={"task": "task:h2", "csrf": "bogus", "worker_name": "feed"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_promote_without_worker_name_is_400(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """`worker_name` vazio → 400 — nunca chega a validar merge/registry sem o nome."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _PROMO_BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _PROMO_GATE)
    csrf = _csrf_from(authed_client.get("/flows/d1").text)

    resp = authed_client.post(
        "/flows/gate/promote",
        data={"task": "task:h2", "csrf": csrf, "worker_name": "   "},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_promote_failure_reopens_board_with_message(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """`PromotionError` (PR não mesclado / worker fora do registry, E10) REABRE o board com a
    mensagem do próprio erro (422) — o gate segue aberto, at-least-once."""
    from collections.abc import Iterator
    from contextlib import contextmanager

    from kubo.errors import PromotionError

    @contextmanager
    def _fake_rw(cfg: object = None) -> Iterator[object]:
        yield object()

    monkeypatch.setattr("kubo.api.routes.flows.client.connect_rw", _fake_rw)
    monkeypatch.setattr("kubo.api.routes.flows.client.connect", _fake_rw)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _PROMO_GATE)
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _PROMO_BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.flow_of_task", lambda db, t: RecordID("flow", "d1"))

    def _boom(db: object, *, gate_task: object, worker_name: str) -> None:
        raise PromotionError("worker 'feed2' não está na imagem viva; rode ./scripts/deploy.sh")

    monkeypatch.setattr("kubo.api.routes.flows.promote_gate", _boom)
    csrf = _csrf_from(authed_client.get("/flows/d1").text)

    resp = authed_client.post(
        "/flows/gate/promote",
        data={"task": "task:h2", "csrf": csrf, "worker_name": "feed2"},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "deploy.sh" in resp.text


def test_promote_forge_failure_reopens_board_with_502(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """Achado CodeRabbit: `ForgeError` (falha ao consultar o GitHub, ex. rede/HTTP) do
    `promote_gate` REABRE o board com aviso (502), nunca um 500 não tratado — o gate segue
    aberto (at-least-once), espelhando o tratamento de `ForgeError` no reject."""
    from collections.abc import Iterator
    from contextlib import contextmanager

    from kubo.errors import ForgeError

    @contextmanager
    def _fake_rw(cfg: object = None) -> Iterator[object]:
        yield object()

    monkeypatch.setattr("kubo.api.routes.flows.client.connect_rw", _fake_rw)
    monkeypatch.setattr("kubo.api.routes.flows.client.connect", _fake_rw)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _PROMO_GATE)
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _PROMO_BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.flow_of_task", lambda db, t: RecordID("flow", "d1"))

    def _boom(db: object, *, gate_task: object, worker_name: str) -> None:
        raise ForgeError("GitHub respondeu HTTP 500")

    monkeypatch.setattr("kubo.api.routes.flows.promote_gate", _boom)
    csrf = _csrf_from(authed_client.get("/flows/d1").text)

    resp = authed_client.post(
        "/flows/gate/promote",
        data={"task": "task:h2", "csrf": csrf, "worker_name": "feed"},
        follow_redirects=False,
    )
    assert resp.status_code == 502
    assert "GitHub" in resp.text


def test_reject_pr_close_failure_reopens_board(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """Rejeição do dev: se o close do PR na API falhar (ForgeError), o board REABRE com aviso (502,
    at-least-once — o gate segue aberto), nunca 500. Espelha a falha de envio do analysis."""
    from collections.abc import Iterator
    from contextlib import contextmanager

    from kubo.errors import ForgeError

    @contextmanager
    def _fake_rw(cfg: object = None) -> Iterator[object]:
        yield object()

    monkeypatch.setattr("kubo.api.routes.flows.client.connect_rw", _fake_rw)
    monkeypatch.setattr("kubo.api.routes.flows.client.connect", _fake_rw)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _PR_GATE)
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _PR_BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.flow_of_task", lambda db, t: RecordID("flow", "d1"))

    def _boom(db: object, *, gate_task: object, reason: str) -> None:
        raise ForgeError("GitHub respondeu HTTP 500")

    monkeypatch.setattr("kubo.api.routes.flows.reject_gate", _boom)
    csrf = _csrf_from(authed_client.get("/flows/d1").text)

    resp = authed_client.post(
        "/flows/gate/reject",
        data={"task": "task:h1", "csrf": csrf, "reason": "escopo errado"},
        follow_redirects=False,
    )
    assert resp.status_code == 502
    assert "PR" in resp.text
