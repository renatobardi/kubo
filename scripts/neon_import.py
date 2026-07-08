#!/usr/bin/env python3
"""Import one-off do legado NeonDB (Postgres) para o grafo (sessão 0007, ADR-0012).

Script one-off, NÃO worker sob contrato (D19 emendada): as garantias vêm da store
existente — idempotência por record ID determinístico, proveniência via
start_run/finish_run + collected_by. Precedente: scripts/embedding_smoke.py (fora
de kubo/). Roda um corpus por invocação (--corpus), na ordem sources -> itens ->
distillations (derived_from é ENFORCED). Neon estritamente read-only.

Duas camadas, deliberadamente separadas (o plano exige "funções puras testadas,
casca de I/O fina"):

  1. Camada PURA (abaixo, testada em tests/scripts/test_neon_import.py): mapeia
     valores legados -> args da store. Recebe valores explícitos, nunca uma linha do
     Neon — assim os testes independem do schema real do Neon.

  2. Camada de I/O (handlers): o schema real do Neon é conhecido (ADR-0012 §VII).
     O SQL só é EXECUTÁVEL e VALIDÁVEL contra o Neon vivo (gated no
     NEON_DATABASE_URL do servidor + backup/pg_dump) — não há teste automatizado
     de handler, propositalmente (não se inventa teste que dependa de banco vivo).

Uso (conexão SÓ por env, invariante 8; o agente nunca lê o valor):
    NEON_DATABASE_URL=... uv run --group import python scripts/neon_import.py --corpus <nome>
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.errors import ConfigError
from kubo.store import client, knowledge

_log = structlog.get_logger().bind(worker="neon_import")

# Ordem OBRIGATÓRIA entre invocações (derived_from é ENFORCED no schema): sources
# antes de qualquer item, distillations por último — uma distillation apontando para
# item inexistente falha (a órfã vira skipped_invalid contada, não crash). Corpora
# que ancoram destilados entram todos (ADR-0012 §X): cortá-los orfanaria destilados.
_CORPUS_ORDER = ("sources", "news", "videos", "podcasts", "emails", "linkedin", "distillations")

# Cursor server-side lê em blocos deste tamanho (segurança de memória nas tabelas de
# conteúdo). O texto da transcrição vem pronto na coluna transcripts.transcript — não
# há concat dos 816k transcript_segments (ADR-0012 §II).
_NEON_ITERSIZE = 2000

# Sources sintéticas para corpora sem cadastro de origem (ADR-0012 §VII): canonical
# byte-estável (é a chave do record ID). O `sender`/origem real vai para o metadata.
_SOURCE_EMAIL_CANONICAL = "legacy:email"
_SOURCE_LINKEDIN_CANONICAL = "legacy:linkedin"
_SOURCE_YOUTUBE_CANONICAL = "legacy:youtube"


def _safe_error(exc: Exception) -> dict[str, Any]:
    """Erro estruturado SEM segredo: `str(exc)` do psycopg pode ecoar a DSN (com
    senha) em erro de conexão/parse (invariante 8). Redige a DSN antes de persistir
    em `run.error` ou logar — usado por TODOS os handlers no `except` do run."""
    dsn = os.environ.get("NEON_DATABASE_URL") or ""
    message = str(exc)
    if dsn and dsn in message:
        message = message.replace(dsn, "<NEON_DATABASE_URL redigida>")
    return {"kind": type(exc).__name__, "message": message}


@dataclass(frozen=True)
class ItemArgs:
    """Args prontos para knowledge.upsert_item — a saída tipada da camada pura."""

    external_id: str
    content: str
    url: str | None
    title: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DistilledArgs:
    """Args prontos para knowledge.insert_distilled(chunks=[]) de um destilado legado."""

    summary: str
    claims: list[str]


def legacy_metadata(
    *,
    published_at: str | None,
    tags: Sequence[str],
    legacy_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserva no metadata o que os campos READONLY do schema descartariam.

    `item.collected_at` é READONLY e recebe a data do IMPORT — o timestamp ORIGINAL
    e as tags iriam a lugar nenhum. Vão para o namespace `legacy` de `item.metadata`
    (option<object> FLEXIBLE), custo zero. `extra` carrega o que é específico do
    corpus (ex.: `sender` de email — a origem real, já que emails penduram numa
    source sintética, ADR-0012 §VII). A MARCA de legado em si é proveniência
    (produced_by/collected_by -> run com worker='neon_import'), não um campo."""
    legacy: dict[str, Any] = {}
    if published_at:
        legacy["published_at"] = published_at
    if tags:
        legacy["tags"] = list(tags)
    if legacy_id:
        legacy["id"] = legacy_id
    if extra:
        legacy.update({k: v for k, v in extra.items() if v is not None})
    return {"legacy": legacy} if legacy else {}


def item_args(
    *,
    external_id: str | None,
    content: str,
    url: str | None,
    title: str | None,
    published_at: str | None,
    tags: Sequence[str],
    legacy_id: str | None = None,
) -> ItemArgs | None:
    """Monta os args de upsert_item; None se não há external_id.

    Sem external_id não há chave natural estável — o item não seria idempotente e o
    re-run duplicaria. O I/O conta esse None como skipped_invalid (com motivo), nunca
    o descarta em silêncio."""
    if not external_id:
        return None
    return ItemArgs(
        external_id=external_id,
        content=content,
        url=url,
        title=title,
        metadata=legacy_metadata(published_at=published_at, tags=tags, legacy_id=legacy_id),
    )


def pick_content(*candidates: str | None) -> str:
    """Primeiro conteúdo não-vazio, na ordem de prioridade dada; senão "".

    Prioridade do import (ADR-0012 §VII): `transcripts.transcript` (o texto que foi
    de fato destilado) antes do corpo/descrição/excerpt da tabela-tipo. Item sem
    NENHUM texto entra com content="" (é a âncora do derived_from de um destilado
    real; pulá-lo orfanaria o destilado) — o I/O conta isso como sub-categoria
    `sem_conteudo` dentro de imported."""
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate
    return ""


