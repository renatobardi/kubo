"""Vertical do flow `analysis-review` com GATE (integração, SurrealDB real — ADR-0018).

Prova o ciclo inteiro com LLM/embedder/Telegram FALSOS mas store e runner REAIS:
`run_flow(analysis-review)` roda a analista em PRODUCE-ONLY → o relatório PARA no gate
(analista + humano em awaiting_review, deliverable no grafo, NENHUM envio ainda) → o dono é
notificado (dispatch artifact=gate) → `resume_gate` ENVIA agora e leva as 2 tasks a delivered
numa transação → `reject_gate` arquiva com motivo. E prova o at-least-once: falha de envio na
aprovação deixa o gate ABERTO.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Any

import pytest

from kubo.distribution.destinations import ResolvedDestination
from kubo.errors import SenderError
from kubo.runtime.flow_runner import reject_gate, resume_gate, run_flow
from kubo.store import client, knowledge, migrations
from kubo.store.knowledge import Chunk
from kubo.workers.analyst import ReportOutput

pytestmark = pytest.mark.integration

_DB = "test_flow_review_vertical"
_DEST = ResolvedDestination(
    id="owner-telegram", name="Renato", kind="pessoa", channel="telegram", address="chat-1"
)
_BASE = "https://kubo.example"


@pytest.fixture(autouse=True)
def _telegram_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolução EAGER de integrações (produce-only ainda declara telegram no manifest) e a
    notificação/envio de gate exigem o token no env — o sender é falso e o ignora."""
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
    def __init__(self, report: str = "Análise sintetizada.") -> None:
        self._report = report

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
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
            raise SenderError("Telegram HTTP 400")


def _seed(db: Any, title: str, summary: str) -> Any:
    src = knowledge.upsert_source(db, kind="rss", canonical=f"src::{title}")
    item = knowledge.upsert_item(
        db, source=src, external_id=f"ext::{title}", content="x", title=title
    )
    chunk = Chunk(text=summary, seq=0, embedding=[0.1] * 768, model="m", dim=768, task_type="X")
    return knowledge.insert_distilled(db, item=item, summary=summary, chunks=[chunk])


def _run_to_gate(db: Any, sender: _FakeSender) -> Any:
    """Instancia e roda até o gate abrir; devolve o FlowRunResult (state=awaiting_review)."""
    _seed(db, "Rust", "resumo sobre Rust")
    return run_flow(
        db,
        template_name="analysis-review",
        question="o que dizem sobre memória?",
        embedder=_FakeEmbedder(),
        destination=_DEST,
        base_url=_BASE,
        executor=_FakeExecutor(),
        senders={"telegram": sender},
    )


def test_run_parks_at_gate_and_notifies_without_delivering(db: Any) -> None:
    """Run-até-gate: a analista PARA em awaiting_review, o humano recebe task, o deliverable
    (prosa) existe no grafo, o dono é notificado (dispatch artifact=gate) — e NADA de report
    ainda (o relatório espera a decisão)."""
    notify = _FakeSender()
    result = _run_to_gate(db, notify)

    assert result.state == "awaiting_review"
    assert result.gate_task is not None
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "awaiting_review"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.gate_task})[0] == "awaiting_review"
    # deliverable produzido (prosa pura), mas NÃO enviado durante o run
    deliv = db.query("SELECT VALUE ->produces->deliverable FROM $f;", {"f": result.flow})[0]
    assert len(deliv) == 1
    # gate task é do humano
    persona = db.query(
        "SELECT VALUE ->assigned_to->persona.catalog_name FROM $t;", {"t": result.gate_task}
    )[0]
    assert persona == ["humano"]
    # notificação: 1 mensagem + dispatch artifact=gate (só isso — nenhum report ainda)
    assert len(notify.calls) == 1
    assert db.query("SELECT VALUE artifact FROM dispatch;") == ["gate"]


def test_approve_sends_and_delivers_both_tasks(db: Any) -> None:
    """Aprovação: o relatório é ENVIADO agora, o dispatch de report é gravado (não move o
    watermark do digest) e as 2 tasks vão a delivered com a decisão na task do gate."""
    result = _run_to_gate(db, _FakeSender())
    approve = _FakeSender()

    resume_gate(
        db,
        gate_task=result.gate_task,
        destination=_DEST,
        base_url=_BASE,
        senders={"telegram": approve},
    )

    assert len(approve.calls) == 1  # o envio acontece SÓ na aprovação
    assert approve.calls[0]["parse_mode"] == "HTML"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "delivered"
    gate = db.query("SELECT state, decision, decided_at FROM $t;", {"t": result.gate_task})[0]
    assert gate["state"] == "delivered"
    assert gate["decision"] == "approved"
    assert gate["decided_at"] is not None
    arts = {r["artifact"] for r in db.query("SELECT artifact FROM dispatch;")}
    assert arts == {"gate", "report"}
    assert knowledge.last_dispatch_watermark(db, "owner-telegram") is None


def test_reject_archives_both_tasks_with_reason(db: Any) -> None:
    """Rejeição: as 2 tasks vão a rejected, o motivo obrigatório fica na task do gate, e NÃO
    há envio de report (só o dispatch de gate da abertura)."""
    result = _run_to_gate(db, _FakeSender())

    reject_gate(db, gate_task=result.gate_task, reason="fontes fracas para a pergunta")

    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "rejected"
    gate = db.query("SELECT state, decision, reason FROM $t;", {"t": result.gate_task})[0]
    assert gate["state"] == "rejected"
    assert gate["decision"] == "rejected"
    assert gate["reason"] == "fontes fracas para a pergunta"
    assert db.query("SELECT VALUE artifact FROM dispatch;") == ["gate"]  # nenhum report


def test_approve_send_failure_keeps_gate_open(db: Any) -> None:
    """At-least-once (ADR-0018 §V): falha de ENVIO na aprovação deixa o gate ABERTO (o dono
    clica de novo), grava o dispatch de report em erro e NÃO decide o gate."""
    result = _run_to_gate(db, _FakeSender())

    with pytest.raises(SenderError):
        resume_gate(
            db,
            gate_task=result.gate_task,
            destination=_DEST,
            base_url=_BASE,
            senders={"telegram": _FakeSender(fail=True)},
        )

    # gate segue aberto — nada transicionou
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.gate_task})[0] == "awaiting_review"
    assert db.query("SELECT VALUE state FROM $t;", {"t": result.task})[0] == "awaiting_review"
    report = db.query("SELECT VALUE status FROM dispatch WHERE artifact = 'report';")
    assert report == ["error"]
