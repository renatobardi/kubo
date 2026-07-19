"""Contrato do seed de bootstrap das fontes RSS legadas (#108, corte RSS do ADR-0025).

O seed migra as 6 fontes do antigo `schedules.yaml` para Cadastros no DB — idempotente e
NÃO-destrutivo. Estes testes provam as duas garantias que o advisor cravou: (1) semeia as 6
como ativas com as tags certas em ambiente limpo; (2) o coalesce preserva estado do dono
(pausa, edição de tags) quando o seed re-roda sobre um DB já mexido pela UI (#106/#107) —
sem clobber silencioso. Integração: só é exercível contra banco real.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.store import client, knowledge, migrations
from kubo.store.seed import FEED_CADASTROS, seed_feed_cadastros

pytestmark = pytest.mark.integration

_SEED_DB = "test_seed"


@pytest.fixture
def db() -> Iterator[Any]:
    """Database próprio do teste, migrado do zero e limpo depois — sem o seed (não é migração)."""
    cfg = replace(client.config(), database=_SEED_DB)
    with client.connect(cfg) as conn:
        conn.query(f"REMOVE DATABASE IF EXISTS {_SEED_DB};")
        conn.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(conn)
        yield conn
        conn.query(f"REMOVE DATABASE IF EXISTS {_SEED_DB};")


def _count_source(db: Any) -> int:
    rows = db.query("SELECT count() FROM source GROUP ALL;")
    return int(rows[0]["count"]) if rows else 0


def test_seed_creates_six_active_rss_feeds_with_tags(db: Any) -> None:
    """Ambiente limpo: o seed cria as 6 fontes como Cadastros rss ATIVOS, com o title e as tags
    do `schedules.yaml` — é o que o sweep varre e o que reproduz a coleta legada sem regressão."""
    processed = seed_feed_cadastros(db)

    assert processed == 6
    active = knowledge.active_sources(db, kind="rss")
    assert len(active) == 6
    by_canonical = {s.canonical: s for s in active}
    openai = by_canonical["https://openai.com/news/rss.xml"]
    assert openai.title == "OpenAI News"
    assert openai.tags == ["ai", "openai", "confiavel"]


def test_seed_is_once_per_env_no_op_on_second_run(db: Any) -> None:
    """O seed roda UMA VEZ por ambiente (marcador): a 2ª chamada devolve 0 e não toca nada —
    6 fontes depois de rodar duas vezes, não 12."""
    assert seed_feed_cadastros(db) == 6
    assert seed_feed_cadastros(db) == 0

    assert _count_source(db) == 6


def test_seed_first_run_coalesces_owner_pause_and_title(db: Any) -> None:
    """No 1º seed, o coalesce protege estado que o dono já mudou ANTES do bootstrap (ambiente
    legado onde o #106/#107 já rodou): pausa e título editado sobrevivem, e as tags legadas
    (`[]`) são preenchidas — este é o único momento em que `[]` significa 'legado'."""
    rid = knowledge.create_source(
        db, kind="rss", canonical="https://openai.com/news/rss.xml", title="Meu título"
    )
    knowledge.set_source_enabled(db, id=rid, enabled=False)

    assert seed_feed_cadastros(db) == 6

    got = knowledge.get_source(db, rid)
    assert got is not None
    assert got.title == "Meu título"  # coalesce title ?? $title → edição do dono sobrevive
    assert got.enabled is False  # coalesce enabled ?? true → pausa do dono sobrevive
    assert got.tags == ["ai", "openai", "confiavel"]  # tags legadas ([]) preenchidas no bootstrap


def test_seed_once_per_env_preserves_later_tag_clear(db: Any) -> None:
    """A correção do CodeRabbit (#116): depois do bootstrap, o dono limpa TODAS as tags de uma
    fonte pela UI (`tags=[]` intencional). Um segundo deploy NÃO pode refilar as tags legadas —
    o marcador faz o seed pular, então o `[]` do dono sobrevive. É o caso que o coalesce sozinho
    não cobria (`[]` ambíguo: legado vs limpo-de-propósito), resolvido por rodar só uma vez."""
    seed_feed_cadastros(db)
    tgt = {s.canonical: s for s in knowledge.active_sources(db, kind="rss")}[
        "https://openai.com/news/rss.xml"
    ]
    knowledge.edit_source(
        db, id=tgt.id, title="OpenAI News", tags=[], canonical="https://openai.com/news/rss.xml"
    )

    assert seed_feed_cadastros(db) == 0  # marcador presente → pula

    (again,) = [
        s
        for s in knowledge.active_sources(db, kind="rss")
        if s.canonical == "https://openai.com/news/rss.xml"
    ]
    assert again.tags == []  # o 'limpar tudo' do dono sobreviveu ao re-deploy


def test_seed_reuses_legacy_sha256_record(db: Any) -> None:
    """No kubo-test as 6 já existem com id sha256(canonical) (legado da coleta) e tags=[]. O
    seed deve REUSAR esse record (backfill das tags), nunca criar um segundo — o lookup-first
    por (kind, canonical) resolve o id existente qualquer que seja sua forma."""
    canonical = FEED_CADASTROS[0].canonical
    legacy_id = knowledge._rid("source", canonical)
    db.query(
        "CREATE $r SET kind = 'rss', canonical = $c, title = 'OpenAI News', "
        "enabled = true, tags = [];",
        {"r": legacy_id, "c": canonical},
    )

    seed_feed_cadastros(db)

    rows = db.query("SELECT id, tags FROM source WHERE canonical = $c;", {"c": canonical})
    assert len(rows) == 1
    assert str(rows[0]["id"]) == str(legacy_id)
    assert rows[0]["tags"] == ["ai", "openai", "confiavel"]
