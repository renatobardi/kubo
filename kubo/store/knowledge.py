"""Camada de acesso ao grafo de conhecimento — a ÚNICA porta para as tabelas §2.3.

Expõe SOMENTE os comportamentos que os consumidores da fase 1 exigem (coleta →
destilação → busca → proveniência). Nenhum método sem teste que o exija.

Idempotência por record ID determinístico derivado da chave natural (UPSERT), o
que elimina a corrida do get-or-create sem SELECT-then-CREATE. Escrita composta
(distilled + chunks + arestas) é atômica via `transaction.run_transaction`.
Conteúdo coletado é hostil: entra sempre por bind param, nunca interpolado.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from surrealdb import RecordID

from kubo.store.transaction import run_transaction

_log = structlog.get_logger(__name__)

# Parte estática da busca KNN (sem interpolação — a store resolve chunk -> distilled).
_KNN_SELECT = "SELECT id, vector::distance::knn() AS dist, ->chunk_of->distilled AS d FROM chunk"
_MAX_K = 100  # teto de resultados por busca — clamp anti-DoS na borda (escala pessoal, folgado).
# teto de itens por página de browse — start/limit vêm de query param (hostis na borda).
_MAX_PAGE = 100


@dataclass(frozen=True)
class Chunk:
    """Unidade de embedding a inserir (ADR-0008: um vetor por registro).

    `embedding` já vem gerado pelo chamador — a geração é do M6, não da store.
    `dim` é proveniência (ADR-0006), mantida por registro mesmo sendo o tamanho fixo.
    """

    text: str
    seq: int
    embedding: Sequence[float]
    model: str
    dim: int
    task_type: str


@dataclass(frozen=True)
class SearchHit:
    """Resultado de busca vetorial: o conhecimento (distilled), não o fragmento órfão."""

    distilled: RecordID
    chunk: RecordID
    score: float


def normalize_entity(name: str) -> str:
    """Normaliza nome de entidade para match exato: NFC + casefold + colapso de whitespace.

    Sem fuzzy (decisão vigente): "Python", "  python " e "PYTHON" colapsam no mesmo
    normalizado — logo no mesmo record ID e no mesmo índice UNIQUE.
    """
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def _rid(table: str, natural_key: str) -> RecordID:
    """Record ID determinístico (sha256 hex da chave natural) — idempotência por id."""
    return RecordID(table, hashlib.sha256(natural_key.encode("utf-8")).hexdigest())


def _fresh(table: str) -> RecordID:
    """Record ID novo para um evento (destilação, execução): cada chamada é distinta."""
    return RecordID(table, secrets.token_hex(16))


def _require_dim_matches(ch: Chunk) -> None:
    """Rejeita chunk cujo `dim` de proveniência mente sobre o tamanho real do embedding.

    O schema garante embedding==768, mas não que o `dim` registrado seja verdadeiro;
    um dim mentiroso corromperia a proveniência do re-embed (ADR-0006)."""
    if len(ch.embedding) != ch.dim:
        raise ValueError(
            f"chunk seq={ch.seq}: dim={ch.dim} não bate com len(embedding)={len(ch.embedding)}"
        )


def upsert_source(db: Any, *, kind: str, canonical: str, title: str | None = None) -> RecordID:
    """Cria/atualiza uma source pela chave natural (identificador canônico). Idempotente."""
    rid = _rid("source", canonical)
    db.query(
        "UPSERT $r SET kind = $kind, canonical = $canonical, title = $title;",
        {"r": rid, "kind": kind, "canonical": canonical, "title": title},
    )
    return rid


def upsert_item(
    db: Any,
    *,
    source: RecordID,
    external_id: str,
    content: str,
    url: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    run: RecordID | None = None,
) -> RecordID:
    """Cria/atualiza um item (chave natural: source + external_id, D4) e a aresta
    `item -[from_source]-> source`. Idempotente (2x = no-op): a aresta é reescrita
    (DELETE + RELATE) para não duplicar; tudo numa transação atômica.

    `run` (opcional) registra a proveniência de execução `item -[collected_by]-> run`
    (ADR-0008 §VI): quem coletou o item. Semântica de re-coleta = last-wins (DELETE +
    RELATE na MESMA transação, como `from_source`). Um upsert SEM run não toca a aresta
    — não pode apagar a proveniência de uma coleta anterior nem inventar uma agora."""
    rid = _rid("item", f"{source}|{external_id}")
    statements = [
        "UPSERT $r SET external_id = $external_id, content = $content, "
        "url = $url, title = $title, metadata = $metadata",
        "DELETE $r->from_source",
        "RELATE $r->from_source->$source",
    ]
    params: dict[str, Any] = {
        "r": rid,
        "external_id": external_id,
        "content": content,
        "url": url,
        "title": title,
        "metadata": metadata,
        "source": source,
    }
    if run is not None:
        # Só reescreve collected_by quando HÁ run: DELETE incondicional só entra
        # acompanhado do RELATE (last-wins), nunca sozinho — senão um upsert sem
        # run apagaria a proveniência de quem coletou.
        statements += ["DELETE $r->collected_by", "RELATE $r->collected_by->$run"]
        params["run"] = run
    run_transaction(db, statements, params)
    return rid


def get_or_create_entity(db: Any, *, name: str, kind: str | None = None) -> RecordID:
    """Resolve uma entity pelo nome normalizado, criando se não existir. Idempotente."""
    normalized = normalize_entity(name)
    rid = _rid("entity", normalized)
    db.query(
        "UPSERT $r SET name = $name, normalized = $normalized, kind = $kind;",
        {"r": rid, "name": name, "normalized": normalized, "kind": kind},
    )
    return rid


def insert_distilled(
    db: Any,
    *,
    item: RecordID,
    summary: str,
    chunks: Sequence[Chunk],
    claims: Sequence[str] | None = None,
    run: RecordID | None = None,
    entities: Sequence[RecordID] | None = None,
) -> RecordID:
    """Insere um destilado e tudo que o acompanha numa ÚNICA transação atômica:
    o record `distilled`, `derived_from -> item`, cada `chunk` + `chunk_of ->
    distilled`, `produced_by -> run` (se dado) e `mentions -> entity` (se dados).
    Vetor de dimensão != 768 é rejeitado na borda e reverte a transação inteira.

    Levanta `ValueError` se a proveniência de um chunk (`dim`) não bate com o vetor
    real (`len(embedding)`): o schema garante `embedding` == 768, mas não que o `dim`
    registrado seja verdadeiro — um `dim` mentiroso corromperia a proveniência do re-embed."""
    distilled = _fresh("distilled")
    stmts = [
        "CREATE $d SET summary = $summary, claims = $claims",
        "RELATE $d->derived_from->$item",
    ]
    params: dict[str, Any] = {
        "d": distilled,
        "item": item,
        "summary": summary,
        "claims": list(claims) if claims else [],
    }
    for i, ch in enumerate(chunks):
        _require_dim_matches(ch)
        cid = _rid("chunk", f"{distilled}|{ch.seq}")
        stmts.append(
            f"CREATE $c{i} SET text = $ct{i}, seq = $cs{i}, embedding = $ce{i}, "
            f"model = $cm{i}, dim = $cd{i}, task_type = $ck{i}"
        )
        stmts.append(f"RELATE $c{i}->chunk_of->$d")
        params |= {
            f"c{i}": cid,
            f"ct{i}": ch.text,
            f"cs{i}": ch.seq,
            f"ce{i}": list(ch.embedding),
            f"cm{i}": ch.model,
            f"cd{i}": ch.dim,
            f"ck{i}": ch.task_type,
        }
    if run is not None:
        stmts.append("RELATE $d->produced_by->$run")
        params["run"] = run
    for i, ent in enumerate(entities or []):
        stmts.append(f"RELATE $d->mentions->$e{i}")
        params[f"e{i}"] = ent
    run_transaction(db, stmts, params)
    return distilled


@dataclass(frozen=True)
class ProvenanceItem:
    """Um item de origem de um distilled, já resolvido até a source (ADR-0013 §8.5):
    substitui a antiga `provenance` (só ids) — o CLI exibe estes campos direto,
    sem 2ª consulta."""

    external_id: str
    url: str | None
    title: str | None
    source_canonical: str
    source_title: str | None
    source_kind: str


@dataclass(frozen=True)
class RunRef:
    """Referência enxuta a um run que produziu um distilled (worker + status)."""

    worker: str
    status: str


@dataclass(frozen=True)
class DistilledView:
    """Visão completa de proveniência de UM distilled: distilled -> item -> source
    e distilled -> run, numa leitura. Alimenta tanto `kubo query` (usa `.summary`)
    quanto `kubo show --provenance` (ADR-0013 §8.5)."""

    id: RecordID
    summary: str
    claims: list[str]
    items: list[ProvenanceItem]
    runs: list[RunRef]


def read_distilled(db: Any, distilled: RecordID) -> DistilledView | None:
    """Devolve a visão completa de proveniência de um distilled (item(s) + source(s)
    + run(s)), ou None se o id não existe. Substitui `provenance` (ADR-0013 §8.5).

    # ponytail: poucos round-trips por distilled (1 base + 1 por item + 1 por
    # source + 1 por run) — escala pessoal, um distilled costuma ter 1 item.
    # Composição em Python em vez de projeção SurrealQL aninhada (destructure
    # dentro de destructure é o statement mais frágil/ilegível do repo e o
    # comportamento de alias aninhado tem quirk no v3.1.5 — decisão do advisor).
    """
    base = db.query("SELECT summary, claims FROM $d;", {"d": distilled})
    if not base:
        return None
    summary: str = base[0]["summary"]
    claims: list[str] = list(base[0].get("claims") or [])

    item_rows = db.query("SELECT VALUE ->derived_from->item FROM $d;", {"d": distilled})
    item_ids: list[RecordID] = list(item_rows[0]) if item_rows else []
    items: list[ProvenanceItem] = []
    for item_id in item_ids:
        item_row = db.query(
            "SELECT external_id, url, title, ->from_source->source AS source FROM $item;",
            {"item": item_id},
        )[0]
        source_ids: list[RecordID] = list(item_row.get("source") or [])
        if not source_ids:
            raise ValueError(f"item {item_id} sem from_source->source (proveniência incompleta)")
        source_row = db.query(
            "SELECT canonical, title, kind FROM $source;", {"source": source_ids[0]}
        )[0]
        items.append(
            ProvenanceItem(
                external_id=item_row["external_id"],
                url=item_row.get("url"),
                title=item_row.get("title"),
                source_canonical=source_row["canonical"],
                source_title=source_row.get("title"),
                source_kind=source_row["kind"],
            )
        )

    run_rows = db.query("SELECT VALUE ->produced_by->run FROM $d;", {"d": distilled})
    run_ids: list[RecordID] = list(run_rows[0]) if run_rows else []
    runs = [
        RunRef(worker=r["worker"], status=r["status"])
        for run_id in run_ids
        for r in db.query("SELECT worker, status FROM $run;", {"run": run_id})
    ]

    return DistilledView(id=distilled, summary=summary, claims=claims, items=items, runs=runs)


@dataclass(frozen=True)
class DistilledListItem:
    """Card do browse de destilados (UI fase 2): id, summary e — desde a sessão 0010
    (E3) — o título do item (via `derived_from`, com fallback na 1ª linha do summary
    quando o item não tem título), a fonte (a 2 hops) e a data. A cadeia de
    proveniência completa continua vindo de `read_distilled`."""

    id: RecordID
    summary: str
    title: str
    source_canonical: str | None
    source_kind: str | None
    created_at: str


# Colunas do card resolvidas numa projeção (probe 0010: 1-hop p/ título, 2-hop
# encadeado p/ fonte — funciona no v3.1.5; travessia volta ARRAY mesmo p/ 1:1, daí
# o `_unwrap`). created_at entra porque o ORDER BY do v3 exige o campo na seleção.
_CARD_COLS = (
    "id, summary, created_at, "
    "->derived_from->item.title AS item_title, "
    "->derived_from->item->from_source->source.canonical AS src_canonical, "
    "->derived_from->item->from_source->source.kind AS src_kind"
)


def _first_line(text: str) -> str:
    """1ª linha não-vazia de um texto — fallback de título quando o item não tem `title` (E3)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return text.strip()