def claims_from_structured(structured: Any) -> list[str]:
    """Extrai `distilled.claims` do jsonb `distillations.structured`.

    `structured.claims` é uma lista de objetos `{text, evidence, ts_start}`
    (verificado nas amostras); só o `text` cabe em `distilled.claims`
    (array<string>) — evidence/ts_start são perda consciente (ADR-0012 §XI). Robusto
    a formatos inesperados (conteúdo legado é hostil): o que não for str não-vazia é
    ignorado, nunca explode."""
    if not isinstance(structured, dict):
        return []
    raw = structured.get("claims")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for claim in raw:
        text = claim.get("text") if isinstance(claim, dict) else None
        if isinstance(text, str) and text.strip():
            out.append(text)
    return out


def distilled_args(
    *, summary: str | None, claims: Sequence[str] | None = None
) -> DistilledArgs | None:
    """Args de insert_distilled para um destilado legado; None se não há summary.

    `distilled.summary` é NOT NULL no schema; summary ausente/vazio -> None (o I/O
    conta skipped_invalid). `chunks=[]` (verificado: a sequência vazia funciona hoje,
    sem mudança de API) — embedding/chunks são backfill do M6, não deste import."""
    if not summary or not summary.strip():
        return None
    return DistilledArgs(summary=summary, claims=list(claims) if claims else [])


# ── Mapa manual dos feeds (checkpoint 0007) ────────────────────────────────────
# O dono aponta a correspondência à mão entre os feed_sources legados e as sources
# já no grafo. O match automático por URL é SUGESTÃO, nunca decisão (instrução do
# dono): a source existente foi gravada pelo feed worker com o canonical exato do
# schedules.yaml (https vs http, trailing slash), e um upsert com canonical diferente
# criaria uma source duplicada e duplicaria todos os itens do período de sobreposição.


@dataclass(frozen=True)
class LegacyFeed:
    """Um feed_sources legado, já extraído da linha do Neon (fronteira tipada)."""

    id: str
    name: str
    url: str


@dataclass(frozen=True)
class GraphSource:
    """Uma source já no grafo (SELECT id, canonical, title FROM source)."""

    id: str
    canonical: str
    title: str | None


def _norm_url_for_suggestion(url: str) -> str:
    """Normalização SÓ para sugerir correspondência (casefold + strip + sem barra
    final). NÃO é canonicalização de gravação — o canonical de gravação é o do mapa
    manual do dono, byte a byte, nunca esta forma."""
    return url.strip().casefold().rstrip("/")


def feed_match_suggestion(feed: LegacyFeed, graph: Sequence[GraphSource]) -> str | None:
    """ID da source do grafo cujo canonical bate por URL normalizada — ou None.
    SUGESTÃO para o dono confirmar, nunca a decisão de gravação."""
    target = _norm_url_for_suggestion(feed.url)
    for src in graph:
        if _norm_url_for_suggestion(src.canonical) == target:
            return src.id
    return None


def render_feed_map(feeds: Sequence[LegacyFeed], graph: Sequence[GraphSource]) -> str:
    """Lista os feed_sources legados lado a lado com as sources do grafo + a sugestão
    de correspondência por URL, para o dono apontar o pareamento à mão (plano 0007).

    A coluna 'sugestão' é dica automática, NÃO decisão — o cabeçalho diz isso."""
    lines = [
        f"# Feeds legados: {len(feeds)}  |  Sources no grafo: {len(graph)}",
        "# 'sugestão' = match automático por URL (SÓ dica; o dono confirma à mão).",
        "",
        "## feed_sources (Neon)",
    ]
    for f in feeds:
        suggestion = feed_match_suggestion(f, graph)
        hint = f"  ->sugestão: {suggestion}" if suggestion else "  ->sugestão: (nenhuma)"
        lines.append(f"  [{f.id}] {f.name}  <{f.url}>{hint}")
    lines += ["", "## source (grafo, kind=rss)"]
    for s in graph:
        lines.append(f"  [{s.id}] {s.title or '(sem título)'}  <{s.canonical}>")
    return "\n".join(lines)


# ── Mapeamento legado -> grafo por corpus (checkpoint do dono, 2026-07-06) ─────
# `news_items.source` (nome) -> canonical da source no grafo. Confirmado à mão pelo
# dono: os 6 primeiros já existem (gravados pelo feed worker vivo, canonical exato
# do schedules.yaml); os 4 seguintes são novos; "Hacker News" é ÚNICA e colapsa os
# 12 feed_sources de HN do Neon (news_items só guarda o nome, nunca o feed_source
# individual) — a dedupe acontece de graça no upsert idempotente por canonical.
_NEWS_SOURCE_MAP: dict[str, str] = {
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "GitHub": "https://github.blog/ai-and-ml/feed/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Import AI": "https://importai.substack.com/feed",
    "OpenAI": "https://openai.com/news/rss.xml",
    "SemiAnalysis": "https://www.semianalysis.com/feed",
    "Anthropic": "https://www.anthropic.com/news",
    "Cognition": "https://cognition.ai/blog",
    "Cursor": "https://cursor.com/blog",
    "Mistral": "https://mistral.ai/news",
    "Hacker News": "https://news.ycombinator.com",
}


def resolve_news_source_canonical(
    name: str, *, feed_sources_by_name: Mapping[str, str] | None = None
) -> str | None:
    """Resolve `news_items.source` (nome) ao canonical da source do grafo.

    Primeiro o mapa manual do dono (checkpoint 2026-07-06); se o nome não está lá,
    cai para o `endpoint` do `feed_sources` homônimo (mesma regra de canonical de
    `_import_feed_sources`) — None se nenhum dos dois resolve (o I/O conta como
    skipped_invalid "source desconhecida", nunca inventa um canonical)."""
    if name in _NEWS_SOURCE_MAP:
        return _NEWS_SOURCE_MAP[name]
    if feed_sources_by_name:
        return feed_sources_by_name.get(name)
    return None


