"""Vertical do digest ponta a ponta (integração, SurrealDB) — ADR-0015 §IV, ADR-0050.

`run_worker` completo com `TelegramDigestWorker` (sender FAKE, destino injetado)
contra o banco real: prova o encanamento `items_for_digest` (janela de publicação
+ score + distilled) → builder → sender → `DispatchPayload` → `_persist` (parse
`item:<id>` → RecordID + `insert_dispatch`). Cobre o ramo `DispatchPayload`
do runner e o critério físico do plano em unit: enviar cria dispatch(ok);
re-rodar sem novidade envia aviso (não silêncio — ADR-0050 revoga só-se-novidade).
ZERO rede — o sender fake nunca toca o Bot API.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.runtime.runner import run_worker
from kubo.store import client, destinations, knowledge, migrations
from kubo.workers.digest import TelegramDigestWorker

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


def _seed_items(db: Any, tenant_id: RecordID, user_id: RecordID, summaries: list[str]) -> None:
    """Cria itens com published_at=ontem, score=8 e distilled — prontos para o digest."""
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    src = knowledge.upsert_source(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        kind="rss",
        canonical=f"src::{secrets.token_hex(4)}",
    )
    for summary in summaries:
        item = knowledge.upsert_item(
            db,
            source=src,
            external_id=secrets.token_hex(4),
            content="x",
            title=f"Title {summary}",
            url=f"https://example.com/{secrets.token_hex(4)}",
            published_at=yesterday,
        )
        knowledge.apply_score(db, tenant_id=tenant_id, user_id=user_id, item=item, score=8)
        knowledge.insert_distilled(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            item=item,
            summary=summary,
            chunks=[],
        )


def _worker(sender: _RecordingSender) -> TelegramDigestWorker:
    dest = destinations.Destination(
        id=RecordID("destination", "ownertelegram"),
        name="dono",
        kind="pessoa",
        channel="telegram",
        address="99",
        enabled=True,
        archived_at=None,
        dispatches=0,
    )
    return TelegramDigestWorker(
        destination=dest,
        base_url="https://kubo.test:3900",
        sender=sender,
    )


def _dispatch_rows(db: Any) -> list[dict[str, Any]]:
    return list(db.query("SELECT * FROM dispatch ORDER BY sent_at ASC;") or [])


def test_digest_vertical_sends_and_persists_dispatch(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Feliz: 3 itens aprovados na janela → sender chamado 1x, 1 dispatch(ok)
    persistido com item_count=3 e watermark, o token resolvido do env chega ao sender."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _CHAT_TOKEN)
    _seed_items(db, tenant_id, user_id, ["a", "b", "c"])
    sender = _RecordingSender()

    run_worker(db, _worker(sender), config={"max_items": 50}, tenant_id=tenant_id, user_id=user_id)

    assert len(sender.calls) == 1
    assert sender.calls[0]["token"] == _CHAT_TOKEN
    assert sender.calls[0]["chat_id"] == "99"
    rows = _dispatch_rows(db)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["item_count"] == 3
    assert len(rows[0]["items"]) == 3
    assert rows[0]["watermark"] is not None


def test_digest_vertical_rerun_sends_warning_not_silence(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-rodar após um dispatch ok, sem itens novos → envia AVISO (não silêncio),
    e persiste um segundo dispatch(ok) com item_count=0 (ADR-0050 revoga
    só-se-novidade). O critério físico do plano, provado em unit."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _CHAT_TOKEN)
    _seed_items(db, tenant_id, user_id, ["a", "b"])
    run_worker(
        db,
        _worker(_RecordingSender()),
        config={"max_items": 50},
        tenant_id=tenant_id,
        user_id=user_id,
    )
    assert len(_dispatch_rows(db)) == 1

    second = _RecordingSender()
    run_worker(db, _worker(second), config={"max_items": 50}, tenant_id=tenant_id, user_id=user_id)

    assert len(second.calls) == 1  # aviso enviado, não silêncio
    assert len(_dispatch_rows(db)) == 2  # segundo dispatch(ok) com item_count=0
    row = _dispatch_rows(db)[1]
    assert row["item_count"] == 0
    assert row["status"] == "ok"
    assert row["items"] == []