def _unwrap(values: Any) -> Any | None:
    """1º valor de uma travessia de grafo, que volta array mesmo para relação 1:1
    (`->edge->node.field` = lista); None se vazia ou `[None]` (campo opcional NULL)."""
    if not values:
        return None
    return values[0]


def _card(row: dict[str, Any]) -> DistilledListItem:
    """Monta um `DistilledListItem` a partir de uma linha projetada com `_CARD_COLS`."""
    item_title = _unwrap(row.get("item_title"))
    return DistilledListItem(
        id=row["id"],
        summary=row["summary"],
        title=item_title if item_title else _first_line(row["summary"]),
        source_canonical=_unwrap(row.get("src_canonical")),
        source_kind=_unwrap(row.get("src_kind")),
        created_at=str(row["created_at"]),
    )


def list_distilled(db: Any, *, limit: int, start: int) -> list[DistilledListItem]:
    """Página do acervo de destilados, mais recentes primeiro (browse da UI fase 2).

    `limit`/`start` vêm de query param (hostis): o clamp fica aqui, a store é a
    fronteira em que a spec confia — `limit` em [1, _MAX_PAGE], `start` >= 0. Ordem
    `created_at DESC, id`: recência para o browse, id como desempate determinístico
    para paginação estável quando dois destilados têm o mesmo created_at."""
    limit = max(1, min(int(limit), _MAX_PAGE))
    start = max(0, int(start))
    # LIMIT/START não aceitam bind param nesta versão do SurrealDB (o parser exige
    # literal, mesmo caso do <|k,ef|> em `search`). limit/start são ints já clampados
    # pela store — não conteúdo coletado; interpolação é segura aqui.
    query = (
        f"SELECT {_CARD_COLS} FROM distilled "  # noqa: S608
        f"ORDER BY created_at DESC, id LIMIT {limit} START {start};"
    )
    return [_card(r) for r in db.query(query)]