def youtube_channel_canonical(youtube_channel_id: str) -> str:
    """Canonical determinístico de um canal do YouTube (chave da source no grafo)."""
    return f"https://www.youtube.com/channel/{youtube_channel_id}"


def youtube_playlist_canonical(youtube_playlist_id: str) -> str:
    """Canonical determinístico de uma playlist do YouTube (chave da source)."""
    return f"https://www.youtube.com/playlist?list={youtube_playlist_id}"


def _video_source(
    row: Mapping[str, Any],
    channels: Mapping[Any, tuple[str, str | None]],
    playlists: Mapping[Any, tuple[str, str | None]],
) -> tuple[str, str | None] | None:
    """Resolve (canonical, título) da source de um vídeo legado: canal > playlist >
    None (o I/O usa a source sintética `legacy:youtube` quando nenhum resolve —
    ADR-0012 §VII: vídeo sem cadastro de canal/playlist ainda ancora destilado)."""
    channel_id = row.get("channel_id")
    if channel_id is not None and channel_id in channels:
        return channels[channel_id]
    playlist_id = row.get("playlist_id")
    if playlist_id is not None and playlist_id in playlists:
        return playlists[playlist_id]
    return None


_MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MiB de sanidade (ADR-0012 §VI) — reject+log, nunca trunca.


def _exceeds_cap(content: str) -> bool:
    """True se `content` estoura o cap de sanidade do import (1 MiB, ADR-0012 §VI).
    O maior transcript real verificado é 482 KB — o teto é folgado, pega só outlier."""
    return len(content.encode("utf-8")) > _MAX_CONTENT_BYTES


def _iso(value: Any) -> str | None:
    """Normaliza um timestamp do Neon (o driver devolve `datetime`) para ISO 8601.

    None passa; string já formatada passa como está (robustez a fixture de teste
    ou coluna já-texto) — só objetos com `.isoformat()` são convertidos."""
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def _item_args_with_extra(
    *,
    external_id: str | None,
    content: str,
    url: str | None,
    title: str | None,
    published_at: str | None,
    legacy_id: str | None,
    extra: dict[str, Any],
) -> ItemArgs | None:
    """Como `item_args`, mas repassa `extra` (ex.: sender de email, author de
    linkedin) ao namespace `legacy` do metadata — `item_args` não expõe `extra`
    (não precisava até a camada pura existir); construído aqui, reusando
    `legacy_metadata` já testada, para não estender a assinatura pública sem
    necessidade (email/linkedin são os únicos corpora com esse dado extra)."""
    if not external_id:
        return None
    return ItemArgs(
        external_id=external_id,
        content=content,
        url=url,
        title=title,
        metadata=legacy_metadata(
            published_at=published_at, tags=(), legacy_id=legacy_id, extra=extra
        ),
    )


# ── Reconciliação por corpus (plano 0007 §7.2.1) ───────────────────────────────


@dataclass
class ReconReport:
    """Reconciliação de UM corpus: toda linha de origem cai em `imported`,
    `preexisting` (idempotência/dedup — nenhum registro novo) ou `skipped_invalid`
    (com motivo). Nada some em silêncio — é a condição de desligamento do Neon.
    `render` acusa qualquer linha não contabilizada (buraco na reconciliação)."""

    corpus: str
    source_count: int = 0
    imported: int = 0
    sem_conteudo: int = 0
    preexisting: int = 0
    skipped_invalid: list[tuple[str, str]] = field(default_factory=list)

    def record_imported(self, *, empty: bool = False) -> None:
        """Conta um item/registro gravado. `empty=True` marca item importado sem
        texto (content="") — SUB-contagem de imported (ADR-0012 §VIII), não uma 4ª
        categoria disjunta: entra em imported e também em sem_conteudo, para a soma
        da reconciliação continuar fechando."""
        self.imported += 1
        if empty:
            self.sem_conteudo += 1

    def record_preexisting(self) -> None:
        """Linha que resolveu a um registro já presente (re-run idempotente ou
        sobreposição com a coleta viva) — nenhum registro novo criado."""
        self.preexisting += 1

    def record_skipped(self, legacy_id: str, reason: str) -> None:
        """Linha rejeitada na borda (sem external_id, sem summary, cap estourado):
        contada e motivada, NUNCA truncada nem descartada em silêncio."""
        self.skipped_invalid.append((legacy_id, reason))

    @property
    def accounted(self) -> int:
        """Total de linhas contabilizadas — deve igualar source_count."""
        return self.imported + self.preexisting + len(self.skipped_invalid)

    @property
    def reconciled(self) -> bool:
        """True só se toda linha de origem foi contabilizada (destino explica origem)."""
        return self.accounted == self.source_count

    def render(self) -> str:
        """Bloco de texto para as notas da sessão (commitado — plano 0007 §7.2.1)."""
        head = (
            f"corpus={self.corpus}  origem(Neon)={self.source_count}  "
            f"importados={self.imported} (sem_conteudo={self.sem_conteudo})  "
            f"já-presentes={self.preexisting}  rejeitados={len(self.skipped_invalid)}"
        )
        status = (
            "RECONCILIADO ✓"
            if self.reconciled
            else f"DISCREPÂNCIA: {self.source_count - self.accounted} linha(s) não contabilizada(s)"
        )
        lines = [head, status]
        if self.skipped_invalid:
            lines.append("rejeitados (id: motivo):")
            lines += [f"  {legacy_id}: {reason}" for legacy_id, reason in self.skipped_invalid]
        return "\n".join(lines)


