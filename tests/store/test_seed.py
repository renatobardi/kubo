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


def test_seed_is_idempotent(db: Any) -> None:
    """Re-rodar o seed é no-op seguro (o índice UNIQUE(kind,canonical) já barraria dup, mas o
    lookup-first nem tenta): 6 fontes depois de rodar duas vezes, não 12."""
    seed_feed_cadastros(db)
    seed_feed_cadastros(db)

    assert _count_source(db) == 6


def test_seed_preserves_owner_pause_and_tag_edits(db: Any) -> None:
    """O coração da correção do advisor: o #107 está vivo, então entre um deploy e outro o dono
    pode pausar ou re-tagear uma fonte pela UI. O seed re-rodando NÃO pode reverter isso — o
    coalesce preenche lacuna, nunca sobrescreve. Prova: pauso uma e edito as tags de outra,
    re-semeio, e ambas as ações do dono sobrevivem."""
    seed_feed_cadastros(db)
    active = {s.canonical: s for s in knowledge.active_sources(db, kind="rss")}
    paused = active["https://importai.substack.com/feed"]
    retagged = active["https://www.semianalysis.com/feed"]
    knowledge.set_source_enabled(db, id=paused.id, enabled=False)
    knowledge.edit_source(
        db,
        id=retagged.id,
        title="SemiAnalysis",
        tags=["dono-editou"],
        canonical="https://www.semianalysis.com/feed",
    )

    seed_feed_cadastros(db)

    # A pausa sobrevive: a fonte pausada saiu dos ativos (não voltou a enabled=true).
    still_active = {s.canonical: s for s in knowledge.active_sources(db, kind="rss")}
    assert "https://importai.substack.com/feed" not in still_active
    # A edição de tags sobrevive: o seed não sobrescreveu as tags do dono pelas do bootstrap.
    assert still_active["https://www.semianalysis.com/feed"].tags == ["dono-editou"]


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