@dataclass(frozen=True)
class EntityListItem:
    """Linha da lista de entidades (UI fase 2, E2): glifo por `kind`, nome, badge de
    tipo e contagem de menções. SEM sparkline e SEM relações — corte por dado
    inexistente (E2): `relates_to` não tem produtor na fase 1 e `mentions` não tem
    timestamp, então tendência/relação seriam inventadas."""

    id: RecordID
    name: str
    kind: str | None
    mentions: int


def list_entities(db: Any, *, limit: int, start: int) -> list[EntityListItem]:
    """Página de entidades, mais mencionadas primeiro (E2).

    Menções = `array::len(<-mentions)` — conta as arestas incoming sem materializar
    os destilados (uma aresta por menção; `insert_distilled` não deduplica o par, mas
    a contagem de arestas é a verdade do grafo). `limit`/`start` clampados na borda
    como nas outras listas; ORDER BY sobre o alias computado é provado pelo probe 0010."""
    limit = max(1, min(int(limit), _MAX_PAGE))
    start = max(0, int(start))
    query = (
        "SELECT id, name, kind, array::len(<-mentions) AS mentions FROM entity "  # noqa: S608
        f"ORDER BY mentions DESC, name LIMIT {limit} START {start};"
    )
    return [
        EntityListItem(id=r["id"], name=r["name"], kind=r.get("kind"), mentions=int(r["mentions"]))
        for r in db.query(query)
    ]