# ── Camada de I/O (handlers) ───────────────────────────────────────────────────
# O modelo legado é conhecido (ADR-0012 §VII); os handlers são escritos e validados
# na sessão de EXECUÇÃO, contra o Neon vivo (a única forma de validar o SQL). Cada
# handler deve:
#   1. abrir um run (knowledge.start_run(db, worker="neon_import")) e fechá-lo com
#      finish_run(stats=contadores do report) / fail_run em exceção;
#   2. ler o Neon por cursor SERVER-SIDE (itersize=_NEON_ITERSIZE), ORDER BY estável,
#      NUNCA fetchall; o texto vem pronto de transcripts.transcript (sem concat de
#      transcript_segments — ADR-0012 §II);
#   3. montar o conteúdo por prioridade (pick_content: transcript > corpo > ""),
#      mapear pela camada pura (item_args/distilled_args/claims_from_structured);
#   4. detectar preexisting por POINT-READ do record ID determinístico antes do
#      upsert (ADR-0012 §VIII); gravar SÓ pela store, passando o run; distillations:
#      resolver derived_from por dict em memória (SELECT external_id,id FROM item) e
#      pular item que já tem distilled_for;
#   5. contabilizar tudo em ReconReport (imported[/sem_conteudo] / preexisting /
#      skipped_invalid); cap 1 MiB = reject+log, NUNCA truncar (ADR-0012 §VI).
# Armadilhas Neon: retry na 1ª conexão (cold-start do autosuspend); evitar transação
# gigante por corpus (statement timeout); DSN NUNCA logada.


def _neon_dsn() -> str:
    """DSN do Neon do ambiente (invariante 8: só por env, nunca lida/logada pelo
    agente). Endpoint DIRETO (não `-pooler`: PgBouncer quebra named cursors),
    sslmode=require. Falha explícita se ausente."""
    dsn = os.environ.get("NEON_DATABASE_URL")
    if not dsn:
        raise ConfigError(
            "NEON_DATABASE_URL ausente no ambiente (invariante 8: conexão só por env). "
            "Use o endpoint DIRETO (não -pooler) com sslmode=require."
        )
    if "sslmode=" not in dsn:
        # Falha explícita e SEM detalhe da dsn (invariante 8) — Neon exige SSL; isto
        # pega o erro do operador (DSN colada sem sslmode) antes de tentar conectar.
        raise ConfigError("NEON_DATABASE_URL sem sslmode= — Neon exige SSL (ex.: sslmode=require).")
    return dsn


def _connect_neon() -> Any:
    """Abre a conexão ao Neon (import TARDIO de psycopg: a camada pura continua
    importável sem o grupo de dependência `import`, ADR-0012 §I/consequências).

    Retry ÚNICO na 1ª tentativa: o autosuspend do Neon derruba a 1ª conexão em
    cold-start; a 2ª já encontra o compute acordado. `row_factory=dict_row` deixa
    cada linha indexável por nome de coluna (os handlers abaixo assumem isso).

    `str(exc)` do psycopg pode ecoar a DSN (com senha) em erro de conexão/parse —
    NUNCA propagado (invariante 8): toda falha vira `ConfigError` com mensagem
    fixa, sem detalhe, via `from None` (o traceback encadeado do `except` também
    carregaria a DSN)."""
    import psycopg
    from psycopg.rows import dict_row

    dsn = _neon_dsn()
    for attempt in (1, 2):
        try:
            # type: ignore justificado — limitação conhecida dos stubs do psycopg3: o
            # overload genérico de `connect()` não liga `Row` a partir de `row_factory`
            # quando chamado como função de módulo (só via `Connection.connect` teria
            # o generic correto); em runtime dict_row funciona normalmente.
            return psycopg.connect(dsn, row_factory=dict_row)  # type: ignore[reportArgumentType]
        except psycopg.OperationalError:
            if attempt == 1:
                _log.warning("neon_import.cold_start_retry")
                time.sleep(5)
                continue
            raise ConfigError(
                "falha ao conectar no Neon (cold-start?) — detalhe omitido (invariante 8)"
            ) from None
        except psycopg.Error:
            raise ConfigError(
                "DSN/conexão do Neon inválida — detalhe omitido (invariante 8)"
            ) from None
    # Inalcançável (o loop sempre retorna ou levanta) — satisfaz o checador de tipos.
    raise ConfigError("falha ao conectar no Neon — detalhe omitido (invariante 8)")


def _iter_rows(neon: Any, cursor_name: str, query: str) -> Iterator[Mapping[str, Any]]:
    """Itera as linhas de `query` por cursor SERVER-SIDE nomeado (itersize=
    _NEON_ITERSIZE) — NUNCA fetchall no corpus principal (ADR-0012 §II/§VII):
    o texto vem pronto de `transcripts.transcript`, sem streaming de segments."""
    with neon.cursor(name=cursor_name) as cur:
        cur.itersize = _NEON_ITERSIZE
        cur.execute(query)
        yield from cur


def _transcript_map(neon: Any, source_type: str) -> dict[str, str]:
    """dict source_ref->transcript de um `source_type` NÃO-youtube (poucas centenas
    de linhas por tipo — cursor comum é seguro aqui; a leitura grande e sem limite
    é a do corpus principal, não este índice auxiliar, ADR-0012 §II)."""
    with neon.cursor() as cur:
        cur.execute(
            "SELECT source_ref, transcript FROM transcripts WHERE source_type = %s",
            (source_type,),
        )
        index = {
            row["source_ref"]: row["transcript"] for row in cur.fetchall() if row["source_ref"]
        }
    _log.info("neon_import.transcript_map_built", source_type=source_type, count=len(index))
    return index


def _feed_sources_by_name(neon: Any) -> dict[str, str]:
    """dict name->canonical(=endpoint) de `feed_sources` — fallback de resolução de
    `news_items.source` quando o nome não está no mapa manual do dono (§VII)."""
    with neon.cursor() as cur:
        cur.execute("SELECT name, endpoint FROM feed_sources")
        index = {row["name"]: row["endpoint"] for row in cur.fetchall() if row["name"]}
    _log.info("neon_import.feed_sources_by_name_built", count=len(index))
    return index


def _podcast_feed_map(neon: Any) -> dict[Any, tuple[str, str | None]]:
    """dict id->(feed_url, title) de `podcast_feeds`, para resolver a source de
    cada `podcast_episodes.feed_id` sem 1 query por episódio."""
    with neon.cursor() as cur:
        cur.execute("SELECT id, feed_url, title FROM podcast_feeds")
        index = {row["id"]: (row["feed_url"], row["title"]) for row in cur.fetchall()}
    _log.info("neon_import.podcast_feed_map_built", count=len(index))
    return index


