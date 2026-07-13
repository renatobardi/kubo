"""Vertical do flow `analysis` (integração, SurrealDB real — ADR-0016).

Prova o encanamento inteiro com LLM/embedder/Telegram FALSOS mas store e runner REAIS:
`run_flow` → instantiate_flow (snapshot) → transição → run_worker(AnalystWorker) → _persist
(ReportPayload via FlowCtx → insert_deliverable: produces + consults) → dispatch(artifact=
report) → transição delivered. E prova que o dispatch de report NÃO cria watermark de digest
(fix E1) e que uma falha de envio leva o task a `failed` com o deliverable ainda no grafo.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

import pytest

from kubo.distribution.destinations import ResolvedDestination
from kubo.runtime.flow_runner import run_flow
from kubo.store import client, knowledge, migrations
from kubo.store.knowledge import Chunk
from kubo.workers.analyst import ReportOutput

pytestmark = pytest.mark.integration

_DB = "test_flow_vertical"
_DEST = ResolvedDestination(
    id="owner-telegram", name="Renato", kind="pessoa", channel="telegram", address="chat-1"
)
_BASE = "https://kubo.example"


@pytest.fixture(autouse=True)
def _telegram_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolução EAGER de integrações (_build_context) exige o token no env — o sender é
    falso e o ignora, mas sem o env o run falharia antes do worker rodar."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DB};")


class _FakeExecutor:
    """Devolve sempre o mesmo ReportOutput; registra se foi chamado."""

    def __init__(self, report: str = "Análise sintetizada.") -> None:
        self._report = report
        self.calls = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        self.calls += 1
        return ReportOutput(report=self._report)


class _FakeEmbedder:
    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]


class _FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self._fail:
            from kubo.errors import SenderError

            raise SenderError("Telegram HTTP 400")


def _seed(db: Any, title: str, summary: str) -> Any:
    """Semeia um distilled buscável: source+item (título) + um chunk embeddado em [0.1]*768,
    o mesmo vetor que o embedder falso devolve para a pergunta (KNN acha)."""
    src = knowledge.upsert_source(db, kind="rss", canonical=f"src::{title}")
    item = knowledge.upsert_item(
        db, source=src, external_id=f"ext::{title}", content="x", title=title
    )
    chunk = Chunk(
        text=summary,
        seq=0,
        embedding=[0.1] * 768,
        model="m",
        dim=768,
        task_type="SEMANTIC_SIMILARITY",
    )
    return knowledge.insert_distilled(db, item=item, summary=summary, chunks=[chunk])


def test_flow_delivers_report_with_full_provenance(db: Any) -> None:
    """Caminho feliz: o relatório vira deliverable com proveniência completa e o task
    termina em delivered; o dispatch de report NÃO cria watermark de digest (E1)."""
    d1 = _seed(db, "Rust", "resumo sobre Rust")
    d2 = _seed(db, "GC", "resumo sobre GC")
    sender = _FakeSender()
    executor = _FakeExecutor()

    result = run_flow(
        db,
        template_name="analysis",
        question="o que dizem sobre memória?",
        embedder=_FakeEmbedder(),
        destination=_DEST,
        base_url=_BASE,
        executor=executor,
        senders={"telegram": sender},
    )

    assert result.state == "delivered"
    # task transicionou até delivered no grafo
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "delivered"
    # flow com snapshot congelado + question
    flow = db.query("SELECT template_name, snapshot, question FROM $f;", {"f": result.flow})[0]
    assert flow["template_name"] == "analysis"
    assert flow["snapshot"]["board"]["states"] == ["created", "analyzing", "delivered", "failed"]
    # personas materializadas (analista + humano)
    personas = db.query("SELECT VALUE catalog_name FROM persona;")
    assert set(personas) == {"analista", "humano"}
    # deliverable + produces + consults
    delivered = db.query("SELECT VALUE ->produces->deliverable FROM $f;", {"f": result.flow})[0]
    assert len(delivered) == 1
    body = db.query("SELECT kind, content FROM $d;", {"d": delivered[0]})[0]
    assert body["kind"] == "report"
    assert "## Fontes" in body["content"]
    consulted = db.query("SELECT VALUE ->consults->distilled FROM $t;", {"t": result.task})[0]
    assert {str(x) for x in consulted} == {str(d1), str(d2)}
    # task.run liga ao run
    assert db.query("SELECT VALUE run FROM $t;", {"t": result.task})[0] == result.run
    # dispatch de report: artifact=report, e NÃO move o watermark do digest (fica None)
    dispatch = db.query("SELECT artifact, status, watermark FROM dispatch;")[0]
    assert dispatch["artifact"] == "report"
    assert dispatch["status"] == "ok"
    assert dispatch["watermark"] is None
    assert knowledge.last_dispatch_watermark(db, "owner-telegram") is None
    assert len(sender.calls) == 1
    assert executor.calls == 1  # a etapa de síntese foi de fato exercitada (não pulou o LLM)


def test_flow_send_failure_lands_in_failed_with_deliverable_persisted(db: Any) -> None:
    """Falha de envio: o task termina em `failed`, mas o deliverable permanece no grafo
    (o produto é o grafo; o Telegram é entrega)."""
    _seed(db, "Rust", "resumo sobre Rust")

    result = run_flow(
        db,
        template_name="analysis",
        question="pergunta",
        embedder=_FakeEmbedder(),
        destination=_DEST,
        base_url=_BASE,
        executor=_FakeExecutor(),
        senders={"telegram": _FakeSender(fail=True)},
    )

    assert result.state == "failed"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "failed"
    produced = db.query("SELECT VALUE ->produces->deliverable FROM $f;", {"f": result.flow})[0]
    assert len(produced) == 1  # deliverable persistido apesar da falha de entrega
    dispatch = db.query("SELECT artifact, status FROM dispatch;")[0]
    assert dispatch["artifact"] == "report"
    assert dispatch["status"] == "error"