@dataclass(frozen=True)
class EntityView:
    """Detalhe de uma entidade (E2): a entidade + os destilados que a mencionam, como
    cards (mesma resolução título/fonte/data do browse). Sem relações (E2)."""

    id: RecordID
    name: str
    kind: str | None
    mentions: int
    distilled: list[DistilledListItem]


def read_entity(db: Any, entity: RecordID) -> EntityView | None:
    """Devolve a entidade + os destilados que a mencionam (cards), ou None se o id não
    existe. Os destilados vêm por `<-mentions<-distilled` projetado com `_CARD_COLS`
    numa leitura (probe 0010), mais recentes primeiro."""
    base = db.query("SELECT name, kind, array::len(<-mentions) AS mentions FROM $e;", {"e": entity})
    if not base:
        return None
    cards = db.query(
        f"SELECT {_CARD_COLS} FROM $e<-mentions<-distilled "  # noqa: S608
        "ORDER BY created_at DESC, id;",
        {"e": entity},
    )
    return EntityView(
        id=entity,
        name=base[0]["name"],
        kind=base[0].get("kind"),
        mentions=int(base[0]["mentions"]),
        distilled=[_card(r) for r in cards],
    )


@dataclass(frozen=True)
class DashboardCounts:
    """Contagens do acervo para o Painel (UI fase 2): quantos destilados, itens e fontes."""

    distilled: int
    items: int
    sources: int


