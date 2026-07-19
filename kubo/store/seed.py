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

from dataclasses import dataclass
from typing import Any

import structlog

from kubo.store import client
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
    # `IF NOT EXISTS` como no runner de migrations: SELECT de tabela inexistente ERRA no v3.1.5.
    db.query("DEFINE TABLE IF NOT EXISTS seed_marker SCHEMALESS;")
    if db.query("SELECT id FROM seed_marker:feed_cadastros;"):
        return 0
    for feed in FEED_CADASTROS:
        upsert_seed_source(
            db, kind="rss", canonical=feed.canonical, title=feed.title, tags=feed.tags
        )
    db.query("CREATE seed_marker:feed_cadastros SET applied_at = time::now();")
    return len(FEED_CADASTROS)


def main() -> int:
    """Conecta por ambiente e semeia as fontes RSS; devolve o total processado."""
    try:
        with client.connect() as db:
            count = seed_feed_cadastros(db)
    except Exception:  # noqa: BLE001 — loga estruturado e repropaga (padrão do migrations-cli)
        _log.exception("seed_failed")
        raise
    _log.info("feed cadastros seeded", count=count)
    return count


if __name__ == "__main__":
    main()
