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
from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.runtime.catalog_defaults import DEFAULT_INTEGRATIONS
from kubo.store import catalog, client, knowledge, migrations, settings
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


class _FakeConnect:
    """Fake de `client.connect()`: `__enter__` devolve a conexão de teste já aberta pela
    fixture `db`, em vez de abrir uma nova — permite testar `main()` (que faz `with
    client.connect() as db:`) contra o banco efêmero do teste."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __enter__(self) -> Any:
        return self._inner

    def __exit__(self, *args: Any) -> None:
        pass


def _count_source(db: Any) -> int:
    rows = db.query("SELECT count() FROM source GROUP ALL;")
    return int(rows[0]["count"]) if rows else 0


def test_seed_creates_six_active_rss_feeds_with_tags(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """Ambiente limpo: o seed cria as 6 fontes como Cadastros rss ATIVOS, com o title e as tags
    do `schedules.yaml` — é o que o sweep varre e o que reproduz a coleta legada sem regressão."""
    processed = seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id)

    assert processed == 6
    active = knowledge.active_sources(db, tenant_id=tenant_id, user_id=user_id, kind="rss")
    assert len(active) == 6
    by_canonical = {s.canonical: s for s in active}
    openai = by_canonical["https://openai.com/news/rss.xml"]
    assert openai.title == "OpenAI News"
    assert openai.tags == ["ai", "openai", "confiavel"]


def test_seed_is_once_per_env_no_op_on_second_run(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """O seed roda UMA VEZ por ambiente (marcador): a 2ª chamada devolve 0 e não toca nada —
    6 fontes depois de rodar duas vezes, não 12."""
    assert seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id) == 6
    assert seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id) == 0

    assert _count_source(db) == 6


def test_seed_first_run_coalesces_owner_pause_and_title(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """No 1º seed, o coalesce protege estado que o dono já mudou ANTES do bootstrap (ambiente
    legado onde o #106/#107 já rodou): pausa e título editado sobrevivem, e as tags legadas
    (`[]`) são preenchidas — este é o único momento em que `[]` significa 'legado'."""
    rid = knowledge.create_source(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        kind="rss",
        canonical="https://openai.com/news/rss.xml",
        title="Meu título",
    )
    knowledge.set_source_enabled(db, tenant_id=tenant_id, user_id=user_id, id=rid, enabled=False)

    assert seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id) == 6

    got = knowledge.get_source(db, rid, tenant_id=tenant_id, user_id=user_id)
    assert got is not None
    assert got.title == "Meu título"  # coalesce title ?? $title → edição do dono sobrevive
    assert got.enabled is False  # coalesce enabled ?? true → pausa do dono sobrevive
    assert got.tags == ["ai", "openai", "confiavel"]  # tags legadas ([]) preenchidas no bootstrap


def test_seed_once_per_env_preserves_later_tag_clear(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """A correção do CodeRabbit (#116): depois do bootstrap, o dono limpa TODAS as tags de uma
    fonte pela UI (`tags=[]` intencional). Um segundo deploy NÃO pode refilar as tags legadas —
    o marcador faz o seed pular, então o `[]` do dono sobrevive. É o caso que o coalesce sozinho
    não cobria (`[]` ambíguo: legado vs limpo-de-propósito), resolvido por rodar só uma vez."""
    seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id)
    tgt = {
        s.canonical: s
        for s in knowledge.active_sources(db, tenant_id=tenant_id, user_id=user_id, kind="rss")
    }["https://openai.com/news/rss.xml"]
    knowledge.edit_source(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        id=tgt.id,
        title="OpenAI News",
        tags=[],
        canonical="https://openai.com/news/rss.xml",
    )

    # marcador presente → pula
    assert seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id) == 0

    (again,) = [
        s
        for s in knowledge.active_sources(db, tenant_id=tenant_id, user_id=user_id, kind="rss")
        if s.canonical == "https://openai.com/news/rss.xml"
    ]
    assert again.tags == []  # o 'limpar tudo' do dono sobreviveu ao re-deploy


def test_seed_reuses_legacy_sha256_record(db: Any, tenant_id: RecordID, user_id: RecordID) -> None:
    """No kubo-test as 6 já existem com id sha256(canonical) (legado da coleta) e tags=[]. O
    seed deve REUSAR esse record (backfill das tags), nunca criar um segundo — o lookup-first
    por (tenant, kind, canonical) resolve o id existente qualquer que seja sua forma."""
    canonical = FEED_CADASTROS[0].canonical
    legacy_id = knowledge._rid("source", canonical)
    db.query(
        "CREATE $r SET tenant_id = $t, kind = 'rss', canonical = $c, title = 'OpenAI News', "
        "enabled = true, tags = [];",
        {"r": legacy_id, "t": tenant_id, "c": canonical},
    )

    seed_feed_cadastros(db, tenant_id=tenant_id, user_id=user_id)

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
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: ambiente limpo ganha o destino Telegram do dono e ele vira default."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", _OWNER_TELEGRAM)

    seed_default_settings(db)
    applied = seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id)

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
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: seed_owner_destination só escreve o ponteiro em settings já existente."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    assert get_settings(db) is None
    with pytest.raises(ConfigError):
        seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id)


def test_seed_owner_destination_is_once_per_env(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: o seed do destino do dono roda uma vez; segunda chamada é no-op."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    seed_default_settings(db)
    assert seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id) is True
    assert seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id) is False

    # Apenas um destino existe (não duplicou).
    rows = db.query("SELECT count() FROM destination WHERE channel = 'telegram' GROUP ALL;")
    assert int(rows[0]["count"] if rows else 0) == 1


def test_seed_owner_destination_preserves_owner_edits(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: re-rodar o seed não reverte uma edição manual do dono no default."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    seed_default_settings(db)
    seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id)
    original = get_settings(db)
    assert original is not None
    original_default = original.default_destination
    assert original_default is not None

    # Dono cria outro destino e muda o default pela UI.
    other = destination_store.create_destination(
        db,
        name="Outro",
        kind="pessoa",
        channel="telegram",
        address="999999",
        tenant_id=tenant_id,
    )
    settings.put_settings(
        db,
        digest_cron=original.digest_cron,
        distribution_paused=original.distribution_paused,
        default_destination=other,
    )

    # Re-run do seed não sobrescreve a escolha do dono.
    assert seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id) is False
    current = get_settings(db)
    assert current is not None
    assert current.default_destination == other


def test_seed_owner_destination_preserves_destination_edits(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: re-rodar o seed não reverte uma edição do próprio destino do dono."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "123456")

    seed_default_settings(db)
    seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id)
    original = get_settings(db)
    assert original is not None
    assert original.default_destination is not None

    # Dono edita nome e endereço do destino semeado.
    destination_store.edit_destination(
        db, id=original.default_destination, name="Renomeado", address="999999"
    )

    assert seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id) is False
    edited = destination_store.get_destination(db, original.default_destination)
    assert edited is not None
    assert edited.name == "Renomeado"
    assert edited.address == "999999"


def test_seed_owner_destination_fails_fast_without_env(
    db: Any, tenant_id: RecordID, user_id: RecordID
) -> None:
    """KUBO-45: env ausente gera falha clara, sem escrever no banco."""
    assert os.environ.get("KUBO_OWNER_TELEGRAM_CHAT_ID") is None

    with pytest.raises(ConfigError):
        seed_owner_destination(db, tenant_id=tenant_id, user_id=user_id)

    # Nenhum destino foi criado e settings continua sem default.
    rows = db.query("SELECT count() FROM destination GROUP ALL;")
    assert int(rows[0]["count"] if rows else 0) == 0


def test_main_seeds_settings_owner_destination_and_feeds_idempotently(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-45: main() roda settings, destino padrão e feeds sem duplicar."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "12345678")

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


def test_main_seeds_catalog_for_tenant_that_predates_adr_0042(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-191: `seed_catalog` só rodava na CRIAÇÃO do tenant (`create_tenant`). Um tenant
    que já existia antes do ADR-0042 nunca passou por esse caminho — `catalog_integration`
    fica vazio para sempre, e todo worker que declara integração morre com ConfigError
    ('rss'/'telegram' não existe no catálogo). `main()` (o passo de deploy) precisa fechar
    essa lacuna, sem depender de o tenant ser novo."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "12345678")

    # Simula o tenant legado: `create_tenant` (via a fixture) já semeou o catálogo — apaga
    # tudo para reproduzir o estado real de `breakglass` (kubo-test, 2026-08-03/04).
    db.query("DELETE catalog_integration WHERE tenant_id = $t;", {"t": tenant_id})
    db.query("DELETE catalog_persona WHERE tenant_id = $t;", {"t": tenant_id})
    db.query("DELETE catalog_flow_template WHERE tenant_id = $t;", {"t": tenant_id})
    assert catalog.list_integrations(db, tenant_id=tenant_id, user_id=user_id) == []

    monkeypatch.setattr("kubo.store.seed.client.connect", lambda cfg=None: _FakeConnect(db))
    monkeypatch.setattr(
        "kubo.scheduler.tenant.resolve_scheduler_tenant_and_user",
        lambda _db: (tenant_id, user_id),
    )

    main()

    integrations = catalog.list_integrations(db, tenant_id=tenant_id, user_id=user_id)
    names = {i["name"] for i in integrations}
    assert names == {i["name"] for i in DEFAULT_INTEGRATIONS}
    assert "rss" in names
    assert "telegram" in names


def test_main_catalog_seed_is_idempotent_and_preserves_edits(
    db: Any, tenant_id: RecordID, user_id: RecordID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KUBO-191: re-rodar `main()` (todo deploy roda) não duplica o catálogo nem sobrescreve
    uma edição do dono — mesma garantia de coalesce que `seed_catalog` já tem.

    Parte do estado inicial VAZIO (como `test_main_seeds_catalog_for_tenant_that_predates_
    adr_0042`): se não fosse, o 1º `main()` não exerceria a semeadura nova, e a asserção de
    coalesce passaria mesmo sem `main()` tocar o catálogo — teste vácuo."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "12345678")
    db.query("DELETE catalog_integration WHERE tenant_id = $t;", {"t": tenant_id})

    monkeypatch.setattr("kubo.store.seed.client.connect", lambda cfg=None: _FakeConnect(db))
    monkeypatch.setattr(
        "kubo.scheduler.tenant.resolve_scheduler_tenant_and_user",
        lambda _db: (tenant_id, user_id),
    )

    main()
    rss = catalog.get_integration(db, tenant_id=tenant_id, name="rss", user_id=user_id)
    assert rss is not None
    edited = dict(rss, base_url="https://meu-proxy-rss.example")
    catalog.upsert_integration(db, tenant_id=tenant_id, user_id=user_id, integration=edited)

    main()

    still_there = catalog.get_integration(db, tenant_id=tenant_id, name="rss", user_id=user_id)
    assert still_there is not None
    assert still_there["base_url"] == "https://meu-proxy-rss.example"
    integrations = catalog.list_integrations(db, tenant_id=tenant_id, user_id=user_id)
    names = [i["name"] for i in integrations]
    assert len(names) == len(set(names))  # sem duplicata