def _count(db: Any, table: str) -> int:
    """Contagem de registros de uma tabela. `table` é literal interno da store, nunca
    entrada externa — a UI não escolhe tabela."""
    rows = db.query(f"SELECT count() FROM {table} GROUP ALL;")  # noqa: S608
    return int(rows[0]["count"]) if rows else 0


def dashboard_counts(db: Any) -> DashboardCounts:
    """Contagens do acervo (distilled/item/source) para o Painel."""
    return DashboardCounts(
        distilled=_count(db, "distilled"),
        items=_count(db, "item"),
        sources=_count(db, "source"),
    )


@dataclass(frozen=True)
class RunSummary:
    """Linha de 'últimas execuções' do Painel: worker, status e o `error.kind` que
    discrimina a falha (rate_limit, malformed_output…), com os carimbos de tempo."""

    worker: str
    status: str
    error_kind: str | None
    started_at: str
    finished_at: str | None


def recent_runs(db: Any, *, limit: int) -> list[RunSummary]:
    """Últimas `limit` execuções, mais recentes primeiro (Painel).

    `error.kind` é projetado como `error_kind` (None quando `error` é NONE); os
    carimbos viram string para a view exibir sem lidar com o tipo de datetime do SDK.
    `limit` é clampado (mesma borda de `list_distilled`) e interpolado como literal
    (LIMIT não aceita bind; started_at precisa estar na projeção — quirk do v3)."""
    limit = max(1, min(int(limit), _MAX_PAGE))
    query = (
        "SELECT worker, status, error.kind AS error_kind, started_at, finished_at "  # noqa: S608
        f"FROM run ORDER BY started_at DESC LIMIT {limit};"
    )
    rows = db.query(query)
    return [
        RunSummary(
            worker=r["worker"],
            status=r["status"],
            error_kind=r.get("error_kind"),
            started_at=str(r["started_at"]),
            finished_at=str(r["finished_at"]) if r.get("finished_at") is not None else None,
        )
        for r in rows
    ]


# Chaves de `run.stats` que significam "unidades produzidas", por worker (E6): o feed
# grava `items`, o distiller grava `distilled`. Ordem = precedência na derivação.
_STATS_ITEM_KEYS = ("items", "distilled")


def _run_items(stats: dict[str, Any]) -> int | None:
    """Nº de itens produzidos por um run, derivado do `stats` de shape variável (E6):
    a 1ª chave de `_STATS_ITEM_KEYS` com valor > 0. None = fallback gracioso (worker
    sem contador conhecido ou run que não produziu nada) — a view simplesmente omite."""
    for key in _STATS_ITEM_KEYS:
        value = stats.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


@dataclass(frozen=True)
class RunListItem:
    """Linha da tela de Execuções (E6): worker, status e — quando falha — o erro
    ESTRUTURADO completo (kind + objeto para o painel expansível). `error_kind` é o
    atalho para o badge que discrimina `quota` de falha real, SEM reclassificar o
    `status` armazenado. `items` vem de `stats` (fallback gracioso). Sem coluna de
    fluxo (desvio E6: `flow` não existe na fase 1)."""

    worker: str
    status: str
    error_kind: str | None
    error: dict[str, Any] | None
    items: int | None
    started_at: str
    finished_at: str | None