def _channel_canonicals(neon: Any) -> dict[Any, tuple[str, str | None]]:
    """dict target_channels.id->(canonical, channel_name), para resolver a source
    de vídeos dirigidos por `channel_id` sem 1 query por vídeo."""
    with neon.cursor() as cur:
        cur.execute("SELECT id, youtube_channel_id, channel_name FROM target_channels")
        index = {
            row["id"]: (youtube_channel_canonical(row["youtube_channel_id"]), row["channel_name"])
            for row in cur.fetchall()
        }
    _log.info("neon_import.channel_canonicals_built", count=len(index))
    return index


def _playlist_canonicals(neon: Any) -> dict[Any, tuple[str, str | None]]:
    """dict playlists.id->(canonical, title), espelha `_channel_canonicals`."""
    with neon.cursor() as cur:
        cur.execute("SELECT id, youtube_playlist_id, title FROM playlists")
        index = {
            row["id"]: (youtube_playlist_canonical(row["youtube_playlist_id"]), row["title"])
            for row in cur.fetchall()
        }
    _log.info("neon_import.playlist_canonicals_built", count=len(index))
    return index


def _source_index(db: Any) -> dict[str, RecordID]:
    """dict canonical->source (uma leitura via `knowledge.list_sources`, invariante
    2: nenhum SELECT cru de `source` no script) para os handlers de item RESOLVEREM
    a source sem upsert — upsertar por linha reescreveria title/kind de source já
    viva (achado do advisor #1)."""
    index = {s.canonical: s.id for s in knowledge.list_sources(db)}
    _log.info("neon_import.source_index_built", count=len(index))
    return index


def _resolve_source(index: Mapping[str, RecordID], canonical: str) -> RecordID:
    """Resolve a source pelo canonical no índice pré-carregado; MISS é erro FATAL,
    não skip — força mecanicamente rodar `--corpus sources` antes de qualquer
    corpus de item (advisor #1: nenhum handler de item cria/upserta source)."""
    source = index.get(canonical)
    if source is None:
        raise RuntimeError(
            f"source ausente para canonical {canonical!r} — rode --corpus sources primeiro"
        )
    return source


def _report_stats(report: ReconReport) -> dict[str, Any]:
    """Contadores do `ReconReport` prontos para `finish_run(stats=...)`."""
    return {
        "source_count": report.source_count,
        "imported": report.imported,
        "sem_conteudo": report.sem_conteudo,
        "preexisting": report.preexisting,
        "skipped_invalid": len(report.skipped_invalid),
        "reconciled": report.reconciled,
    }


@contextmanager
def _run_context(db: Any, report: ReconReport) -> Iterator[RecordID]:
    """Envolve cada handler de corpus: abre/fecha o run, redige o erro antes de
    persistir (invariante 8) e imprime o relatório no fim. Centraliza o boilerplate
    idêntico dos 7 handlers — o corpo do `with` só faz a lógica de linhas do corpus."""
    run = knowledge.start_run(db, worker="neon_import")
    try:
        yield run
        knowledge.finish_run(db, run, stats=_report_stats(report))
    except Exception as exc:
        knowledge.fail_run(db, run, error=_safe_error(exc))
        raise
    finally:
        print(report.render())


def _write_source(
    db: Any, *, report: ReconReport, known: set[str], kind: str, canonical: str, title: str | None
) -> None:
    """Grava (ou conta preexisting) uma source: `known` é o snapshot+dedupe
    intra-run (ADR-0012 §VIII aplicado a `source`)."""
    if canonical in known:
        report.record_preexisting()
        return
    knowledge.upsert_source(db, kind=kind, canonical=canonical, title=title)
    known.add(canonical)
    report.record_imported()


def _write_item(
    db: Any,
    *,
    report: ReconReport,
    run: RecordID,
    existing_external_ids: set[str],
    source: RecordID,
    legacy_id: str,
    args: ItemArgs | None,
) -> None:
    """Grava (ou conta) um item: `args=None` (sem external_id/summary) -> skipped;
    cap 1 MiB estourado -> skipped, NUNCA truncado (ADR-0012 §VI); external_id já
    visto -> preexisting (sem regravar); senão upsert_item + imported (sub-contado
    sem_conteudo se content vazio, ADR-0012 §VIII) e o external_id entra no
    snapshot (dedupe intra-corpus)."""
    if args is None:
        report.record_skipped(legacy_id, "sem external_id/summary")
        return
    if _exceeds_cap(args.content):
        report.record_skipped(legacy_id, "conteudo > 1MiB")
        return
    if args.external_id in existing_external_ids:
        report.record_preexisting()
        return
    knowledge.upsert_item(
        db,
        source=source,
        external_id=args.external_id,
        content=args.content,
        url=args.url,
        title=args.title,
        metadata=args.metadata,
        run=run,
    )
    existing_external_ids.add(args.external_id)
    report.record_imported(empty=(args.content == ""))


# ── Handlers por corpus (I/O; validados na execução contra o Neon vivo) ────────

_FEED_SOURCES_QUERY = (
    "SELECT id, name, endpoint, source_type, deleted_at FROM feed_sources ORDER BY id"
)
_PODCAST_FEEDS_QUERY = "SELECT id, feed_url, title, deleted_at FROM podcast_feeds ORDER BY id"
_TARGET_CHANNELS_QUERY = (
    "SELECT id, youtube_channel_id, channel_name, deleted_at FROM target_channels ORDER BY id"
)
_PLAYLISTS_QUERY = "SELECT id, youtube_playlist_id, title, deleted_at FROM playlists ORDER BY id"


