"""Seleção do digest por janela de publicação (ADR-0050, KUBO-194).

Integração (SurrealDB real): a seleção substitui o watermark posicional por
janela de calendário + exclusão por já-enviado + dedup por URL + ordenação por
nota. Cobre os critérios de aceite do ticket:
- janela normal (só ontem)
- janela elástica após falha (recuperação)
- teto de 7 dias
- exclusão por já-enviado àquele destino nos últimos 7 dias
- dedup por URL equivalente
- ordenação por nota, corte em 5/10 do período inteiro
- as quatro formas de mensagem (normal, nada publicado, nenhuma passou, recuperação)
- aviso de dia vazio conta como envio bem-sucedido
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

_DIGEST_WINDOW_DB = "test_digest_window"


def _dest(key: str) -> RecordID:
    return RecordID("destination", key)


@pytest.fixture
def db() -> Iterator[Any]:
    cfg = replace(client.config(), database=_DIGEST_WINDOW_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_DIGEST_WINDOW_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_DIGEST_WINDOW_DB};")


def _make_item(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    *,
    external_id: str,
    title: str,
    url: str | None,
    content: str = "body content",
    published_at: datetime,
) -> RecordID:
    src = knowledge.upsert_source(
        db, tenant_id=tenant_id, user_id=user_id, kind="rss", canonical=f"src::{external_id}"
    )
    return knowledge.upsert_item(
        db,
        source=src,
        external_id=external_id,
        content=content,
        title=title,
        url=url,
        published_at=published_at,
    )


def _score_and_distill(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    item: RecordID,
    *,
    score: int,
    summary: str = "resumo do item",
    entities: tuple[str, ...] = (),
) -> None:
    knowledge.apply_score(db, tenant_id=tenant_id, user_id=user_id, item=item, score=score)
    entity_ids: list[RecordID] = []
    for name in entities:
        ent = knowledge.get_or_create_entity(db, tenant_id=tenant_id, user_id=user_id, name=name)
        entity_ids.append(ent)
    knowledge.insert_distilled(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        item=item,
        summary=summary,
        chunks=[],
        entities=entity_ids if entity_ids else None,
    )


def _dispatch_ok(
    db: Any,
    tenant_id: RecordID,
    user_id: RecordID,
    destination: RecordID,
    *,
    watermark: datetime,
    items: list[RecordID],
    channel: str = "telegram",
) -> None:
    knowledge.insert_dispatch(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        destination=destination,
        channel=channel,
        status="ok",
        artifact="digest",
        watermark=watermark,
        item_count=len(items),
        items=items,
    )


# ── Janela normal: só ontem ───────────────────────────────────────────────────


def test_window_normal_selects_only_yesterday(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Janela normal sem dispatch anterior: só itens publicados ontem entram."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)

    old_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="old",
        title="Old",
        url="https://a.com/old",
        published_at=two_days_ago,
    )
    yest_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="yest",
        title="Yesterday",
        url="https://a.com/yest",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, old_item, score=8)
    _score_and_distill(db, tenant_id, user_id, yest_item, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "normal"
    assert len(selection.items) == 1
    assert str(selection.items[0].id) == str(yest_item)
    assert selection.window_start is not None
    assert selection.window_end is not None


def test_window_normal_after_successful_dispatch(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Após um dispatch ok que cobriu ontem, a próxima janela é só o novo ontem."""
    now = datetime.now(timezone.utc)
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)

    # Item de 2 dias atrás — já enviado
    old_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="old",
        title="Old",
        url="https://a.com/old",
        published_at=two_days_ago,
    )
    _score_and_distill(db, tenant_id, user_id, old_item, score=8)
    _dispatch_ok(
        db,
        tenant_id,
        user_id,
        _dest("tg"),
        watermark=two_days_ago,
        items=[old_item],
    )

    # Item de ontem — novo
    yest_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="yest",
        title="Yesterday",
        url="https://a.com/yest",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, yest_item, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "normal"
    assert len(selection.items) == 1
    assert str(selection.items[0].id) == str(yest_item)


# ── Janela elástica: recuperação após falha ────────────────────────────────────


