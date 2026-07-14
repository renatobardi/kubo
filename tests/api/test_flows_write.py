"""O TESTE MAIS IMPORTANTE DA SESSÃO (ADR-0018 §I / plano 0015): o footgun do no-op
silencioso. Aprovar/rejeitar pela ROTA REAL (com a credencial kubo_rw de verdade) e LER DE
VOLTA o estado + a decisão. Se um handler usasse por bug a conexão kubo_ro, a escrita falharia
EM SILÊNCIO e o gate 'aprovaria' sem transicionar — este teste pega isso lendo o grafo.

Integração: SurrealDB real + usuário kubo_rw EDITOR efêmero + app FastAPI real. O Telegram é
falso (monkeypatch de send_telegram). A conexão real é RESTAURADA (a conftest a stuba por
default para as rotas de leitura).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

import pytest
from starlette.testclient import TestClient

from kubo.api.app import create_app
from kubo.distribution.destinations import ResolvedDestination
from kubo.runtime.flow_runner import run_flow
from kubo.store import client, knowledge, migrations
from kubo.store.client import connect as _real_connect
from kubo.store.knowledge import Chunk
from kubo.workers.analyst import ReportOutput
from tests.api.conftest import UI_PASSWORD

pytestmark = pytest.mark.integration

_DB = "test_flows_write"
_RW_PASS = "editor-ephemeral-write-test-pw"  # pragma: allowlist secret  # efêmero, descartado
_DEST = ResolvedDestination(
    id="owner-telegram", name="Renato", kind="pessoa", channel="telegram", address="chat-1"
)


class _FakeExecutor:
    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        return ReportOutput(report="Análise sintetizada para o gate.")


class _FakeEmbedder:
    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]


def _seed_distilled(db: Any, title: str) -> None:
    src = knowledge.upsert_source(db, kind="rss", canonical=f"src::{title}")
    item = knowledge.upsert_item(
        db, source=src, external_id=f"e::{title}", content="x", title=title
    )
    chunk = Chunk(text="s", seq=0, embedding=[0.1] * 768, model="m", dim=768, task_type="X")
    knowledge.insert_distilled(db, item=item, summary="s", chunks=[chunk])


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Any, Any, list[Any]]]:
    """App real apontado a um db efêmero com kubo_rw + um gate aberto. Devolve
    (app, flow_key, gate_task, sent) — `sent` captura os envios (Telegram falso)."""
    monkeypatch.setenv("SURREAL_DB", _DB)
    monkeypatch.setenv("KUBO_RW_SURREAL_PASS", _RW_PASS)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")  # pragma: allowlist secret
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setenv("KUBO_BASE_URL", "https://kubo.example")
    # Restaura a conexão REAL (a conftest stuba client.connect por default para as leituras).
    monkeypatch.setattr("kubo.store.client.connect", _real_connect)
    sent: list[Any] = []
    monkeypatch.setattr("kubo.runtime.flow_runner.send_telegram", lambda **kw: sent.append(kw))

    root_cfg = replace(client.config(), database=_DB)
    with _real_connect(root_cfg) as root:
        root.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        root.use(root_cfg.namespace, root_cfg.database)
        migrations.apply_migrations(root)
        root.query(f"DEFINE USER OVERWRITE kubo_rw ON ROOT PASSWORD '{_RW_PASS}' ROLES EDITOR;")
        _seed_distilled(root, "Rust")
        result = run_flow(
            root,
            template_name="analysis-review",
            question="o que dizem sobre memória?",
            embedder=_FakeEmbedder(),
            destination=_DEST,
            base_url="https://kubo.example",
            executor=_FakeExecutor(),
            senders={"telegram": lambda **kw: None},  # notificação do gate: no-op no seed
        )
        flow_key = str(result.flow).partition(":")[2]
        try:
            yield create_app(), flow_key, result.gate_task, sent
        finally:
            root.query("REMOVE USER IF EXISTS kubo_rw ON ROOT;")
            root.query(f"REMOVE DATABASE IF EXISTS {_DB};")


def _login_and_csrf(app: Any, flow_key: str) -> tuple[TestClient, str]:
    """Autentica e devolve (client, csrf) lido do board renderizado."""
    import re

    tc = TestClient(app)
    login = tc.post("/login", data={"password": UI_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    html = tc.get(f"/flows/{flow_key}").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no board"
    return tc, m.group(1)


def _read_state(gate_task: Any) -> dict[str, Any]:
    """Lê COMO ROOT o estado + decisão da task do gate (a prova do read-back)."""
    with _real_connect(replace(client.config(), database=_DB)) as root:
        return root.query("SELECT state, decision, reason FROM $t;", {"t": gate_task})[0]


def test_approve_via_real_route_lands_in_the_graph(
    gated: tuple[Any, Any, Any, list[Any]],
) -> None:
    """Footgun do no-op silencioso: aprovar pela rota real ENVIA (Telegram falso) e grava
    `delivered` + decisão no grafo. Read-back como root prova que a escrita caiu de verdade —
    se a rota usasse kubo_ro por bug, o estado seguiria awaiting_review e o teste quebraria."""
    app, flow_key, gate_task, sent = gated
    tc, csrf = _login_and_csrf(app, flow_key)

    resp = tc.post(
        "/flows/gate/approve",
        data={"task": str(gate_task), "csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert len(sent) == 1  # o relatório foi enviado NA aprovação (mecânico)
    row = _read_state(gate_task)
    assert row["state"] == "delivered"
    assert row["decision"] == "approved"


def test_reject_via_real_route_archives_with_reason(
    gated: tuple[Any, Any, Any, list[Any]],
) -> None:
    """Rejeitar pela rota real grava `rejected` + o motivo obrigatório no grafo (read-back)."""
    app, flow_key, gate_task, _sent = gated
    tc, csrf = _login_and_csrf(app, flow_key)

    resp = tc.post(
        "/flows/gate/reject",
        data={"task": str(gate_task), "csrf": csrf, "reason": "fontes fracas"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    row = _read_state(gate_task)
    assert row["state"] == "rejected"
    assert row["reason"] == "fontes fracas"


def test_double_approve_is_stale_409(gated: tuple[Any, Any, Any, list[Any]]) -> None:
    """Guarda de staleness: aprovar de novo um gate já decidido → 409 (duplo-clique/duas abas),
    sem segunda escrita. O estado permanece delivered."""
    app, flow_key, gate_task, _sent = gated
    tc, csrf = _login_and_csrf(app, flow_key)
    first = tc.post(
        "/flows/gate/approve", data={"task": str(gate_task), "csrf": csrf}, follow_redirects=False
    )
    assert first.status_code == 303

    again = tc.post(
        "/flows/gate/approve", data={"task": str(gate_task), "csrf": csrf}, follow_redirects=False
    )
    assert again.status_code == 409
    assert _read_state(gate_task)["state"] == "delivered"