def _import_feed_sources(neon: Any, db: Any, report: ReconReport, known: set[str]) -> None:
    """feed_sources -> source (kind = source_type do Neon: rss/html/hn), resolvida
    pelo mapa manual do dono (checkpoint 2026-07-06): nome sem mapeamento é rejeitado
    — o dono não confirmou o canonical, então não se inventa um a partir do endpoint."""
    for row in _iter_rows(neon, "ni_feed_sources", _FEED_SOURCES_QUERY):
        report.source_count += 1
        legacy_id = str(row["id"])
        if row.get("deleted_at") is not None:
            report.record_skipped(legacy_id, "deleted")
            continue
        canonical = _NEWS_SOURCE_MAP.get(row["name"])
        if canonical is None:
            report.record_skipped(legacy_id, f"sem mapeamento manual: {row['name']!r}")
            continue
        _write_source(
            db,
            report=report,
            known=known,
            kind=row["source_type"],
            canonical=canonical,
            title=row["name"],
        )


def _import_podcast_feeds(neon: Any, db: Any, report: ReconReport, known: set[str]) -> None:
    """podcast_feeds -> source (kind=podcast, canonical=feed_url)."""
    for row in _iter_rows(neon, "ni_podcast_feeds", _PODCAST_FEEDS_QUERY):
        report.source_count += 1
        legacy_id = str(row["id"])
        if row.get("deleted_at") is not None:
            report.record_skipped(legacy_id, "deleted")
            continue
        _write_source(
            db,
            report=report,
            known=known,
            kind="podcast",
            canonical=row["feed_url"],
            title=row["title"],
        )


def _import_target_channels(neon: Any, db: Any, report: ReconReport, known: set[str]) -> None:
    """target_channels -> source (kind=youtube)."""
    for row in _iter_rows(neon, "ni_target_channels", _TARGET_CHANNELS_QUERY):
        report.source_count += 1
        legacy_id = str(row["id"])
        if row.get("deleted_at") is not None:
            report.record_skipped(legacy_id, "deleted")
            continue
        canonical = youtube_channel_canonical(row["youtube_channel_id"])
        _write_source(
            db,
            report=report,
            known=known,
            kind="youtube",
            canonical=canonical,
            title=row["channel_name"],
        )


def _import_playlists(neon: Any, db: Any, report: ReconReport, known: set[str]) -> None:
    """playlists -> source (kind=youtube-playlist)."""
    for row in _iter_rows(neon, "ni_playlists", _PLAYLISTS_QUERY):
        report.source_count += 1
        legacy_id = str(row["id"])
        if row.get("deleted_at") is not None:
            report.record_skipped(legacy_id, "deleted")
            continue
        canonical = youtube_playlist_canonical(row["youtube_playlist_id"])
        _write_source(
            db,
            report=report,
            known=known,
            kind="youtube-playlist",
            canonical=canonical,
            title=row["title"],
        )


def _import_synthetic_sources(db: Any, report: ReconReport, known: set[str]) -> None:
    """As 3 sources sintéticas sem cadastro no legado (email/linkedin/youtube,
    ADR-0012 §VII) — a HN única é absorvida pelo mapa manual em
    `_import_feed_sources`. `legacy:youtube` mora aqui (não em `_import_videos`)
    para que TODA source, sintética ou não, seja criada só pelo corpus 'sources'
    (advisor #1: handlers de item nunca upsertam)."""
    report.source_count += 1
    _write_source(
        db,
        report=report,
        known=known,
        kind="email",
        canonical=_SOURCE_EMAIL_CANONICAL,
        title="Email (legado)",
    )
    report.source_count += 1
    _write_source(
        db,
        report=report,
        known=known,
        kind="linkedin",
        canonical=_SOURCE_LINKEDIN_CANONICAL,
        title="LinkedIn (legado)",
    )
    report.source_count += 1
    _write_source(
        db,
        report=report,
        known=known,
        kind="youtube",
        canonical=_SOURCE_YOUTUBE_CANONICAL,
        title="YouTube (sem canal/playlist)",
    )


def _import_sources(neon: Any, db: Any, report: ReconReport) -> None:
    """Corpus 'sources' (ADR-0012 §VII): cria/atualiza todas as sources do grafo a
    partir de feed_sources, podcast_feeds, target_channels, playlists + as
    sintéticas email/linkedin/youtube. Roda ANTES de qualquer item (§X: sources ->
    itens -> distillations)."""
    with _run_context(db, report):
        known = {s.canonical for s in knowledge.list_sources(db)}
        _import_feed_sources(neon, db, report, known)
        _import_podcast_feeds(neon, db, report, known)
        _import_target_channels(neon, db, report, known)
        _import_playlists(neon, db, report, known)
        _import_synthetic_sources(db, report, known)


_NEWS_QUERY = (
    "SELECT id, source, url, title, published_at, excerpt, body FROM news_items ORDER BY id"
)


def _import_news_row(
    row: Mapping[str, Any],
    db: Any,
    report: ReconReport,
    run: RecordID,
    transcripts: Mapping[str, str],
    feed_by_name: Mapping[str, str],
    source_index: Mapping[str, RecordID],
    existing: set[str],
) -> None:
    legacy_id = row["url"] or f"news:{row['id']}"
    canonical = resolve_news_source_canonical(row["source"], feed_sources_by_name=feed_by_name)
    if canonical is None:
        report.record_skipped(legacy_id, "source desconhecida")
        return
    source = _resolve_source(source_index, canonical)
    content = pick_content(transcripts.get(row["url"]), row["body"], row["excerpt"])
    args = item_args(
        external_id=row["url"],
        content=content,
        url=row["url"],
        title=row["title"],
        published_at=_iso(row["published_at"]),
        tags=(),
        legacy_id=row["url"],
    )
    _write_item(
        db,
        report=report,
        run=run,
        existing_external_ids=existing,
        source=source,
        legacy_id=legacy_id,
        args=args,
    )


def _import_news(neon: Any, db: Any, report: ReconReport) -> None:
    """news_items -> item (ADR-0012 §VII): source resolvida pelo mapa manual do
    dono com fallback ao endpoint do feed_sources homônimo; content por
    prioridade transcript(news, source_ref=url) > body > excerpt."""
    with _run_context(db, report) as run:
        transcripts = _transcript_map(neon, "news")
        feed_by_name = _feed_sources_by_name(neon)
        existing = set(knowledge.item_index(db).keys())
        source_index = _source_index(db)
        for row in _iter_rows(neon, "ni_news_items", _NEWS_QUERY):
            report.source_count += 1
            _import_news_row(
                row, db, report, run, transcripts, feed_by_name, source_index, existing
            )