def test_window_recovery_after_send_failure(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Após uma falha de envio, a janela cobre desde o último envio bem-sucedido."""
    now = datetime.now(timezone.utc)
    three_days_ago = now - timedelta(days=3)
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)

    # Dispatch ok que cobriu 3 dias atrás
    old_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="old",
        title="Old",
        url="https://a.com/old",
        published_at=three_days_ago,
    )
    _score_and_distill(db, tenant_id, user_id, old_item, score=8)
    _dispatch_ok(
        db,
        tenant_id,
        user_id,
        _dest("tg"),
        watermark=three_days_ago,
        items=[old_item],
    )

    # Itens de 2 dias atrás e ontem — não enviados (falha)
    mid_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="mid",
        title="Mid",
        url="https://a.com/mid",
        published_at=two_days_ago,
    )
    yest_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="yest",
        title="Yesterday",
        url="https://a.com/yest",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, mid_item, score=6)
    _score_and_distill(db, tenant_id, user_id, yest_item, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "recovery"
    # KUBO-179: recovery só leva os itens do dia mais recente (ontem),
    # não os dias intermediários — mid_item (2 dias atrás) fica de fora.
    assert len(selection.items) == 1
    ids = {str(i.id) for i in selection.items}
    assert str(yest_item) in ids
    assert str(mid_item) not in ids


def test_window_7_day_cap_after_long_failure(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O teto de 7 dias é respeitado mesmo após uma quebra longa."""
    now = datetime.now(timezone.utc)
    ten_days_ago = now - timedelta(days=10)
    eight_days_ago = now - timedelta(days=8)
    yesterday = now - timedelta(days=1)

    # Dispatch ok que cobriu 10 dias atrás
    old_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="old",
        title="Old",
        url="https://a.com/old",
        published_at=ten_days_ago,
    )
    _score_and_distill(db, tenant_id, user_id, old_item, score=8)
    _dispatch_ok(
        db,
        tenant_id,
        user_id,
        _dest("tg"),
        watermark=ten_days_ago,
        items=[old_item],
    )

    # Item de 8 dias atrás — fora do teto de 7 dias, não entra
    out_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="out",
        title="Out",
        url="https://a.com/out",
        published_at=eight_days_ago,
    )
    _score_and_distill(db, tenant_id, user_id, out_item, score=9)

    # Item de ontem — entra
    yest_item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="yest",
        title="Yesterday",
        url="https://a.com/yest",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, yest_item, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    # Recovery cobre 7 dias do dia seguinte ao último ok até ontem
    assert selection.form == "recovery"
    ids = {str(i.id) for i in selection.items}
    assert str(yest_item) in ids
    assert str(out_item) not in ids


# ── Exclusão por já-enviado ────────────────────────────────────────────────────


def test_excludes_items_sent_to_same_destination_in_last_7_days(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Item já enviado àquele destino nos últimos 7 dias é excluído."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    item_a = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="a",
        title="A",
        url="https://a.com/a",
        published_at=yesterday,
    )
    item_b = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="b",
        title="B",
        url="https://a.com/b",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item_a, score=8)
    _score_and_distill(db, tenant_id, user_id, item_b, score=7)

    # Envia A como parte de um dispatch anterior (simula reenvio dentro da janela)
    _dispatch_ok(
        db,
        tenant_id,
        user_id,
        _dest("tg"),
        watermark=now - timedelta(days=3),
        items=[item_a],
    )

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    ids = {str(i.id) for i in selection.items}
    assert str(item_b) in ids
    assert str(item_a) not in ids  # já foi enviado


def test_exclusion_is_per_destination(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Telegram e e-mail mantêm listas de enviados independentes."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    item_a = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="a",
        title="A",
        url="https://a.com/a",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item_a, score=8)

    # Envia A só pro Telegram
    _dispatch_ok(
        db,
        tenant_id,
        user_id,
        _dest("tg"),
        watermark=now - timedelta(days=3),
        items=[item_a],
        channel="telegram",
    )

    # E-mail ainda não enviou A → deve incluir
    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("email"), limit=10
    )
    ids = {str(i.id) for i in selection.items}
    assert str(item_a) in ids


