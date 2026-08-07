"""Worker `telegram-digest` sob contrato (ADR-0029 §2/§3, ADR-0050) — unit puro.

Sem SurrealDB, sem rede. O worker atua sobre UM destino (canal Telegram), recebe
o endereço pelo construtor (PII, nunca na config/log/payload) e devolve um
`RunResult` com `DispatchPayload` ok/error — nunca explode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from kubo.contracts.models import DaySummaryPayload, OpinionPayload
from kubo.executors.base import Executor
from kubo.store.destinations import Destination
from kubo.workers._digest_editorial import DaySummaryOutput, OpinionOutput
from kubo.workers.digest import DigestConfig, TelegramDigestWorker
from tests.workers._digest_fixtures import (
    _assert_send_failure,
    _assert_sends_digest,
    _assert_wrong_channel,
    _destination,
    _dispatch,
    _FakeCtx,
    _FakeExecutor,
    _FakeKnowledge,
    _FakeSender,
    _selection,
    _view,
)

_FAKE_TOKEN = "BOT-TOKEN"


@dataclass
class _Integration:
    secret: str | None


def _ctx(knowledge: _FakeKnowledge, secret: str | None = _FAKE_TOKEN) -> _FakeCtx:
    return _FakeCtx(
        config=DigestConfig(),
        integrations={"telegram": _Integration(secret=secret)},
        knowledge=knowledge,
        logger=structlog.get_logger(),
    )


def _worker(destination: Destination, sender: _FakeSender) -> TelegramDigestWorker:
    return TelegramDigestWorker(
        destination=destination,
        base_url="https://kubo.test:3900",
        sender=sender,
    )


def test_sends_digest_and_records_ok_dispatch() -> None:
    sel = _selection([_view("a"), _view("b"), _view("c")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    _assert_sends_digest(
        result,
        sender,
        channel="telegram",
        address="42",
        expected_call={"token": "BOT-TOKEN", "chat_id": "42"},
    )
    d = _dispatch(result.payloads[0])
    assert set(d.items) == {"item:a", "item:b", "item:c"}


def test_empty_window_sends_warning_and_records_ok() -> None:
    sel = _selection(items=[], form="empty_window", total_publications=0)
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = _dispatch(result.payloads[0])
    assert d.status == "ok"
    assert d.item_count == 0
    assert d.items == []
    assert len(sender.calls) == 1  # aviso é enviado, não silêncio


def test_send_failure_becomes_error_dispatch_without_exploding() -> None:
    sel = _selection([_view("a"), _view("b")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender(fail=True)
    result = _worker(_destination(), sender).run(_ctx(know))

    _assert_send_failure(result, sender, kind="telegram_send", watermark_minutes=0)


def test_missing_token_is_send_error_not_crash() -> None:
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(_destination(), sender).run(_ctx(know, secret=None))
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert sender.calls == []


def test_address_never_appears_in_payload_config_or_repr() -> None:
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    destination = _destination(address="55669999")
    result = TelegramDigestWorker(
        destination=destination, base_url="https://kubo.test:3900", sender=sender
    ).run(_ctx(know))

    assert "55669999" not in repr(destination)
    assert result.payloads
    d = _dispatch(result.payloads[0])
    assert "55669999" not in d.model_dump_json()
    assert sender.calls[0]["chat_id"] == "55669999"


def test_non_telegram_destination_is_not_sent() -> None:
    email = _destination(key="e1b2c3d4e5f67890", channel="email", address="owner@example.com")
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:e1b2c3d4e5f67890": sel})
    sender = _FakeSender()
    result = _worker(email, sender).run(_ctx(know))

    _assert_wrong_channel(result, sender, kind="telegram_send")


# ── Enriquecimento editorial (ADR-0052, KUBO-195) ─────────────────────────────


def _worker_with_executor(
    destination: Destination,
    sender: _FakeSender,
    executor: Executor,
) -> TelegramDigestWorker:
    return TelegramDigestWorker(
        destination=destination,
        base_url="https://kubo.test:3900",
        sender=sender,
        executor=executor,
    )


def _ctx_with_executor(knowledge: _FakeKnowledge, executor: Executor) -> _FakeCtx:
    return _FakeCtx(
        config=DigestConfig(),
        integrations={"telegram": _Integration(secret=_FAKE_TOKEN)},
        knowledge=knowledge,
        logger=structlog.get_logger(),
        executor=executor,
    )


def test_opinion_computed_for_items_without_existing() -> None:
    """Cada item sem parecer persistido recebe um via LLM (ADR-0052 §I).
    O RunResult inclui OpinionPayload para cada parecer novo."""
    sel = _selection([_view("a"), _view("b")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor(
        outputs={
            0: OpinionOutput(opinion="Parecer A — importa porque X"),
            1: OpinionOutput(opinion="Parecer B — importa porque Y"),
            2: DaySummaryOutput(summary="Ontem saíram 2 publicações; o eixo foi IA"),
        }
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    opinions = [p for p in result.payloads if isinstance(p, OpinionPayload)]
    assert len(opinions) == 2
    assert opinions[0].item_id == "item:a"
    assert opinions[0].opinion == "Parecer A — importa porque X"
    assert opinions[1].item_id == "item:b"
    # O texto enviado tem o parecer
    sent_text = str(sender.calls[0]["text"])
    assert "Parecer A" in sent_text
    assert "Parecer B" in sent_text


def test_opinion_reused_from_store_not_recomputed() -> None:
    """Parecer já persistido é lido do banco, não recomputado (ADR-0052 §I).
    Só o item sem parecer chama o LLM."""
    sel = _selection([_view("a"), _view("b")])
    know = _FakeKnowledge(
        {"destination:a1b2c3d4e5f67890": sel},
        opinions={"item:a": "Parecer A já existente"},
    )
    executor = _FakeExecutor(
        outputs={
            0: OpinionOutput(opinion="Parecer B — novo"),
            1: DaySummaryOutput(summary="Resumo do dia"),
        }
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    opinions = [p for p in result.payloads if isinstance(p, OpinionPayload)]
    assert len(opinions) == 1  # só item:b
    assert opinions[0].item_id == "item:b"
    # O texto enviado tem o parecer reusado e o novo
    sent_text = str(sender.calls[0]["text"])
    assert "Parecer A já existente" in sent_text
    assert "Parecer B — novo" in sent_text


def test_day_summary_read_from_store_not_recomputed() -> None:
    """Resumo do dia já persistido é lido do banco, não recomputado (ADR-0052 §II)."""
    sel = _selection([_view("a")])
    day = (
        sel.watermark.astimezone(timezone.utc).date()
        if sel.watermark
        else datetime.now(timezone.utc).date()
    )
    know = _FakeKnowledge(
        {"destination:a1b2c3d4e5f67890": sel},
        day_summaries={day: "Resumo do dia já existente"},
    )
    executor = _FakeExecutor(outputs={0: OpinionOutput(opinion="Parecer A")})
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    day_summaries = [p for p in result.payloads if isinstance(p, DaySummaryPayload)]
    assert len(day_summaries) == 0  # não recomputou
    sent_text = str(sender.calls[0]["text"])
    assert "Resumo do dia já existente" in sent_text


def test_day_summary_computed_when_absent() -> None:
    """Se o resumo do dia não existe, o digest computa e grava (fallback, ADR-0052 §III)."""
    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor(
        outputs={
            0: OpinionOutput(opinion="Parecer A"),
            1: DaySummaryOutput(summary="Ontem saíram 1 publicação; o eixo foi IA"),
        }
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    day_summaries = [p for p in result.payloads if isinstance(p, DaySummaryPayload)]
    assert len(day_summaries) == 1
    assert day_summaries[0].summary == "Ontem saíram 1 publicação; o eixo foi IA"
    sent_text = str(sender.calls[0]["text"])
    assert "Ontem saíram 1 publicação" in sent_text


def test_no_enrichment_for_empty_window() -> None:
    """Forma empty_window não enriquece — não há itens para opinar nem dia para resumir."""
    sel = _selection(items=[], form="empty_window", total_publications=0)
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor()
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    opinions = [p for p in result.payloads if isinstance(p, OpinionPayload)]
    day_summaries = [p for p in result.payloads if isinstance(p, DaySummaryPayload)]
    assert len(opinions) == 0
    assert len(day_summaries) == 0
    assert executor.call_count == 0  # nenhuma chamada LLM


def test_opinion_malformed_does_not_block_digest() -> None:
    """MalformedOutputError no parecer é tratado — digest sai sem parecer, sem
    OpinionPayload persistido (ADR-0052 §I, CLAUDE.md: erros não explodem)."""
    from kubo.errors import MalformedOutputError

    sel = _selection([_view("a"), _view("b")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor(
        outputs={2: DaySummaryOutput(summary="Resumo do dia")},
        errors={0: MalformedOutputError("bad"), 1: MalformedOutputError("bad")},
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    opinions = [p for p in result.payloads if isinstance(p, OpinionPayload)]
    assert len(opinions) == 0  # nenhum parecer persistido
    # O digest foi enviado (não derrubado pelo erro)
    assert len(sender.calls) == 1


def test_opinion_rate_limited_does_not_block_digest() -> None:
    """RateLimitExhausted no parecer é tratado — digest sai sem parecer (ADR-0052 §I)."""
    from kubo.errors import RateLimitExhausted

    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor(
        outputs={1: DaySummaryOutput(summary="Resumo do dia")},
        errors={0: RateLimitExhausted("rate limited", scope="day")},
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    opinions = [p for p in result.payloads if isinstance(p, OpinionPayload)]
    assert len(opinions) == 0
    assert len(sender.calls) == 1


def test_day_summary_malformed_does_not_block_digest() -> None:
    """MalformedOutputError no resumo do dia é tratado — digest sai sem resumo,
    sem DaySummaryPayload persistido (ADR-0052 §III)."""
    from kubo.errors import MalformedOutputError

    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor(
        outputs={0: OpinionOutput(opinion="Parecer A")},
        errors={1: MalformedOutputError("bad")},
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    day_summaries = [p for p in result.payloads if isinstance(p, DaySummaryPayload)]
    assert len(day_summaries) == 0
    # O digest foi enviado com o parecer, mas sem o resumo
    assert len(sender.calls) == 1
    sent_text = str(sender.calls[0]["text"])
    assert "Parecer A" in sent_text


def test_day_summary_rate_limited_does_not_block_digest() -> None:
    """RateLimitExhausted no resumo do dia é tratado — digest sai sem resumo (ADR-0052 §III)."""
    from kubo.errors import RateLimitExhausted

    sel = _selection([_view("a")])
    know = _FakeKnowledge({"destination:a1b2c3d4e5f67890": sel})
    executor = _FakeExecutor(
        outputs={0: OpinionOutput(opinion="Parecer A")},
        errors={1: RateLimitExhausted("rate limited", scope="day")},
    )
    sender = _FakeSender()
    worker = _worker_with_executor(_destination(), sender, executor)
    result = worker.run(_ctx_with_executor(know, executor))

    day_summaries = [p for p in result.payloads if isinstance(p, DaySummaryPayload)]
    assert len(day_summaries) == 0
    assert len(sender.calls) == 1