def _import_video_row(
    row: Mapping[str, Any],
    db: Any,
    report: ReconReport,
    run: RecordID,
    channels: Mapping[Any, tuple[str, str | None]],
    playlists: Mapping[Any, tuple[str, str | None]],
    source_index: Mapping[str, RecordID],
    legacy_source: RecordID,
    existing: set[str],
) -> None:
    legacy_id = row["video_id"] or "vídeo-sem-id"
    resolved = _video_source(row, channels, playlists)
    if resolved is not None:
        canonical, _title_hint = resolved
        source = _resolve_source(source_index, canonical)
    else:
        source = legacy_source
    content = pick_content(row["transcript"])
    title = row.get("cv_title") or row.get("pv_title")
    url = row.get("cv_url") or row.get("pv_url")
    published_at = _iso(row.get("cv_published_at") or row.get("pv_published_at"))
    args = item_args(
        external_id=row["video_id"],
        content=content,
        url=url,
        title=title,
        published_at=published_at,
        tags=(),
        legacy_id=row["video_id"],
    )
    _write_item(
        db,
        report=report,
        run=run,
        existing_external_ids=existing,
        source=source,
        legacy_id=legacy_id,
        args=args,
    )


_VIDEOS_QUERY = (
    "SELECT DISTINCT ON (t.youtube_video_id) "
    "t.youtube_video_id AS video_id, t.transcript AS transcript, "
    "cv.title AS cv_title, cv.url AS cv_url, cv.published_at AS cv_published_at, "
    "cv.channel_id AS channel_id, "
    "pv.title AS pv_title, pv.url AS pv_url, pv.published_at AS pv_published_at, "
    "pv.playlist_id AS playlist_id "
    "FROM transcripts t "
    "LEFT JOIN channel_videos cv ON cv.youtube_video_id = t.youtube_video_id "
    "LEFT JOIN playlist_videos pv ON pv.youtube_video_id = t.youtube_video_id "
    "WHERE t.source_type = 'youtube' "
    "ORDER BY t.youtube_video_id"
)


def _import_videos(neon: Any, db: Any, report: ReconReport) -> None:
    """videos <- transcripts WHERE source_type='youtube' (ADR-0012 §VII: dirigido
    por transcripts, nunca por channel_videos/playlist_videos — senão orfanaria
    destilados de vídeos sem linha de cadastro). LEFT JOIN por youtube_video_id
    para título/data/url; canal > playlist > source sintética `legacy:youtube`."""
    with _run_context(db, report) as run:
        source_index = _source_index(db)
        legacy_source = _resolve_source(source_index, _SOURCE_YOUTUBE_CANONICAL)
        channels = _channel_canonicals(neon)
        playlists = _playlist_canonicals(neon)
        existing = set(knowledge.item_index(db).keys())
        for row in _iter_rows(neon, "ni_videos", _VIDEOS_QUERY):
            report.source_count += 1
            _import_video_row(
                row, db, report, run, channels, playlists, source_index, legacy_source, existing
            )


_PODCASTS_QUERY = (
    "SELECT id, feed_id, guid, title, enclosure_url, published_at, description "
    "FROM podcast_episodes ORDER BY id"
)


def _import_podcast_row(
    row: Mapping[str, Any],
    db: Any,
    report: ReconReport,
    run: RecordID,
    feeds: Mapping[Any, tuple[str, str | None]],
    transcripts: Mapping[str, str],
    source_index: Mapping[str, RecordID],
    existing: set[str],
) -> None:
    legacy_id = row["guid"] or f"podcast:{row['id']}"
    feed = feeds.get(row["feed_id"])
    if feed is None:
        report.record_skipped(legacy_id, "feed inexistente")
        return
    canonical, _feed_title = feed
    source = _resolve_source(source_index, canonical)
    content = pick_content(transcripts.get(row["guid"]), row["description"])
    args = item_args(
        external_id=row["guid"],
        content=content,
        url=row["enclosure_url"],
        title=row["title"],
        published_at=_iso(row["published_at"]),
        tags=(),
        legacy_id=row["guid"],
    )
    _write_item(
        db,
        report=report,
        run=run,
        existing_external_ids=existing,
        source=source,
        legacy_id=legacy_id,
        args=args,
    )


def _import_podcasts(neon: Any, db: Any, report: ReconReport) -> None:
    """podcast_episodes -> item; source = podcast_feeds via feed_id."""
    with _run_context(db, report) as run:
        feeds = _podcast_feed_map(neon)
        transcripts = _transcript_map(neon, "podcast")
        existing = set(knowledge.item_index(db).keys())
        source_index = _source_index(db)
        for row in _iter_rows(neon, "ni_podcast_episodes", _PODCASTS_QUERY):
            report.source_count += 1
            _import_podcast_row(row, db, report, run, feeds, transcripts, source_index, existing)


_EMAILS_QUERY = "SELECT id, message_id, sender, subject, body, received_at FROM emails ORDER BY id"


def _import_email_row(
    row: Mapping[str, Any],
    db: Any,
    report: ReconReport,
    run: RecordID,
    source: RecordID,
    transcripts: Mapping[str, str],
    existing: set[str],
) -> None:
    legacy_id = row["message_id"] or f"email:{row['id']}"
    content = pick_content(transcripts.get(row["message_id"]), row["body"])
    args = _item_args_with_extra(
        external_id=row["message_id"],
        content=content,
        url=None,
        title=row["subject"],
        published_at=_iso(row["received_at"]),
        legacy_id=row["message_id"],
        extra={"sender": row["sender"]},
    )
    _write_item(
        db,
        report=report,
        run=run,
        existing_external_ids=existing,
        source=source,
        legacy_id=legacy_id,
        args=args,
    )