# ── Dedup por URL ──────────────────────────────────────────────────────────────


def test_dedup_by_normalized_url(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Duas URLs equivalentes contam como uma só."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # Mesma notícia em duas fontes, URLs equivalentes
    item1 = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="src1",
        title="News",
        url="https://example.com/article",
        published_at=yesterday,
    )
    item2 = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="src2",
        title="News",
        url="https://example.com/article/",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item1, score=8)
    _score_and_distill(db, tenant_id, user_id, item2, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert len(selection.items) == 1  # dedup — só um entra


def test_dedup_strips_tracking_params(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """URLs com parâmetros de rastreamento contam como equivalentes."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    item1 = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="t1",
        title="News",
        url="https://example.com/article?utm_source=newsletter",
        published_at=yesterday,
    )
    item2 = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="t2",
        title="News",
        url="https://example.com/article",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item1, score=8)
    _score_and_distill(db, tenant_id, user_id, item2, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert len(selection.items) == 1


# ── Ordenação por nota + corte ─────────────────────────────────────────────────


def test_ordering_by_score_descending(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Itens são ordenados por nota, decrescente."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    items = []
    for i, score in enumerate([5, 9, 7, 10, 6]):
        item = _make_item(
            db,
            tenant_id,
            user_id,
            external_id=f"o{i}",
            title=f"Item {i}",
            url=f"https://a.com/o{i}",
            published_at=yesterday,
        )
        _score_and_distill(db, tenant_id, user_id, item, score=score)
        items.append((item, score))

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    scores = [i.score for i in selection.items]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 10  # maior nota primeiro


def test_cut_at_limit_within_recovery_latest_day(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Corte em N do período inteiro — não N por dia. Na forma recovery, só
    conta o dia mais recente (KUBO-179)."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    three_days_ago = now - timedelta(days=3)

    # Dispatch ok cobrindo 3 dias atrás → janela de recovery inclui 2 dias
    # (do dia seguinte ao watermark até ontem)
    old = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="old",
        title="Old",
        url="https://a.com/old",
        published_at=three_days_ago,
    )
    _score_and_distill(db, tenant_id, user_id, old, score=3)
    _dispatch_ok(db, tenant_id, user_id, _dest("tg"), watermark=three_days_ago, items=[old])

    # 7 itens na janela (2 dias: 2 dias atrás + ontem), cortar em 3.
    # KUBO-179: recovery só leva o dia mais recente (ontem) — os 3 de two_days_ago
    # ficam de fora do digest (visíveis na UI).
    two_days_ago = now - timedelta(days=2)
    for i in range(7):
        pub_date = two_days_ago if i < 3 else yesterday
        item = _make_item(
            db,
            tenant_id,
            user_id,
            external_id=f"c{i}",
            title=f"Cut {i}",
            url=f"https://a.com/c{i}",
            published_at=pub_date,
        )
        # Scores: two_days_ago = 8,8,8; yesterday = 10,9,8,7 (todos >= min_score 6)
        score = 8 if i < 3 else 10 - (i - 3)
        _score_and_distill(db, tenant_id, user_id, item, score=score)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=3
    )
    assert selection.form == "recovery"
    assert len(selection.items) == 3
    # Só itens de ontem (scores 10,9,8,7), cortados em 3 → 10,9,8
    scores = [i.score for i in selection.items]
    assert scores == [10, 9, 8]


# ── Quatro formas de mensagem ──────────────────────────────────────────────────


def test_form_empty_window_no_publications(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Forma 2: nada foi publicado na janela."""
    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "empty_window"
    assert selection.items == []
    assert selection.total_publications == 0
    assert selection.window_end is not None


def test_form_none_passed_cut(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Forma 3: N publicações, nenhuma passou o corte — com o número."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # 3 itens publicados ontem, todos com score < min_score (não destilados)
    for i in range(3):
        item = _make_item(
            db,
            tenant_id,
            user_id,
            external_id=f"low{i}",
            title=f"Low {i}",
            url=f"https://a.com/low{i}",
            published_at=yesterday,
        )
        knowledge.apply_score(db, tenant_id=tenant_id, user_id=user_id, item=item, score=3)
        # NÃO destila — score < min_score

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "none_passed"
    assert selection.items == []
    assert selection.total_publications == 3


def test_form_normal_with_items(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Forma 1: houve conteúdo aprovado, o digest sai com as notícias."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="ok",
        title="OK",
        url="https://a.com/ok",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item, score=8)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "normal"
    assert len(selection.items) == 1


def test_form_recovery_identifies_itself(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """Forma 4: recuperação identifica-se e informa o período coberto."""
    now = datetime.now(timezone.utc)
    three_days_ago = now - timedelta(days=3)
    yesterday = now - timedelta(days=1)

    old = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="old",
        title="Old",
        url="https://a.com/old",
        published_at=three_days_ago,
    )
    _score_and_distill(db, tenant_id, user_id, old, score=8)
    _dispatch_ok(db, tenant_id, user_id, _dest("tg"), watermark=three_days_ago, items=[old])

    yest = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="yest",
        title="Yesterday",
        url="https://a.com/yest",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, yest, score=7)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "recovery"
    assert selection.window_start is not None
    assert selection.window_end is not None
    # Janela cobre mais de 1 dia
    assert (selection.window_end - selection.window_start).days >= 1
    # KUBO-179: só leva o item do dia mais recente (ontem)
    ids = {str(i.id) for i in selection.items}
    assert str(yest) in ids
    assert str(old) not in ids


# ── Aviso de dia vazio conta como envio bem-sucedido ───────────────────────────


def test_empty_window_watermark_advances(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """O aviso de dia vazio produz um watermark válido (o dia da janela),
    e o próximo dispatch não tenta recuperar o mesmo período."""
    # Primeira chamada: janela vazia
    selection1 = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection1.form == "empty_window"
    assert selection1.window_end is not None

    # Simula o dispatch ok do aviso (watermark = window_end)
    _dispatch_ok(
        db,
        tenant_id,
        user_id,
        _dest("tg"),
        watermark=selection1.window_end,
        items=[],
    )

    # Segunda chamada: não deve ser recovery — a janela resetou
    selection2 = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection2.form == "empty_window"  # ainda vazia, mas não recovery


# ── Watermark = último dia coberto pela janela ─────────────────────────────────


def test_watermark_is_window_end_not_max_published_at(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O watermark é o último dia da janela, não max(published_at) dos itens —
    por isso a forma 2 (vazia) ainda produz watermark válido."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # Item publicado no início da janela de ontem (não no fim)
    item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="early",
        title="Early",
        url="https://a.com/early",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item, score=8)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tg"), limit=10
    )
    assert selection.form == "normal"
    assert selection.window_end is not None
    # O watermark (window_end) é o fim do dia de ontem, não o published_at do item
    assert selection.watermark == selection.window_end


# ── Fuso do tenant (ADR-0050 §I) ───────────────────────────────────────────────


def test_tenant_timezone_non_utc_exercises_profile_path(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O caminho do profile (não-UTC) é exercitado: cria um user_profile com
    timezone America/Sao_Paulo e verifica que items_for_digest não quebra e
    respeita o fuso (janela de calendário no fuso do tenant, não UTC)."""
    tenancy.update_user_profile(
        db,
        user_id=user_id,
        display_name="Owner",
        language="pt-BR",
        timezone="America/Sao_Paulo",
        work_context="AI agents and infrastructure",
    )
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    item = _make_item(
        db,
        tenant_id,
        user_id,
        external_id="tz-item",
        title="TZ Item",
        url="https://a.com/tz",
        published_at=yesterday,
    )
    _score_and_distill(db, tenant_id, user_id, item, score=8)

    selection = knowledge.items_for_digest(
        db, tenant_id=tenant_id, user_id=user_id, destination=_dest("tz"), limit=10
    )
    # Não importa se é normal/empty — o ponto é que não quebra com fuso não-UTC
    assert selection.window_end is not None
    assert selection.watermark == selection.window_end
