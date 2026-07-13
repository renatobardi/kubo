"""Worker `analista` — unit puro (ADR-0016 §III/§VI).

Sem SurrealDB, sem rede, sem LiteLLM: `ctx` é um `_FakeCtx`, o LLM é `_FakeExecutor`,
a busca é `_FakeKnowledge`, o Telegram é `_FakeSender`. Comportamento fixado (não
implementação):
- as CITAÇÕES vêm do RETRIEVAL, nunca da saída do LLM (§VI, o teste central);
- D6: a pergunta vai na `instruction`, os summaries no `untrusted_content`;
- envio falho → dispatch(error) + ErrorInfo, o deliverable permanece no payload;
- acervo vazio → resposta honesta fixa, SEM chamada de LLM;
- o dispatch de report tem artifact=report e watermark None (não move o do digest).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
import structlog

from kubo.contracts.models import DispatchPayload, ReportPayload
from kubo.contracts.worker import RetrievedView, validate_worker
from kubo.distribution.destinations import ResolvedDestination
from kubo.errors import ConfigError, SenderError
from kubo.workers.analyst import AnalystConfig, AnalystWorker, ReportOutput

_DEST = ResolvedDestination(
    id="owner-telegram", name="Renato", kind="pessoa", channel="telegram", address="chat-123"
)
_BASE = "https://kubo.example"
_DOCS = [
    RetrievedView(id="distilled:aaa111", title="Rust ownership", summary="resumo sobre Rust"),
    RetrievedView(id="distilled:bbb222", title="GC tradeoffs", summary="resumo sobre GC"),
]


class _FakeExecutor:
    """Fake de `Executor`: devolve `output` e registra instruction+untrusted recebidos."""

    def __init__(self, output: ReportOutput) -> None:
        self._output = output
        self.call_count = 0
        self.instruction = ""
        self.untrusted = ""

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        self.call_count += 1
        self.instruction = instruction
        self.untrusted = untrusted_content
        return self._output


class _FakeEmbedder:
    """Fake de `Embedder`: tripla fixa + vetor fixo de 768 floats por texto."""

    model = "gemini-embedding-001"
    dim = 768
    task_type = "SEMANTIC_SIMILARITY"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]


class _FakeKnowledge:
    """Fake do seam: devolve os RetrievedView canned, ignora embedding/k."""

    def __init__(self, docs: list[RetrievedView]) -> None:
        self._docs = docs

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[RetrievedView]:
        return list(self._docs)


class _FakeSender:
    """Fake de sender: captura os kwargs; opcionalmente levanta SenderError."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self._fail:
            raise SenderError("Telegram respondeu HTTP 400")


@dataclass
class _FakeCtx:
    """Fake de `RunContext` — atributos simples satisfazem o Protocol estruturalmente."""

    config: AnalystConfig
    integrations: dict[str, Any]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: _FakeEmbedder | None


class _Integ:
    """Integração resolvida mínima com segredo (o worker lê `.secret`)."""

    def __init__(self, secret: str) -> None:
        self.secret = secret


def _worker(sender: _FakeSender) -> AnalystWorker:
    return AnalystWorker(
        _FakeExecutor(ReportOutput(report="corpo")),
        prompt="Você é a analista.",
        destination=_DEST,
        base_url=_BASE,
        senders={"telegram": sender},
    )


_DEFAULT_EMBEDDER = _FakeEmbedder()


def _ctx(
    docs: list[RetrievedView], *, embedder: _FakeEmbedder | None = _DEFAULT_EMBEDDER
) -> _FakeCtx:
    return _FakeCtx(
        config=AnalystConfig(question="o que é ownership em Rust?", k=5),
        integrations={"telegram": _Integ("bot-token")},
        knowledge=_FakeKnowledge(docs),
        logger=structlog.get_logger(),
        embedder=embedder,
    )


def _run(worker: AnalystWorker, ctx: _FakeCtx) -> Any:
    return worker.run(ctx)  # type: ignore[arg-type]


def test_produces_report_and_report_dispatch() -> None:
    """Caminho feliz: RunResult com ReportPayload (markdown + fontes) + DispatchPayload
    ok de report; o sender do Telegram foi chamado em texto puro."""
    sender = _FakeSender()
    result = _run(_worker(sender), _ctx(_DOCS))

    report = next(p for p in result.payloads if isinstance(p, ReportPayload))
    dispatch = next(p for p in result.payloads if isinstance(p, DispatchPayload))
    assert "## Fontes" in report.content
    assert dispatch.artifact == "report"
    assert dispatch.watermark is None
    assert dispatch.status == "ok"
    assert result.error is None
    assert len(sender.calls) == 1
    assert sender.calls[0]["parse_mode"] is None
    assert sender.calls[0]["chat_id"] == "chat-123"


