"""Vertical do digest ponta a ponta (integração, SurrealDB) — ADR-0015 §IV/§V.

`run_worker` completo com `DigestWorker` (sender FAKE, destinos injetados) contra o
banco real: prova o encanamento `distilled_for_digest` (watermark + bootstrap) →
builder → sender → `DispatchPayload` → `_persist` (parse `distilled:<id>`→RecordID +
`insert_dispatch`). Cobre o ramo `DispatchPayload` do runner e o critério físico do
plano em unit: enviar cria dispatch(ok); re-rodar sem novidade não envia nada.
ZERO rede — o sender fake nunca toca o Bot API.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.distribution.destinations import ResolvedDestination
from kubo.runtime.runner import run_worker
from kubo.store import client, knowledge, migrations
from kubo.workers.digest import DigestWorker

pytestmark = pytest.mark.integration

_DIGEST_DB = "test_digest_vertical"
# valor de teste, não segredo real
_CHAT_TOKEN = "fake-bot-token"  # noqa: S105


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, removido antes e depois — schema aplicado do zero."""
    cfg = replace(client.config(), database=_DIGEST_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DIGEST_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DIGEST_DB};")


class _RecordingSender:
    """Sender fake: registra cada envio; nunca toca a rede."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def __call__(self, *, token: str, chat_id: str, text: str) -> None:
        self.calls.append({"token": token, "chat_id": chat_id, "text": text})


def _seed_distilled(db: Any, summaries: list[str]) -> None:
    """Insere destilados (created_at≈now, sequencial → strictly crescente) via a store."""
    for summary in summaries:
        knowledge.insert_distilled(db, item=_orphan_item(db), summary=summary, chunks=[])


def _orphan_item(db: Any) -> Any:
    """Cria um item mínimo para o distilled derivar (derived_from exige endpoint)."""
    import secrets

    src = knowledge.upsert_source(db, kind="rss", canonical=f"src::{secrets.token_hex(4)}")
    return knowledge.upsert_item(
        db, source=src, external_id=secrets.token_hex(4), content="x", title="T"
    )


def _worker(sender: _RecordingSender) -> DigestWorker:
    dest = ResolvedDestination(
        id="owner-telegram", name="dono", kind="pessoa", channel="telegram", address="99"
    )
    return DigestWorker(
        destinations=[dest], base_url="https://kubo.test:3900", senders={"telegram": sender}
    )


def _dispatch_rows(db: Any) -> list[dict[str, Any]]:
    return list(db.query("SELECT * FROM dispatch;") or [])


def test_digest_vertical_sends_and_persists_dispatch(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Feliz: 3 destilados novos → sender chamado 1x, 1 dispatch(ok) persistido com
    item_count=3 e watermark, o token resolvido do env chega ao sender."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _CHAT_TOKEN)
    _seed_distilled(db, ["a", "b", "c"])
    sender = _RecordingSender()

    run_worker(db, _worker(sender), config={"max_items": 50})

    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == _CHAT_TOKEN
    assert sender.calls[0]["chat_id"] == "99"
    rows = _dispatch_rows(db)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["item_count"] == 3
    assert len(rows[0]["items"]) == 3
    assert rows[0]["watermark"] is not None


def test_digest_vertical_rerun_without_novelty_is_noop(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-rodar após um dispatch ok, sem destilado novo → nenhum envio, nenhum
    dispatch novo (só-se-novidade + watermark, ADR-0015 §V). O critério físico do
    plano, provado em unit."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _CHAT_TOKEN)
    _seed_distilled(db, ["a", "b"])
    run_worker(db, _worker(_RecordingSender()), config={"max_items": 50})
    assert len(_dispatch_rows(db)) == 1

    second = _RecordingSender()
    run_worker(db, _worker(second), config={"max_items": 50})

    assert second.calls == []  # nada novo → nada enviado
    assert len(_dispatch_rows(db)) == 1  # nenhum dispatch novo
