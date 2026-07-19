"""Worker `digest` sob contrato (ADR-0015 §IV/§V) — RED do marco 12.5.

Unit puro: sem SurrealDB, sem rede. `ctx` é um fake que satisfaz `RunContext`
estruturalmente; o sender é um fake que registra chamadas (nenhum teste toca a
rede). Comportamento fixado (não implementação):
- um DispatchPayload(ok) por destino COM novidade; watermark = max(created_at) do
  conjunto; items = ids; item_count = tamanho.
- só-se-novidade: destino sem novidade → nenhum dispatch, stats new_distilled=0, run ok.
- cada destino é consultado com o SEU id (watermark por-destino).
- falha de envio → DispatchPayload(error) estruturado + ErrorInfo(dispatch_partial),
  sem explodir o run (§VII do ADR-0009); watermark ainda registrado (tentativa).
- o token do canal vem de ctx.integrations, nunca do worker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from kubo.contracts.models import DispatchPayload
from kubo.contracts.worker import DigestView
from kubo.distribution.destinations import Channel, ResolvedDestination
from kubo.errors import SenderError
from kubo.workers.digest import DigestConfig, DigestWorker

_NOW = datetime(2026, 7, 13, 9, 30, tzinfo=timezone.utc)
_BASE = "https://kubo.test:3900"
_FAKE_TOKEN = "BOT-TOKEN"  # constante nomeada (evita S107 no default do fake)


def _view(key: str, minutes: int = 0) -> DigestView:
    return DigestView(
        id=f"distilled:{key}",
        title=f"Titulo {key}",
        summary=f"resumo {key}",
        created_at=_NOW + timedelta(minutes=minutes),
        entities=["OpenAI"],
    )


def _dest(id_: str = "owner-telegram", channel: Channel = "telegram") -> ResolvedDestination:
    return ResolvedDestination(id=id_, name=id_, kind="pessoa", channel=channel, address="42")


class _FakeKnowledge:
    """distilled_for_digest devolve a lista canned do destino (por id)."""

    def __init__(self, per_dest: dict[str, list[DigestView]]) -> None:
        self._per_dest = per_dest
        self.calls: list[tuple[str, int]] = []

    def items_to_distill(self, limit: int) -> list[Any]:  # não usado pelo digest
        return []

    def distilled_for_digest(self, destination: str, limit: int) -> list[DigestView]:
        self.calls.append((destination, limit))
        return list(self._per_dest.get(destination, []))

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[Any]:  # não usado
        return []


@dataclass
class _Integration:
    secret: str | None


@dataclass
class _FakeCtx:
    config: DigestConfig
    integrations: dict[str, _Integration]
    knowledge: _FakeKnowledge
    logger: Any
    embedder: None = None


class _FakeSender:
    """Registra as chamadas de envio; opcionalmente falha (SenderError)."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    def __call__(self, *, token: str, chat_id: str, text: str) -> None:
        self.calls.append({"token": token, "chat_id": chat_id, "text": text})
        if self.fail:
            raise SenderError("Telegram respondeu HTTP 400")


def _ctx(knowledge: _FakeKnowledge, secret: str | None = _FAKE_TOKEN) -> _FakeCtx:
    return _FakeCtx(
        config=DigestConfig(),
        integrations={"telegram": _Integration(secret=secret)},
        knowledge=knowledge,
        logger=structlog.get_logger(),
    )


def _worker(destinations: list[ResolvedDestination], sender: _FakeSender) -> DigestWorker:
    return DigestWorker(destinations=destinations, base_url=_BASE, senders={"telegram": sender})


def _dispatch(payload: object) -> DispatchPayload:
    assert isinstance(payload, DispatchPayload)
    return payload


def test_sends_digest_and_records_ok_dispatch() -> None:
    """Destino com 3 novidades → 1 DispatchPayload(ok), sender chamado, watermark=max."""
    views = [_view("a", 0), _view("b", 10), _view("c", 5)]
    know = _FakeKnowledge({"owner-telegram": views})
    sender = _FakeSender()
    result = _worker([_dest()], sender).run(_ctx(know))

    assert len(result.payloads) == 1
    d = _dispatch(result.payloads[0])
    assert d.status == "ok"
    assert d.channel == "telegram"
    assert d.item_count == 3
    assert set(d.items) == {"distilled:a", "distilled:b", "distilled:c"}
    assert d.watermark == _NOW + timedelta(minutes=10)  # max(created_at)
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == 3
    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == "BOT-TOKEN"
    assert sender.calls[0]["chat_id"] == "42"