def test_citations_come_from_retrieval_not_from_llm() -> None:
    """§VI, o teste central: o LLM devolve um texto que INVENTA uma fonte; consulted e as
    fontes renderizadas contêm SÓ os ids do retrieval, nunca o id forjado pelo modelo."""
    forging = AnalystWorker(
        _FakeExecutor(ReportOutput(report="Conforme distilled:forjado999, a resposta é X.")),
        prompt="p",
        destination=_DEST,
        base_url=_BASE,
        senders={"telegram": _FakeSender()},
    )
    result = _run(forging, _ctx(_DOCS))

    report = next(p for p in result.payloads if isinstance(p, ReportPayload))
    dispatch = next(p for p in result.payloads if isinstance(p, DispatchPayload))
    assert report.consulted == ["distilled:aaa111", "distilled:bbb222"]
    assert "forjado999" not in report.consulted
    # a seção de fontes lista só os recuperados (links do retrieval), não o id inventado
    assert "distilled/aaa111" in report.content
    assert "distilled/bbb222" in report.content
    assert "forjado999" not in report.content.split("## Fontes")[1]
    assert dispatch.items == ["distilled:aaa111", "distilled:bbb222"]


def test_question_in_instruction_summaries_in_untrusted() -> None:
    """D6: a pergunta do dono vai na `instruction` (confiável); os summaries vão no
    `untrusted_content` com separadores [DOCUMENTO N] — nunca o contrário."""
    executor = _FakeExecutor(ReportOutput(report="corpo"))
    worker = AnalystWorker(
        executor,
        prompt="Você é a analista.",
        destination=_DEST,
        base_url=_BASE,
        senders={"telegram": _FakeSender()},
    )
    _run(worker, _ctx(_DOCS))

    assert "ownership em Rust" in executor.instruction
    assert "Você é a analista." in executor.instruction
    assert "[DOCUMENTO 1]" in executor.untrusted
    assert "resumo sobre Rust" in executor.untrusted
    assert "ownership em Rust" not in executor.untrusted


def test_send_failure_yields_error_dispatch_but_keeps_deliverable() -> None:
    """Envio falho → DispatchPayload(error) + ErrorInfo, mas o ReportPayload (o deliverable)
    permanece: o produto é o grafo, o Telegram é entrega."""
    result = _run(_worker(_FakeSender(fail=True)), _ctx(_DOCS))

    assert any(isinstance(p, ReportPayload) for p in result.payloads)
    dispatch = next(p for p in result.payloads if isinstance(p, DispatchPayload))
    assert dispatch.status == "error"
    assert dispatch.artifact == "report"
    assert result.error is not None
    assert result.error.kind == "telegram_send"


def test_empty_acervo_answers_honestly_without_calling_llm() -> None:
    """Acervo vazio: resposta honesta fixa, SEM chamada de LLM, consulted vazio, e ainda
    entrega (o dono recebe 'não há fontes')."""
    executor = _FakeExecutor(ReportOutput(report="NUNCA DEVE APARECER"))
    worker = AnalystWorker(
        executor, prompt="p", destination=_DEST, base_url=_BASE, senders={"telegram": _FakeSender()}
    )
    result = _run(worker, _ctx([]))

    report = next(p for p in result.payloads if isinstance(p, ReportPayload))
    assert executor.call_count == 0
    assert report.consulted == []
    assert "Não encontrei fontes" in report.content


def test_missing_embedder_is_config_error() -> None:
    """Sem embedder no ctx → ConfigError (busca semântica é impossível sem ele)."""
    worker = _worker(_FakeSender())
    ctx = _ctx(_DOCS, embedder=None)
    with pytest.raises(ConfigError):
        _run(worker, ctx)


def test_validate_worker_accepts_analyst() -> None:
    """A AnalystWorker honra o contrato de worker (manifest + run(ctx))."""
    manifest = validate_worker(_worker(_FakeSender()))
    assert manifest.name == "analista"
    assert manifest.integrations == ["telegram"]
