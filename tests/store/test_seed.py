"""Contrato do seed de bootstrap das fontes RSS legadas (#108, corte RSS do ADR-0025).

O seed migra as 6 fontes do antigo `schedules.yaml` para Cadastros no DB — idempotente e
NÃO-destrutivo. Estes testes provam as duas garantias que o advisor cravou: (1) semeia as 6
como ativas com as tags certas em ambiente limpo; (2) o coalesce preserva estado do dono
(pausa, edição de tags) quando o seed re-roda sobre um DB já mexido pela UI (#106/#107) —
sem clobber silencioso. Integração: só é exercível contra banco real.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from kubo.errors import ConfigError
from kubo.store import client, knowledge, migrations, settings
from kubo.store import destinations as destination_store
from kubo.store.seed import (
    FEED_CADASTROS,
    main,
    seed_default_settings,
    seed_feed_cadastros,
    seed_owner_destination,
)
from kubo.store.settings import get_settings

pytestmark = pytest.mark.integration

_OWNER_TELEGRAM = "+55 1199999-8888"
_OWNER_TELEGRAM_NORMALIZED = "5511999998888"

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


def test_seed_default_settings_creates_singleton(db: Any) -> None:
    """KUBO-44: ambiente limpo ganha settings:global com defaults operacionais (cron 09:30,
    distribuição não pausada, sem destino padrão)."""
    applied = seed_default_settings(db)

    assert applied is True
    settings_obj = get_settings(db)
    assert settings_obj is not None
    assert settings_obj.digest_cron == "30 9 * * *"
    assert settings_obj.distribution_paused is False
    assert settings_obj.default_destination is None


def test_seed_default_settings_is_once_per_env(db: Any) -> None:
    """O seed de settings roda UMA VEZ por ambiente: a 2ª chamada devolve False e não altera."""
    assert seed_default_settings(db) is True
    settings_obj = get_settings(db)
    assert settings_obj is not None

    # Simula edição do dono pela UI.
    settings.put_settings(
        db, digest_cron="0 20 * * *", distribution_paused=True, default_destination=None
    )

    assert seed_default_settings(db) is False
    current = get_settings(db)
    assert current is not None
    assert current.digest_cron == "0 20 * * *"
    assert current.distribution_paused is True


def test_seed_owner_destination_creates_owner_telegram_and_default(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: ambiente limpo ganha o destino Telegram do dono e ele vira default."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", _OWNER_TELEGRAM)

    seed_default_settings(db)
    applied = seed_owner_destination(db)

    assert applied is True
    settings_obj = get_settings(db)
    assert settings_obj is not None
    assert settings_obj.default_destination is not None

    dest = destination_store.get_destination(db, settings_obj.default_destination)
    assert dest is not None
    assert dest.channel == "telegram"
    assert dest.kind == "pessoa"
    assert dest.name == "owner-telegram"
    assert dest.address == _OWNER_TELEGRAM_NORMALIZED
    assert dest.enabled is True


def test_seed_owner_destination_requires_preexisting_settings(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: seed_owner_destination só escreve o ponteiro em settings já existente."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    assert get_settings(db) is None
    with pytest.raises(ConfigError):
        seed_owner_destination(db)


def test_seed_owner_destination_is_once_per_env(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """KUBO-45: o seed do destino do dono roda uma vez; segunda chamada é no-op."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    seed_default_settings(db)
    assert seed_owner_destination(db) is True
    assert seed_owner_destination(db) is False

    # Apenas um destino existe (não duplicou).
    rows = db.query("SELECT count() FROM destination WHERE channel = 'telegram' GROUP ALL;")
    assert int(rows[0]["count"] if rows else 0) == 1


def test_seed_owner_destination_preserves_owner_edits(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: re-rodar o seed não reverte uma edição manual do dono no default."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    seed_default_settings(db)
    seed_owner_destination(db)
    original = get_settings(db)
    assert original is not None
    original_default = original.default_destination
    assert original_default is not None

    # Dono cria outro destino e muda o default pela UI.
    other = destination_store.create_destination(
        db, name="Outro", kind="pessoa", channel="telegram", address="999999"
    )
    settings.put_settings(
        db,
        digest_cron=original.digest_cron,
        distribution_paused=original.distribution_paused,
        default_destination=other,
    )

    # Re-run do seed não sobrescreve a escolha do dono.
    assert seed_owner_destination(db) is False
    current = get_settings(db)
    assert current is not None
    assert current.default_destination == other


def test_seed_owner_destination_preserves_destination_edits(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: re-rodar o seed não reverte uma edição do próprio destino do dono."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    seed_default_settings(db)
    seed_owner_destination(db)
    original = get_settings(db)
    assert original is not None
    assert original.default_destination is not None

    # Dono edita nome e endereço do destino semeado.
    destination_store.edit_destination(
        db, id=original.default_destination, name="Renomeado", address="999999"
    )

    assert seed_owner_destination(db) is False
    edited = destination_store.get_destination(db, original.default_destination)
    assert edited is not None
    assert edited.name == "Renomeado"
    assert edited.address == "999999"


def test_seed_owner_destination_fails_fast_without_env(db: Any) -> None:
    """KUBO-45: env ausente gera falha clara, sem escrever no banco."""
    assert os.environ.get("KUBO_OWNER_TELEGRAM_CHAT_ID") is None

    with pytest.raises(ConfigError):
        seed_owner_destination(db)

    # Nenhum destino foi criado e settings continua sem default.
    rows = db.query("SELECT count() FROM destination GROUP ALL;")
    assert int(rows[0]["count"] if rows else 0) == 0


def test_main_seeds_settings_owner_destination_and_feeds_idempotently(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: main() roda settings, destino padrão e feeds sem duplicar."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "12345678")

    class _FakeConnect:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> Any:
            return self._inner

        def __exit__(self, *args: Any) -> None:
            pass

    monkeypatch.setattr("kubo.store.seed.client.connect", lambda cfg=None: _FakeConnect(db))

    assert main() == 6

    settings_obj = get_settings(db)
    assert settings_obj is not None
    assert settings_obj.default_destination is not None
    dest = destination_store.get_destination(db, settings_obj.default_destination)
    assert dest is not None
    assert dest.address == "12345678"

    # Segunda execução: feeds continuam 6 (não duplicou), settings/destino no-op.
    assert main() == 0
    assert _count_source(db) == 6