def test_no_novelty_sends_nothing() -> None:
    """Destino sem novidade → nenhum dispatch, nenhum envio, run ok, new_distilled=0."""
    know = _FakeKnowledge({"owner-telegram": []})
    sender = _FakeSender()
    result = _worker([_dest()], sender).run(_ctx(know))

    assert result.payloads == []
    assert sender.calls == []
    assert result.error is None
    assert result.stats.model_dump()["new_distilled"] == 0


def test_queries_each_destination_by_its_own_id() -> None:
    """Cada destino é consultado com o SEU id (watermark por-destino)."""
    know = _FakeKnowledge({"d1": [_view("x")], "d2": [_view("y")]})
    sender = _FakeSender()
    _worker([_dest("d1"), _dest("d2")], sender).run(_ctx(know))
    assert {c[0] for c in know.calls} == {"d1", "d2"}


def test_send_failure_becomes_error_dispatch_without_exploding() -> None:
    """Falha de envio → DispatchPayload(error) estruturado + ErrorInfo(dispatch_partial);
    watermark ainda registrado (tentativa), run não explode."""
    views = [_view("a", 0), _view("b", 3)]
    know = _FakeKnowledge({"owner-telegram": views})
    sender = _FakeSender(fail=True)
    result = _worker([_dest()], sender).run(_ctx(know))

    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert d.error is not None and d.error.kind == "telegram_send"
    assert d.watermark == _NOW + timedelta(minutes=3)
    assert result.error is not None and result.error.kind == "dispatch_partial"


def test_partial_failure_across_destinations() -> None:
    """Dois destinos, um ok e um com falha → payloads=[ok, error], dispatch_partial."""
    know = _FakeKnowledge({"good": [_view("a")], "bad": [_view("b")]})

    class _SelectiveSender:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, *, token: str, chat_id: str, text: str) -> None:
            self.calls += 1
            if chat_id == "bad-addr":
                raise SenderError("HTTP 403")

    sender = _SelectiveSender()
    good = ResolvedDestination(id="good", name="g", kind="pessoa", channel="telegram", address="ok")
    bad = ResolvedDestination(
        id="bad", name="b", kind="pessoa", channel="telegram", address="bad-addr"
    )
    worker = DigestWorker(destinations=[good, bad], base_url=_BASE, senders={"telegram": sender})
    result = worker.run(_ctx(know))

    statuses = {_dispatch(p).destination: _dispatch(p).status for p in result.payloads}
    assert statuses == {"good": "ok", "bad": "error"}
    assert result.error is not None and result.error.kind == "dispatch_partial"


def test_missing_token_is_send_error_not_crash() -> None:
    """Token ausente no ctx (least-privilege/env) → dispatch(error), não crash do run."""
    know = _FakeKnowledge({"owner-telegram": [_view("a")]})
    sender = _FakeSender()
    result = _worker([_dest()], sender).run(_ctx(know, secret=None))
    d = _dispatch(result.payloads[0])
    assert d.status == "error"
    assert sender.calls == []  # nunca chega a enviar sem token


def test_paused_returns_empty_ok_without_querying_knowledge() -> None:
    """Config `paused=true` fecha o run `ok` com zero envio e não avança watermark."""
    know = _FakeKnowledge({"owner-telegram": [_view("a")]})
    sender = _FakeSender()
    result = _worker([_dest()], sender).run(
        _FakeCtx(
            config=DigestConfig(paused=True),
            integrations={"telegram": _Integration(secret=_FAKE_TOKEN)},
            knowledge=know,
            logger=structlog.get_logger(),
        )
    )

    assert result.payloads == []
    assert result.error is None
    assert know.calls == []  # não consulta distilled
    assert sender.calls == []  # não envia