def _import_emails(neon: Any, db: Any, report: ReconReport) -> None:
    """emails -> item; source sintética `legacy:email`; sender preservado no
    metadata (ADR-0012 §VII: parsing de remetente em sources é scope creep)."""
    with _run_context(db, report) as run:
        source = _resolve_source(_source_index(db), _SOURCE_EMAIL_CANONICAL)
        transcripts = _transcript_map(neon, "email")
        existing = set(knowledge.item_index(db).keys())
        for row in _iter_rows(neon, "ni_emails", _EMAILS_QUERY):
            report.source_count += 1
            _import_email_row(row, db, report, run, source, transcripts, existing)


_LINKEDIN_QUERY = "SELECT id, url, author, body, created_at FROM linkedin_posts ORDER BY id"


def _import_linkedin_row(
    row: Mapping[str, Any],
    db: Any,
    report: ReconReport,
    run: RecordID,
    source: RecordID,
    transcripts: Mapping[str, str],
    existing: set[str],
) -> None:
    legacy_id = row["url"] or f"linkedin:{row['id']}"
    content = pick_content(transcripts.get(row["url"]), row["body"])
    args = _item_args_with_extra(
        external_id=row["url"],
        content=content,
        url=row["url"],
        title=None,
        published_at=_iso(row["created_at"]),
        legacy_id=row["url"],
        extra={"author": row["author"]},
    )
    _write_item(
        db,
        report=report,
        run=run,
        existing_external_ids=existing,
        source=source,
        legacy_id=legacy_id,
        args=args,
    )


def _import_linkedin(neon: Any, db: Any, report: ReconReport) -> None:
    """linkedin_posts -> item; source sintética `legacy:linkedin`; content por
    prioridade transcript(linkedin, source_ref=url) > body; author no metadata."""
    with _run_context(db, report) as run:
        source = _resolve_source(_source_index(db), _SOURCE_LINKEDIN_CANONICAL)
        transcripts = _transcript_map(neon, "linkedin")
        existing = set(knowledge.item_index(db).keys())
        for row in _iter_rows(neon, "ni_linkedin_posts", _LINKEDIN_QUERY):
            report.source_count += 1
            _import_linkedin_row(row, db, report, run, source, transcripts, existing)


_DISTILLATIONS_QUERY = "SELECT id, source_key, content, structured FROM distillations ORDER BY id"


def _import_distillation_row(
    row: Mapping[str, Any],
    db: Any,
    report: ReconReport,
    run: RecordID,
    item_by_external_id: Mapping[str, RecordID],
) -> None:
    legacy_id = str(row.get("id"))
    source_key = row.get("source_key")
    item = item_by_external_id.get(source_key) if source_key else None
    if item is None:
        report.record_skipped(legacy_id, "órfã: item inexistente")
        return
    if knowledge.distilled_for(db, item):
        report.record_preexisting()
        return
    args = distilled_args(
        summary=row.get("content"), claims=claims_from_structured(row.get("structured"))
    )
    if args is None:
        report.record_skipped(legacy_id, "sem summary")
        return
    knowledge.insert_distilled(
        db, item=item, summary=args.summary, chunks=[], claims=args.claims, run=run
    )
    report.record_imported()


def _import_distillations(neon: Any, db: Any, report: ReconReport) -> None:
    """distillations -> distilled (ADR-0012 §III/§XI): derived_from resolvido por
    dict em memória external_id->item (uma query); distillation cujo source_key
    não resolve a um item vira órfã (skipped); item já destilado (distilled_for
    não vazio) marca preexisting (idempotência do re-run). Roda POR ÚLTIMO —
    derived_from é ENFORCED (§X)."""
    with _run_context(db, report) as run:
        item_by_external_id = knowledge.item_index(db)
        for row in _iter_rows(neon, "ni_distillations", _DISTILLATIONS_QUERY):
            report.source_count += 1
            _import_distillation_row(row, db, report, run, item_by_external_id)


def _feed_map(neon: Any, db: Any) -> None:
    """Corpus 'feed-map', READ-ONLY: lista feed_sources do Neon lado a lado com as
    sources do grafo (kind=rss) + sugestão de correspondência automática — SÓ
    dica, o dono decide (ADR-0012 §VII)."""
    feeds = [
        LegacyFeed(id=str(row["id"]), name=row["name"], url=row["endpoint"])
        for row in _iter_rows(
            neon, "ni_feed_map", "SELECT id, name, endpoint FROM feed_sources ORDER BY id"
        )
    ]
    graph = [
        GraphSource(id=str(s.id), canonical=s.canonical, title=s.title)
        for s in knowledge.list_sources(db)
        if s.kind == "rss"
    ]
    print(render_feed_map(feeds, graph))


_HANDLERS: dict[str, Callable[[Any, Any, ReconReport], None]] = {
    "sources": _import_sources,
    "news": _import_news,
    "videos": _import_videos,
    "podcasts": _import_podcasts,
    "emails": _import_emails,
    "linkedin": _import_linkedin,
    "distillations": _import_distillations,
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: um corpus por invocação. Valida a presença da conexão (sem lê-la),
    abre o Neon e o grafo, e despacha para o handler do corpus. A ordem entre
    invocações é sagrada: sources -> itens -> distillations por último
    (derived_from ENFORCED, ADR-0012 §VII/§X). `feed-map` é read-only."""
    parser = argparse.ArgumentParser(description="Import one-off do legado NeonDB (sessão 0007).")
    parser.add_argument(
        "--corpus",
        required=True,
        choices=(*_CORPUS_ORDER, "feed-map"),
        help="corpus a importar (ordem entre invocações: sources -> itens -> distillations)",
    )
    args = parser.parse_args(argv)
    _neon_dsn()  # falha cedo se a conexão não está no ambiente (o valor nunca é logado)
    neon = _connect_neon()
    try:
        with client.connect(client.config()) as db:
            if args.corpus == "feed-map":
                _feed_map(neon, db)
            else:
                report = ReconReport(corpus=args.corpus)
                _HANDLERS[args.corpus](neon, db, report)
        return 0
    finally:
        neon.close()


if __name__ == "__main__":
    raise SystemExit(main())
