"""Fixtures e helpers compartilhados pelos testes de workers de digest.

Módulo privado (não coletado pelo pytest) para evitar duplicação entre os testes
de TelegramDigestWorker e EmailDigestWorker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel
from surrealdb import RecordID

from kubo.contracts.models import DispatchPayload
from kubo.contracts.worker import DigestSelectionView, DigestView
from kubo.errors import SenderError
from kubo.executors.base import Executor
from kubo.store.destinations import Channel, Destination
from kubo.workers._digest_common import DigestConfig
from kubo.workers._digest_editorial import DaySummaryOutput, OpinionOutput

_NOW = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
_BASE = "https://kubo.test:3900"


def _view(key: str = "abc", score: int = 7) -> DigestView:
    return DigestView(
        id=f"item:{key}",
        title=f"Titulo {key}",
        summary=f"resumo {key}",
        score=score,
        published_at=_NOW,
        url=f"https://example.com/{key}",
        entities=["OpenAI"],
    )


def _selection(
    items: list[DigestView] | None = None,
    *,
    form: Literal["normal", "empty_window", "none_passed", "recovery"] = "normal",
    total_publications: int | None = None,
    watermark: datetime | None = _NOW,
) -> DigestSelectionView:
    views = items if items is not None else [_view("a"), _view("b"), _view("c")]
    return DigestSelectionView(
        form=form,
        items=views,
        window_start=_NOW - timedelta(days=1),
        window_end=_NOW,
        watermark=watermark,
        total_publications=total_publications if total_publications is not None else len(views),
    )


def _destination(
    key: str = "a1b2c3d4e5f67890",  # pragma: allowlist secret
    channel: Channel = "telegram",
    address: str = "42",
) -> Destination:
    return Destination(
        id=RecordID("destination", key),
        name="Owner",
        kind="pessoa",
        channel=channel,
        address=address,
        enabled=True,
        archived_at=None,
        dispatches=0,
    )


class _FakeKnowledge:
    def __init__(
        self,
        per_dest: dict[str, DigestSelectionView],
        *,
        opinions: dict[str, str] | None = None,
        day_summaries: dict[date, str] | None = None,
    ) -> None:
        self._per_dest = per_dest
        self._opinions = opinions or {}
        self._day_summaries = day_summaries or {}
        self.calls: list[tuple[str, int]] = []

    def items_to_score(self, limit: int) -> list[Any]:
        return []

    def work_context(self) -> str:
        return ""

    def items_for_digest(self, destination: str, limit: int) -> DigestSelectionView:
        self.calls.append((destination, limit))
        return self._per_dest.get(destination, _selection(items=[], form="empty_window"))

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[Any]:
        return []

    def get_opinions(self, item_ids: list[str]) -> dict[str, str]:
        return {k: v for k, v in self._opinions.items() if k in item_ids}

    def get_day_summary(self, day: date) -> str | None:
        return self._day_summaries.get(day)


class _FakeExecutor:
    """Fake de `Executor` para testes de digest — devolve outputs por índice.

    Suporta exceções por índice: se `errors[idx]` existe, levanta a exceção
    em vez de devolver o output (para testar caminhos não-fatais)."""

    def __init__(
        self,
        outputs: dict[int, BaseModel] | None = None,
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self._outputs = outputs or {}
        self._errors = errors or {}
        self.call_count = 0
        self.received_content: list[str] = []
        self.received_instructions: list[str] = []

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        idx = self.call_count
        self.call_count += 1
        self.received_content.append(untrusted_content)
        self.received_instructions.append(instruction)
        if idx in self._errors:
            raise self._errors[idx]
        return self._outputs[idx]


class _FakeOpinionExecutor:
    """Executor fake para testes verticais de enriquecimento editorial.
    Devolve parecer e resumo do dia canned, com contagem de chamadas por tipo."""

    def __init__(self, opinion: str, day_summary: str) -> None:
        self._opinion = opinion
        self._day_summary = day_summary
        self.opinion_calls = 0
        self.day_summary_calls = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        if response_model is OpinionOutput:
            self.opinion_calls += 1
            return OpinionOutput(opinion=self._opinion)
        if response_model is DaySummaryOutput:
            self.day_summary_calls += 1
            return DaySummaryOutput(summary=self._day_summary)
        raise ValueError(f"unexpected model: {response_model}")


@dataclass
class _FakeCtx:
    config: DigestConfig
    integrations: dict[str, Any]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: None = None
    executor: Executor | None = None


class _FakeSender:
    """Registra as chamadas de envio; opcionalmente falha (SenderError)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise SenderError("send failure")


def _dispatch(payload: object) -> DispatchPayload:
    assert isinstance(payload, DispatchPayload)
    return payload


def _assert_sends_digest(
    result: Any,
    sender: _FakeSender,
    *,
    channel: str,
    address: str,
    expected_call: dict[str, object] | None = None,
    item_count: int = 3,
) -> None:
    assert len(result.payloads) == 1
    d = _dispatch(result.payloads[0])
    assert d.status == "ok"
    assert d.channel == channel
    assert d.destination == "destination:a1b2c3d4e5f67890"
    assert d.item_count == item_count
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == item_count
    assert len(sender.calls) == 1
    if expected_call:
        for key, value in expected_call.items():
            assert sender.calls[0][key] == value


def _assert_send_failure(
    result: Any,
    sender: _FakeSender,
    *,
    kind: str,
    watermark_minutes: int,
) -> None:
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == kind
    assert d.watermark == _NOW + timedelta(minutes=watermark_minutes)
    assert result.error is not None and result.error.kind == kind


def _assert_wrong_channel(
    result: Any,
    sender: _FakeSender,
    *,
    kind: str,
) -> None:
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == kind
    assert sender.calls == []
