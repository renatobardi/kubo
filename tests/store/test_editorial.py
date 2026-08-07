"""Persistência de conteúdo editorial (ADR-0052, KUBO-195).

Parecer por (item, tenant) e resumo do dia por (dia, tenant) — as duas tabelas
extra-spec que o ADR-0052 re-abre. Integração (SurrealDB real): testa CRUD,
idempotência, isolamento por tenant e reuso entre canais.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from surrealdb import RecordID

from kubo.store import client, knowledge, migrations, tenancy

pytestmark = pytest.mark.integration

_EDITORIAL_DB = "test_editorial"


@pytest.fixture
def db() -> Iterator[Any]:
    cfg = replace(client.config(), database=_EDITORIAL_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_EDITORIAL_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_EDITORIAL_DB};")


def _make_item(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    *,
    external_id: str,
    title: str = "Title",
    url: str | None = "https://a.com/article",
    published_at: datetime | None = None,
) -> RecordID:
    if published_at is None:
        published_at = datetime.now(timezone.utc) - timedelta(days=1)
    src = knowledge.upsert_source(
        db, tenant_id=tenant_id, user_id=user_id, kind="rss", canonical=f"src::{external_id}"
    )
    return knowledge.upsert_item(
        db,
        source=src,
        external_id=external_id,
        content="body content",
        title=title,
        url=url,
        published_at=published_at,
    )


# ── Parecer (opinion_for: item → tenant) ───────────────────────────────────────


class TestOpinion:
    def test_upsert_and_get_opinion(self, db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
        """upsert_opinion grava e get_opinion lê de volta."""
        item = _make_item(db, tenant_id, user_id, external_id="op-1")
        knowledge.upsert_opinion(
            db, tenant_id=tenant_id, user_id=user_id, item=item, opinion="Importante porque X"
        )
        result = knowledge.get_opinion(db, tenant_id=tenant_id, item=item)
        assert result == "Importante porque X"

    def test_upsert_opinion_is_idempotent(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """Reescrever o parecer sobrescreve, não duplica (last-wins, como apply_score)."""
        item = _make_item(db, tenant_id, user_id, external_id="op-2")
        knowledge.upsert_opinion(
            db, tenant_id=tenant_id, user_id=user_id, item=item, opinion="Primeiro"
        )
        knowledge.upsert_opinion(
            db, tenant_id=tenant_id, user_id=user_id, item=item, opinion="Segundo"
        )
        assert knowledge.get_opinion(db, tenant_id=tenant_id, item=item) == "Segundo"

    def test_get_opinion_returns_none_when_absent(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """Item sem parecer → None (não erro)."""
        item = _make_item(db, tenant_id, user_id, external_id="op-3")
        assert knowledge.get_opinion(db, tenant_id=tenant_id, item=item) is None

    def test_get_opinions_batch(self, db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
        """get_opinions lê múltiplos pareceres de uma vez (chave = str(item_id))."""
        item_a = _make_item(db, tenant_id, user_id, external_id="op-batch-a")
        item_b = _make_item(db, tenant_id, user_id, external_id="op-batch-b")
        item_c = _make_item(db, tenant_id, user_id, external_id="op-batch-c")
        knowledge.upsert_opinion(
            db, tenant_id=tenant_id, user_id=user_id, item=item_a, opinion="Parecer A"
        )
        knowledge.upsert_opinion(
            db, tenant_id=tenant_id, user_id=user_id, item=item_b, opinion="Parecer B"
        )
        opinions = knowledge.get_opinions(
            db,
            tenant_id=tenant_id,
            items=[item_a, item_b, item_c],
        )
        assert opinions[str(item_a)] == "Parecer A"
        assert opinions[str(item_b)] == "Parecer B"
        assert str(item_c) not in opinions

    def test_opinion_isolated_per_tenant(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """Parecer do tenant A não é visível pelo tenant B."""
        item = _make_item(db, tenant_id, user_id, external_id="op-iso")
        knowledge.upsert_opinion(
            db, tenant_id=tenant_id, user_id=user_id, item=item, opinion="Do tenant A"
        )

        # Cria segundo tenant
        other_user = tenancy.create_user(db, firebase_uid="editorial-other-uid")
        other_tenant = tenancy.create_tenant(db, name="Other Tenant", owner_user_id=other_user.id)
        assert knowledge.get_opinion(db, tenant_id=other_tenant.id, item=item) is None


# ── Resumo do dia (day_summary) ────────────────────────────────────────────────


class TestDaySummary:
    def test_upsert_and_get_day_summary(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """upsert_day_summary grava e get_day_summary lê de volta."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        knowledge.upsert_day_summary(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            day=yesterday,
            summary="Ontem saíram 5 publicações; o eixo foi IA agents",
            publication_count=5,
        )
        result = knowledge.get_day_summary(db, tenant_id=tenant_id, day=yesterday)
        assert result is not None
        assert result.summary == "Ontem saíram 5 publicações; o eixo foi IA agents"
        assert result.publication_count == 5

    def test_upsert_day_summary_is_idempotent(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """Reescrever o resumo sobrescreve, não duplica (fallback race-safe)."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        knowledge.upsert_day_summary(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            day=yesterday,
            summary="Primeiro",
            publication_count=3,
        )
        knowledge.upsert_day_summary(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            day=yesterday,
            summary="Segundo",
            publication_count=3,
        )
        result = knowledge.get_day_summary(db, tenant_id=tenant_id, day=yesterday)
        assert result is not None
        assert result.summary == "Segundo"

    def test_get_day_summary_returns_none_when_absent(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """Dia sem resumo → None (não erro)."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        assert knowledge.get_day_summary(db, tenant_id=tenant_id, day=yesterday) is None

    def test_day_summary_isolated_per_tenant(
        self, db: Any, tenant_id: RecordID, user_id: RecordID
    ) -> None:
        """Resumo do tenant A não é visível pelo tenant B."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        knowledge.upsert_day_summary(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            day=yesterday,
            summary="Do tenant A",
            publication_count=2,
        )

        other_user = tenancy.create_user(db, firebase_uid="editorial-other-uid-2")
        other_tenant = tenancy.create_tenant(db, name="Other Tenant 2", owner_user_id=other_user.id)
        assert knowledge.get_day_summary(db, tenant_id=other_tenant.id, day=yesterday) is None
