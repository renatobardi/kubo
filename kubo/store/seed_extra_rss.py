"""Seed ad hoc de feeds RSS adicionais (segunda leva de fontes de IA).

Script one-shot idempotente que cadastra os feeds descobertos durante a pesquisa
via `kubo.store.knowledge.upsert_seed_source`. Pode ser rodado a qualquer momento
no deploy ou manualmente; reexecução é no-op por (kind, canonical).

Usa `client.connect()` (usuário root/SURREAL_USER do `.env`) porque o seed roda
no `kubo-scheduler`, que não carrega `KUBO_RW_SURREAL_PASS` — mesmo caminho do
`python -m kubo.store.seed` do deploy.

Para executar no ambiente de produção:
    python -m kubo.store.seed_extra_rss
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from kubo.scheduler.tenant import resolve_scheduler_tenant_and_user
from kubo.store import client
from kubo.store.knowledge import upsert_seed_source

_log = structlog.get_logger().bind(worker="seed-extra-rss")


@dataclass(frozen=True)
class FeedSeed:
    """Uma fonte RSS a semear: URL do feed (canonical) e rótulo (title)."""

    canonical: str
    title: str


# Feitas adicionais descobertos e validados (segunda leva). Mantém fora do seed
# legado (`kubo.store.seed`) para não misturar o bootstrap histórico com a carga
# pontual de novas fontes.
FEEDS: list[FeedSeed] = [
    # -- Grandes labs / modelos
    FeedSeed("https://openai.com/news/engineering/rss.xml", "OpenAI Engineering"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
        "Anthropic News",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_engineering.xml",
        "Anthropic Engineering",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_claude.xml",
        "Claude Blog",
    ),
    FeedSeed("https://blog.google/technology/ai/rss/", "Google AI Blog"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_google_ai.xml",
        "Google AI (community feed)",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_meta_ai.xml",
        "Meta AI",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_mistral.xml",
        "Mistral AI",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_cohere.xml",
        "Cohere",
    ),
    FeedSeed("https://blog.ai21.engineering/feed", "AI21 Labs"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_xainews.xml",
        "xAI",
    ),
    # -- Infra / chips / cloud
    FeedSeed("https://blogs.nvidia.com/feed/", "NVIDIA Blog"),
    FeedSeed("https://azure.microsoft.com/en-us/blog/feed/", "Microsoft Azure Blog"),
    FeedSeed(
        "https://aws.amazon.com/blogs/machine-learning/feed/",
        "AWS Machine Learning Blog",
    ),
    FeedSeed(
        "https://newsroom.ibm.com/press-releases-artificial-intelligence?pagetemplate=rss",
        "IBM AI Press Releases",
    ),
    FeedSeed("https://newsroom.amd.com/rss.xml", "AMD Newsroom"),
    FeedSeed("https://newsroom.arm.com/rss", "Arm Newsroom"),
    FeedSeed("https://www.databricks.com/rss.xml", "Databricks Blog"),
    FeedSeed("https://medium.com/feed/snowflake", "Snowflake Blog"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_groq.xml",
        "Groq",
    ),
    FeedSeed("https://www.together.ai/blog/rss.xml", "Together AI"),
    # -- Agentes / ferramentas / plataformas
    FeedSeed("https://releases.sh/cognition.atom", "Cognition"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_cursor.xml",
        "Cursor",
    ),
    FeedSeed("https://blog.langchain.dev/rss.xml", "LangChain"),
    FeedSeed("https://medium.com/feed/llamaindex-blog", "LlamaIndex"),
    FeedSeed("https://vercel.com/changelog/rss.xml", "Vercel Changelog"),
    FeedSeed("https://linear.app/rss/now.xml", "Linear /now"),
    FeedSeed("https://github.blog/feed/?tag=ai", "GitHub Blog — AI"),
    FeedSeed("https://goose-docs.ai/blog/rss.xml", "Goose"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_perplexity_hub.xml",
        "Perplexity",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_pinecone.xml",
        "Pinecone",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_weaviate.xml",
        "Weaviate",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_ollama.xml",
        "Ollama",
    ),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_windsurf_blog.xml",
        "Windsurf",
    ),
    FeedSeed("https://stability.ai/news-updates?format=rss", "Stability AI"),
    FeedSeed(
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_the_batch.xml",
        "The Batch | DeepLearning.AI",
    ),
    FeedSeed("https://news.ycombinator.com/rss", "Hacker News"),
    # -- Japão
    FeedSeed("https://sakana.ai/feed.xml", "Sakana AI"),
    FeedSeed("https://tech.preferred.jp/en/rss", "Preferred Networks"),
    # -- China
    FeedSeed("https://qwenlm.github.io/blog/index.xml", "Qwen"),
    FeedSeed("https://medium.com/feed/@kimi_moonshot", "Moonshot AI / Kimi"),
    # -- Mídia genérica (cobre empresas sem feed próprio)
    FeedSeed(
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "TechCrunch AI",
    ),
    FeedSeed(
        "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "MIT Technology Review AI",
    ),
    FeedSeed(
        "https://venturebeat.com/category/ai/feed/",
        "VentureBeat AI",
    ),
]


def seed_extra_rss_sources(db: Any) -> int:
    """Semeia os feeds adicionais como Cadastros rss ativos.

    Idempotente por (tenant_id, kind, canonical) e não-destrutivo: títulos/tags/pausa do
    dono sobrevivem. Devolve o número de feeds processados.
    """
    tenant_id, user_id = resolve_scheduler_tenant_and_user(db)
    for feed in FEEDS:
        upsert_seed_source(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            kind="rss",
            canonical=feed.canonical,
            title=feed.title,
            tags=[],
        )
    return len(FEEDS)


def main() -> int:
    """Conecta por ambiente e semeia os feeds adicionais.

    Usa `client.connect()` (usuário root/SURREAL_USER do .env) porque o seed roda
    do scheduler, que não carrega `KUBO_RW_SURREAL_PASS` — mesmo caminho do
    `python -m kubo.store.seed` do deploy.
    """
    try:
        with client.connect() as db:
            count = seed_extra_rss_sources(db)
    except Exception:  # noqa: BLE001 — loga estruturado e repropaga
        _log.exception("seed_extra_rss_failed")
        raise
    _log.info("extra_rss_feeds_seeded", count=count)
    return count


if __name__ == "__main__":
    main()
