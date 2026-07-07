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

  2. Camada de I/O (handlers): o schema real do Neon é conhecido (ADR-0012 §VII),
     mas o SQL só é EXECUTÁVEL contra o Neon vivo. Handlers são escritos + validados
     na sessão de EXECUÇÃO (gated no NEON_DATABASE_URL do servidor + backup/pg_dump),
     onde os poucos joins residuais (news->source, chave de join do transcript) se
     confirmam contra dado real numa passada só, em vez de chutados agora.

Uso (conexão SÓ por env, invariante 8; o agente nunca lê o valor):
    NEON_DATABASE_URL=... uv run --group import python scripts/neon_import.py --corpus <nome>
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from kubo.errors import ConfigError

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
    return dsn


def _handler_pending(corpus: str) -> int:
    """Handler escrito e validado na sessão de EXECUÇÃO, contra o Neon vivo.

    O modelo é conhecido (ADR-0012 §VII), mas o SQL só é validável executando: os
    joins residuais (news->source; chave de join do transcript por tipo) se
    confirmam com dado real numa passada só, em vez de chutados agora sem poder
    rodar. Requer NEON_DATABASE_URL no servidor + backup/pg_dump prontos."""
    raise NotImplementedError(
        f"corpus {corpus!r}: handler escrito na execução contra o Neon vivo "
        "(ADR-0012 §VII; docs/sessions/0007-neon-import.md)."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: um corpus por invocação. Valida a presença da conexão (sem lê-la) e
    despacha para o handler do corpus. A ordem entre invocações é sagrada: sources
    -> itens -> distillations por último (derived_from ENFORCED)."""
    parser = argparse.ArgumentParser(description="Import one-off do legado NeonDB (sessão 0007).")
    parser.add_argument(
        "--corpus",
        required=True,
        choices=(*_CORPUS_ORDER, "feed-map"),
        help="corpus a importar (ordem entre invocações: sources -> itens -> distillations)",
    )
    args = parser.parse_args(argv)
    _neon_dsn()  # falha cedo se a conexão não está no ambiente (o valor nunca é logado)
    return _handler_pending(args.corpus)


if __name__ == "__main__":
    raise SystemExit(main())
