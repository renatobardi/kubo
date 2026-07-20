"""Seed de bootstrap das fontes RSS legadas como Cadastros (#108, corte RSS do ADR-0025).

Passo de deploy `python -m kubo.store.seed`, IRMÃO de `python -m kubo.store.migrations`
(roda logo depois no deploy-remote.sh). NÃO é migração: migração é SCHEMA (roda no fixture
de teste, poluiria toda DB com estas 6 linhas), seed é DADO (roda só no deploy). Idempotente
e não-destrutivo (coalesce em `upsert_seed_source`) — re-rodar a cada deploy é no-op seguro.

Estas 6 URLs foram, até este corte, a lista de feeds do `schedules.yaml`. O ADR-0025 tira a
lista do YAML e a coloca no DB (o banco diz O QUÊ coletar). Este seed é o bootstrap histórico
dessa migração: roda 1x por ambiente, o DB passa a ser a fonte-de-verdade e o dono edita pela
UI (#106) daqui pra frente. Em ambiente novo (restore do zero) reproduz as 6; no kubo-test,
onde já existem da coleta, só completa as `tags` (que viviam só no YAML) sem tocar edições.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import client
from kubo.store import destinations as destination_store
from kubo.store import settings as settings_store
from kubo.store.knowledge import upsert_seed_source

_log = structlog.get_logger().bind(worker="seed-cli")


@dataclass(frozen=True)
class FeedSeed:
    """Uma fonte RSS a semear: canonical (a URL do feed), title (rótulo) e tags (rótulo dos
    itens, via metadata do worker `feed`) — os três campos que migram do `schedules.yaml`."""

    canonical: str
    title: str
    tags: list[str]


# As 6 fontes reais (plano 0005), com canonical+title+tags idênticos ao que estava no
# `schedules.yaml` — para o sweep reproduzir a coleta atual sem mudança de comportamento.
FEED_CADASTROS: list[FeedSeed] = [
    FeedSeed(
        "https://openai.com/news/rss.xml",
        "OpenAI News",
        ["ai", "openai", "confiavel"],
    ),
    FeedSeed(
        "https://deepmind.google/blog/rss.xml",
        "Google DeepMind Blog",
        ["ai", "deepmind", "confiavel"],
    ),
    FeedSeed(
        "https://huggingface.co/blog/feed.xml",
        "Hugging Face Blog",
        ["ai", "dev", "huggingface"],
    ),
    FeedSeed(
        "https://github.blog/ai-and-ml/feed/",
        "GitHub Blog — AI & ML",
        ["ai", "dev", "github"],
    ),
    FeedSeed(
        "https://importai.substack.com/feed",
        "Import AI",
        ["ai", "newsletter"],
    ),
    FeedSeed(
        "https://www.semianalysis.com/feed",
        "SemiAnalysis",
        ["ai", "hardware", "semianalysis"],
    ),
]


def _ensure_marker_table(db: Any) -> None:
    """`IF NOT EXISTS` como no runner de migrations: SELECT de tabela inexistente ERRA no v3.1.5."""
    db.query("DEFINE TABLE IF NOT EXISTS seed_marker SCHEMALESS;")


def _marker_id(name: str) -> RecordID:
    """RecordID do marcador `seed_marker:<name>`."""
    return RecordID("seed_marker", name)


def _marker_seen(db: Any, name: str) -> bool:
    """Verifica se o marcador `seed_marker:<name>` já existe."""
    _ensure_marker_table(db)
    rows = db.query("SELECT id FROM $r;", {"r": _marker_id(name)})
    return bool(rows)


def _mark(db: Any, name: str) -> None:
    """Cria o marcador `seed_marker:<name>` com timestamp."""
    db.query("CREATE $r SET applied_at = time::now();", {"r": _marker_id(name)})


def seed_feed_cadastros(db: Any) -> int:
    """Semeia as `FEED_CADASTROS` como Cadastros rss ativos — bootstrap histórico que roda
    **UMA VEZ por ambiente** (marcador `seed:feed_cadastros`), não a cada deploy. Devolve quantas
    fontes processou (0 se já semeado).

    Por que once-per-env e não idempotente-a-cada-deploy: `tags=[]` é AMBÍGUO — significa tanto
    'legado ainda não migrado' (migration 0009) quanto 'o dono limpou todas as tags de propósito'
    (#106). O coalesce de `upsert_seed_source` preenche `[]` com as tags do seed; se rodasse todo
    deploy, refilaria as tags legadas por cima de um 'limpar tudo' do dono — destrutivo (achado do
    CodeRabbit, PR #116). Rodando só na 1ª vez, o `[]` no bootstrap é sempre legado (preenche); um
    `[]` posterior é sempre edição do dono (preservado, porque o seed não roda de novo). O coalesce
    permanece para proteger pausa/título que o dono já tenha mudado ANTES do 1º seed."""
    if _marker_seen(db, "feed_cadastros"):
        return 0
    for feed in FEED_CADASTROS:
        upsert_seed_source(
            db, kind="rss", canonical=feed.canonical, title=feed.title, tags=feed.tags
        )
    _mark(db, "feed_cadastros")
    return len(FEED_CADASTROS)


def seed_default_settings(db: Any) -> bool:
    """Cria o singleton `settings:global` com os defaults operacionais UMA VEZ por ambiente
    (KUBO-44, ADR-0028). Segue o mesmo padrão de marcador do seed de feeds para evitar
    overwrite de edições do dono feitas pela UI."""
    if _marker_seen(db, "settings"):
        return False
    settings_store.put_settings(
        db,
        digest_cron="30 9 * * *",
        distribution_paused=False,
        default_destination=None,
    )
    _mark(db, "settings")
    return True


_OWNER_CHAT_ID_ENV = "KUBO_OWNER_TELEGRAM_CHAT_ID"


def _owner_telegram_chat_id() -> str:
    """Lê o chat_id do dono do env; env ausente ou vazio é falha alta."""
    value = os.environ.get(_OWNER_CHAT_ID_ENV, "").strip()
    if not value:
        raise ConfigError(
            f"{_OWNER_CHAT_ID_ENV} não está configurado — defina o chat_id do Telegram do dono"
        )
    return value


def seed_owner_destination(db: Any) -> bool:
    """Semeia o destino Telegram do dono e o define como padrão UMA VEZ por ambiente.

    Cria o destino a partir de `KUBO_OWNER_TELEGRAM_CHAT_ID` (nunca literal no código),
    atualiza `settings:global.default_destination` e marca a ação. Requer que
    `seed_default_settings` já tenha criado o singleton. Idempotente por marcador:
    re-rodar não recria nem sobrescreve edições do dono.
    """
    if _marker_seen(db, "owner_destination"):
        return False

    chat_id = _owner_telegram_chat_id()
    rid = destination_store.create_destination(
        db,
        name="owner-telegram",
        kind="pessoa",
        channel="telegram",
        address=chat_id,
    )

    current = settings_store.get_settings(db)
    if current is None:
        raise ConfigError(
            "settings:global não encontrado — seed_default_settings deve rodar primeiro"
        )
    settings_store.put_settings(
        db,
        digest_cron=current.digest_cron,
        distribution_paused=current.distribution_paused,
        default_destination=rid,
    )

    _mark(db, "owner_destination")
    return True


def main() -> int:
    """Conecta por ambiente e semeia settings, destino padrão e feeds.

    Ordem (KUBO-45): settings primeiro, depois destino do dono (que escreve o
    ponteiro `default_destination` em settings), depois os feeds legados.
    Devolve total de feeds processado.
    """
    try:
        with client.connect() as db:
            settings_applied = seed_default_settings(db)
            owner_applied = seed_owner_destination(db)
            count = seed_feed_cadastros(db)
    except Exception:  # noqa: BLE001 — loga estruturado e repropaga (padrão do migrations-cli)
        _log.exception("seed_failed")
        raise
    _log.info("default_settings_seeded", applied=settings_applied)
    _log.info("owner_destination_seeded", applied=owner_applied)
    _log.info("feed cadastros seeded", count=count)
    return count


if __name__ == "__main__":
    main()