def list_runs(db: Any, *, limit: int, start: int) -> list[RunListItem]:
    """Página de execuções, mais recentes primeiro (tela de Execuções, fase 2).

    Projeta o `error` inteiro (não só o kind) para o painel expansível — o objeto é
    contratualmente seguro (ErrorInfo: `extra=forbid`, `message`<=500 sem conteúdo
    coletado). `items` é derivado de `stats` (E6). `limit`/`start` clampados na borda;
    LIMIT/START interpolados como literal (não aceitam bind, quirk do v3)."""
    limit = max(1, min(int(limit), _MAX_PAGE))
    start = max(0, int(start))
    query = (
        "SELECT worker, status, error, error.kind AS error_kind, stats, "  # noqa: S608
        f"started_at, finished_at FROM run ORDER BY started_at DESC LIMIT {limit} START {start};"
    )
    rows = db.query(query)
    return [
        RunListItem(
            worker=r["worker"],
            status=r["status"],
            error_kind=r.get("error_kind"),
            error=r.get("error"),
            items=_run_items(r.get("stats") or {}),
            started_at=str(r["started_at"]),
            finished_at=str(r["finished_at"]) if r.get("finished_at") is not None else None,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class SourceInfo:
    """Uma source do grafo (id + chave natural + classificação), para consumidores
    que LISTAM ou RESOLVEM sources sem regravá-las: o import one-off (resolve a source
    de um item por canonical) e a UI da fase 1 (lista/agrupa por kind)."""

    id: RecordID
    canonical: str
    kind: str
    title: str | None


def list_sources(db: Any) -> list[SourceInfo]:
    """Lista todas as sources (id, canonical, kind, title) numa leitura.

    Porta única para 'quais sources existem' — o import resolve a source de um item
    pelo canonical a partir daqui (sem upsert, para não mutar dado vivo) e a fase 1
    lista por aqui; substitui SELECTs de `source` espalhados fora da store (inv. 2)."""
    rows = db.query("SELECT id, canonical, kind, title FROM source;")
    return [
        SourceInfo(id=r["id"], canonical=r["canonical"], kind=r["kind"], title=r.get("title"))
        for r in rows
    ]


@dataclass(frozen=True)
class SourceStat:
    """Uma fonte com os fatos da coleta (UI Fontes, fase 2): quantos itens acumulou e
    o carimbo da ÚLTIMA coleta. `last_collected_at` alimenta o badge de recência (E4:
    mostra o FATO 'última coleta há Nd', não julga 'saúde'); None quando a fonte ainda
    não coletou nada."""

    id: RecordID
    canonical: str
    kind: str
    title: str | None
    items: int
    last_collected_at: str | None


def sources_with_stats(db: Any) -> list[SourceStat]:
    """Lista as fontes com contagem de itens e o carimbo da última coleta (E4).

    `array::len(<-from_source<-item)` conta os itens da fonte; `time::max(...)` pega o
    `collected_at` mais recente (math::max explode em datetime — probe 0010). Fonte sem
    item nenhum → agregação NONE → `last_collected_at` None (o badge trata o estado)."""
    rows = db.query(
        "SELECT id, canonical, kind, title, "
        "array::len(<-from_source<-item) AS items, "
        "time::max(<-from_source<-item.collected_at) AS last FROM source;"
    )
    return [
        SourceStat(
            id=r["id"],
            canonical=r["canonical"],
            kind=r["kind"],
            title=r.get("title"),
            items=int(r["items"]),
            last_collected_at=str(r["last"]) if r.get("last") is not None else None,
        )
        for r in rows
    ]


def item_index(db: Any) -> dict[str, RecordID]:
    """Mapa `external_id -> item` de todos os itens numa leitura.

    O import resolve `derived_from` (distilled -> item pela chave natural do legado)
    e detecta itens já presentes por aqui — sem 1 query por linha nem SELECT de `item`
    espalhado fora da store (invariante 2). Colisão de external_id entre sources não é
    esperada (é parte da chave natural do item) e ligaria uma destilação ao item
    errado — então é LOGADA (warning), não descartada em silêncio; a 1ª ocorrência
    vence (escolha determinística: a query ordena por id)."""
    index: dict[str, RecordID] = {}
    for r in db.query("SELECT external_id, id FROM item ORDER BY id;"):
        ext = r.get("external_id")
        if not ext:
            continue
        if ext in index:
            _log.warning("store.item_index.external_id_collision", external_id=ext)
            continue
        index[ext] = r["id"]
    return index


def distilled_for(db: Any, item: RecordID) -> list[RecordID]:
    """Destilados que derivam de um item (travessia item <-derived_from<- distilled).

    Leitura mínima que o import one-off usa para pular itens já destilados —
    `insert_distilled` NÃO é idempotente (cada chamada cria um evento novo), então
    sem esta checagem re-rodar o corpus duplicaria os destilados. O M6 precisa da
    mesma travessia para o backfill de embeddings."""
    rows = db.query("SELECT <-derived_from<-distilled AS d FROM $item;", {"item": item})
    return list(rows[0]["d"]) if rows else []


def attach_chunks(db: Any, *, distilled: RecordID, chunks: Sequence[Chunk]) -> None:
    """Anexa chunks já embeddados a um `distilled` EXISTENTE, sem tocar em nada que
    já pende dele (ADR-0013 §VI) — o backfill dos 935 destilados legados (import
    Neon, ADR-0012) usa isto para tornar buscável um `distilled` inserido com
    `chunks=[]`.

    Anexa, não deleta+recria: delete+recria destruiria `produced_by -> run` e
    `mentions -> entity` já gravados — a proveniência é o produto. Guarda de
    idempotência fica DENTRO desta função (não no loop do chamador): um `distilled`
    que já tem QUALQUER `chunk_of` incoming é no-op explícito, não soma — a
    retomabilidade do backfill não depende da disciplina do script chamador. Reusa a mesma validação
    `dim == len(embedding)` de `insert_distilled`; dim mentiroso levanta `ValueError`
    e reverte a transação inteira (nenhum chunk gravado)."""
    if not chunks:
        return
    # ponytail: guarda por leitura+escrita não-atômica; ok no backfill one-off
    # single-process, revisar se ganhar concorrência.
    existing = db.query("SELECT VALUE array::len(<-chunk_of) FROM $d;", {"d": distilled})
    if existing and existing[0] > 0:
        _log.info("store.attach_chunks.skip_has_chunks", distilled=str(distilled))
        return
    for ch in chunks:
        _require_dim_matches(ch)
    stmts: list[str] = []
    params: dict[str, Any] = {"d": distilled}
    for i, ch in enumerate(chunks):
        cid = _rid("chunk", f"{distilled}|{ch.seq}")
        stmts.append(
            f"CREATE $c{i} SET text = $ct{i}, seq = $cs{i}, embedding = $ce{i}, "
            f"model = $cm{i}, dim = $cd{i}, task_type = $ck{i}"
        )
        stmts.append(f"RELATE $c{i}->chunk_of->$d")
        params |= {
            f"c{i}": cid,
            f"ct{i}": ch.text,
            f"cs{i}": ch.seq,
            f"ce{i}": list(ch.embedding),
            f"cm{i}": ch.model,
            f"cd{i}": ch.dim,
            f"ck{i}": ch.task_type,
        }
    run_transaction(db, stmts, params)


def distilled_without_chunks(db: Any) -> list[tuple[RecordID, str]]:
    """Lista `(distilled_id, summary)` de todo `distilled` SEM nenhum `chunk_of`
    incoming — os candidatos ao backfill de embeddings (ADR-0013 §VI/§VII: os 935
    destilados legados do import Neon foram inseridos com `chunks=[]`).

    Leitura que torna o backfill script one-off retomável por construção: re-rodar
    só processa quem ainda não tem chunk (condição transitória do ADR-0012 §IV).
    Par de leitura de `distilled_for` (item -> distilled); aqui a direção é
    distilled -> ausência de chunk."""
    rows = db.query("SELECT id, summary FROM distilled WHERE array::len(<-chunk_of) = 0;")
    return [(r["id"], r["summary"]) for r in rows]


def items_without_distilled(db: Any, *, limit: int) -> list[tuple[RecordID, str | None, str]]:
    """Lista `(item_id, title, content)` dos candidatos à destilação NOVA: todo `item`
    SEM nenhum `derived_from` incoming E com `content` não-vazio (ADR-0013 §III.1/§III.7).

    O filtro de conteúdo vazio/só-whitespace é precondição do run vivo (§III): mandar o
    vazio ao LLM alucinaria um summary que persistiria COM proveniência, e o item sairia
    do funil para sempre — conhecimento fabricado com carimbo de origem. Os ~536 link-posts
    do Hacker News (só URL, `content=""`) ficam de fora deliberadamente. Propriedade: se o
    content for preenchido depois (harvest futuro), o item REENTRA no funil automaticamente
    — o filtro é sobre o estado atual do item, não uma marca permanente.

    Ordem determinística (por id) e limitada por `limit`: o worker de distilação consome
    esta lista em lotes previsíveis, sem depender de ordem de inserção do servidor. Par de
    leitura de `distilled_for` (item -> distilled); aqui a direção é item -> ausência de
    destilado."""
    rows = db.query(
        "SELECT id, title, content FROM item "
        'WHERE array::len(<-derived_from) = 0 AND string::trim(content) != "" '
        "ORDER BY id LIMIT $limit;",
        {"limit": limit},
    )
    return [(r["id"], r.get("title"), r["content"]) for r in rows]


def search(db: Any, *, embedding: Sequence[float], k: int) -> list[SearchHit]:
    """Busca vetorial KNN sobre `chunk`, resolvendo cada acerto ao seu `distilled`.

    Função ÚNICA de busca (ADR-0005): sempre injeta EF = max(k*4, 40); nunca aceita
    SurrealQL cru com `<|K|>` de fora. Nenhuma constante deriva do smoke (ADR-0006).
    O operador `<|k,ef|>` exige inteiros literais (não aceita bind) — k/ef são ints
    computados pela store, não conteúdo coletado; o vetor de busca vai por bind param.
    """
    # Clamp na borda: a store é a fronteira em que a spec confia. Um k enorme faria
    # ef = k*4 explodir e degradar o nó HNSW (DoS). Teto de 100 é folgado p/ escala pessoal.
    k = max(1, min(int(k), _MAX_K))
    ef = max(k * 4, 40)
    # k/ef são ints computados pela store (não conteúdo coletado); o vetor vai por bind
    # param. O operador <|k,ef|> exige inteiros literais, não aceita bind (ADR-0005).
    query = f"{_KNN_SELECT} WHERE embedding <|{k},{ef}|> $q ORDER BY dist;"  # noqa: S608
    rows = db.query(query, {"q": list(embedding)})
    return [
        SearchHit(distilled=row["d"][0], chunk=row["id"], score=float(row["dist"])) for row in rows
    ]


def start_run(db: Any, *, worker: str) -> RecordID:
    """Abre um `run` (status 'running', started_at). Retorna o id para finish/fail.

    `stats` fica no default {} do schema até um produtor exigir escrevê-lo (M5+) —
    a store não expõe superfície sem teste que a exija (anti-especulação, plano §3.2).
    """
    rid = _fresh("run")
    db.query("CREATE $r SET worker = $worker, status = 'running';", {"r": rid, "worker": worker})
    return rid


def finish_run(db: Any, run: RecordID, *, stats: dict[str, Any] | None = None) -> None:
    """Fecha um `run` com sucesso (status 'ok', finished_at, `stats` opcional).

    `stats` são contadores da execução (ex.: itens vistos/gravados) — vão para o
    campo FLEXIBLE `run.stats`. A forma tipada vem do contrato (`RunResult.stats`,
    ADR-0009); a store só recebe o dict já serializado. Ausente = `{}` (default do
    schema preservado)."""
    db.query(
        "UPDATE $r SET status = 'ok', finished_at = time::now(), stats = $stats;",
        {"r": run, "stats": dict(stats) if stats else {}},
    )


def fail_run(db: Any, run: RecordID, *, error: dict[str, Any]) -> None:
    """Fecha um `run` com falha (status 'error', finished_at, erro estruturado)."""
    db.query(
        "UPDATE $r SET status = 'error', finished_at = time::now(), error = $error;",
        {"r": run, "error": dict(error)},
    )
