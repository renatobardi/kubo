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


# ── Enriquecimento editorial (ADR-0052, KUBO-195) ─────────────────────────────


class _FakeOpinionExecutor:
    """Executor LLM fake que devolve parecer + resumo do dia canned."""

    def __init__(self, opinion: str, day_summary: str) -> None:
        self._opinion = opinion
        self._day_summary = day_summary
        self.call_count = 0

    def complete(self, instruction: str, untrusted_content: str, response_model: type[Any]) -> Any:
        from kubo.workers._digest_editorial import DaySummaryOutput, OpinionOutput

        self.call_count += 1
        if response_model is OpinionOutput:
            return OpinionOutput(opinion=self._opinion)
        if response_model is DaySummaryOutput:
            return DaySummaryOutput(summary=self._day_summary)
        raise ValueError(f"unexpected model: {response_model}")


def test_digest_vertical_with_editorial_sends_opinion_and_day_summary(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vertical do enriquecimento editorial (ADR-0052): itens seedados → digest
    worker com executor fake → parecer computado e resumo do dia (fallback)
    enviados no texto, e OpinionPayload + DaySummaryPayload persistidos pelo
    runner (aresta `opinion_for` + tabela `day_summary`)."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _CHAT_TOKEN)
    _seed_items(db, tenant_id, user_id, ["sobre IA", "sobre Rust"])
    executor = _FakeOpinionExecutor(
        opinion="Parecer editorial teste — importa porque X",
        day_summary="Ontem saíram 2 publicações; o eixo foi IA aplicada",
    )
    sender = _RecordingSender()
    worker = TelegramDigestWorker(
        destination=destinations.Destination(
            id=RecordID("destination", "ownertelegram2"),
            name="dono",
            kind="pessoa",
            channel="telegram",
            address="99",
            enabled=True,
            archived_at=None,
            dispatches=0,
        ),
        base_url="https://kubo.test:3900",
        sender=sender,
        executor=executor,
    )

    run_worker(db, worker, config={"max_items": 50}, tenant_id=tenant_id, user_id=user_id)

    # 1. Texto enviado contém parecer e resumo do dia
    assert len(sender.calls) == 1
    text = sender.calls[0]["text"]
    assert "Parecer editorial teste" in text
    assert "Ontem saíram 2 publicações" in text

    # 2. Persistido no banco: aresta opinion_for + tabela day_summary
    opinion_rows = list(
        db.query("SELECT * FROM opinion_for WHERE out = $tenant;", {"tenant": tenant_id}) or []
    )
    assert len(opinion_rows) == 2
    summary_rows = list(
        db.query("SELECT * FROM day_summary WHERE tenant_id = $tenant;", {"tenant": tenant_id})
        or []
    )
    assert len(summary_rows) == 1
    assert summary_rows[0]["summary"] == "Ontem saíram 2 publicações; o eixo foi IA aplicada"


def test_digest_vertical_opinion_reused_across_channels(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parecer persistido pelo 1º canal é reusado pelo 2º — não recomputa (ADR-0052 §I).
    Prova o compartilhamento entre canais: dois destinos diferentes (Telegram +
    e-mail) disparam na mesma janela; o 2º não chama o LLM para itens que já
    têm parecer, nem para o resumo do dia já persistido."""
    from kubo.workers._digest_editorial import DaySummaryOutput, OpinionOutput
    from kubo.workers.email_digest import EmailDigestWorker

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _CHAT_TOKEN)
    _seed_items(db, tenant_id, user_id, ["sobre IA"])

    class _CountingExecutor:
        def __init__(self) -> None:
            self.opinion_calls = 0
            self.day_summary_calls = 0

        def complete(
            self, instruction: str, untrusted_content: str, response_model: type[Any]
        ) -> Any:
            if response_model is OpinionOutput:
                self.opinion_calls += 1
                return OpinionOutput(opinion="Parecer compartilhado teste")
            if response_model is DaySummaryOutput:
                self.day_summary_calls += 1
                return DaySummaryOutput(summary="Resumo do dia compartilhado")
            raise ValueError(f"unexpected: {response_model}")

    # 1º canal: Telegram — computa parecer + resumo do dia
    executor1 = _CountingExecutor()
    sender1 = _RecordingSender()
    worker1 = TelegramDigestWorker(
        destination=destinations.Destination(
            id=RecordID("destination", "ownertelegram3"),
            name="dono",
            kind="pessoa",
            channel="telegram",
            address="99",
            enabled=True,
            archived_at=None,
            dispatches=0,
        ),
        base_url="https://kubo.test:3900",
        sender=sender1,
        executor=executor1,
    )
    run_worker(db, worker1, config={"max_items": 50}, tenant_id=tenant_id, user_id=user_id)
    assert executor1.opinion_calls == 1  # computou 1 parecer
    assert executor1.day_summary_calls == 1  # computou 1 resumo do dia

    # 2º canal: e-mail — mesmo itens, parecer já persistido pelo Telegram
    executor2 = _CountingExecutor()
    sender2 = _RecordingSender()
    worker2 = EmailDigestWorker(
        destination=destinations.Destination(
            id=RecordID("destination", "owneremail1"),
            name="dono",
            kind="pessoa",
            channel="email",
            address="owner@example.com",
            enabled=True,
            archived_at=None,
            dispatches=0,
        ),
        base_url="https://kubo.test:3900",
        smtp_config=None,
        email_sender=sender2,
        executor=executor2,
    )
    run_worker(db, worker2, config={"max_items": 50}, tenant_id=tenant_id, user_id=user_id)

    # Parecer reusado do banco — 0 chamadas de opinião na 2ª execução
    assert executor2.opinion_calls == 0
    # Resumo do dia já persistido — 0 chamadas na 2ª execução
    assert executor2.day_summary_calls == 0
