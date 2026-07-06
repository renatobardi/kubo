"""Camada pura de mapeamento do import legado (sessão 0007) — sem I/O, sem DB.

Testa SÓ a transformação legado→args-da-store com valores explícitos (não linhas
do Neon): a fronteira tipada isola estes testes do schema real do Neon, que ainda
não conhecemos. O SQL e os adapters `row -> tipo de borda` são a única peça adiada
para quando o dono entregar o `pg_dump --schema-only` (checkpoint do plano 0007).
"""

from __future__ import annotations

from scripts import neon_import as ni


def test_feed_external_id_prefers_guid_then_link() -> None:
    """Cadeia guid->link IDÊNTICA à do feed worker (_external_id): guid quando há,
    senão link — para o período de sobreposição com a coleta viva DEDUPLICAR."""
    assert ni.feed_external_id("guid-1", "https://x/a") == "guid-1"
    assert ni.feed_external_id(None, "https://x/a") == "https://x/a"
    assert ni.feed_external_id("", "https://x/a") == "https://x/a"


def test_feed_external_id_none_when_no_identity() -> None:
    """Sem guid nem link não há chave estável — None (o corpus conta skipped_invalid,
    nunca inventa id, senão a idempotência do re-run quebra)."""
    assert ni.feed_external_id(None, None) is None
    assert ni.feed_external_id("", "") is None


def test_legacy_metadata_preserves_original_timestamp_and_tags() -> None:
    """collected_at é READONLY (recebe a data do import); o timestamp ORIGINAL e as
    tags vão para o namespace `legacy` de item.metadata (FLEXIBLE), custo zero."""
    md = ni.legacy_metadata(
        published_at="2021-05-01T10:00:00Z", tags=["ml", "rust"], legacy_id="42"
    )
    assert md == {
        "legacy": {"published_at": "2021-05-01T10:00:00Z", "tags": ["ml", "rust"], "id": "42"}
    }


def test_legacy_metadata_empty_when_nothing_to_preserve() -> None:
    """Sem timestamp/tags/id não polui o metadata com um `legacy` vazio."""
    assert ni.legacy_metadata(published_at=None, tags=[]) == {}


def test_item_args_builds_upsert_kwargs() -> None:
    """item_args monta os args de upsert_item preservando o original no metadata."""
    args = ni.item_args(
        external_id="vid-1",
        content="transcrição bruta",
        url="https://y/w?v=1",
        title="Aula 1",
        published_at="2020-01-02T00:00:00Z",
        tags=["python"],
        legacy_id="vid-1",
    )
    assert args is not None
    assert args.external_id == "vid-1"
    assert args.content == "transcrição bruta"
    assert args.url == "https://y/w?v=1"
    assert args.metadata == {
        "legacy": {"published_at": "2020-01-02T00:00:00Z", "tags": ["python"], "id": "vid-1"}
    }


def test_item_args_none_without_external_id() -> None:
    """Sem external_id não há item idempotente — None (skipped_invalid contado)."""
    assert (
        ni.item_args(
            external_id=None, content="x", url=None, title=None, published_at=None, tags=[]
        )
        is None
    )


def test_join_transcript_concatenates_in_order_dropping_empties() -> None:
    """Junta os segmentos JÁ ordenados por seq (ordenação/agrupamento por vídeo é do
    I/O em streaming); descarta vazios/whitespace e une por espaço único."""
    assert ni.join_transcript(["Olá", "  ", "mundo", "", " tudo bem "]) == "Olá mundo tudo bem"
    assert ni.join_transcript([]) == ""


def test_distilled_args_requires_summary() -> None:
    """distilled.summary é NOT NULL no schema: sem summary (ou só whitespace) -> None
    (skipped_invalid); claims default para lista vazia."""
    ok = ni.distilled_args(summary="resumo", claims=["c1", "c2"])
    assert ok is not None and ok.summary == "resumo" and ok.claims == ["c1", "c2"]
    assert ni.distilled_args(summary="resumo").claims == []  # type: ignore[union-attr]
    assert ni.distilled_args(summary=None) is None
    assert ni.distilled_args(summary="   ") is None


def test_feed_match_suggestion_matches_by_normalized_url_only() -> None:
    """A sugestão bate por URL normalizada (casefold + sem barra final) — mas é só
    dica: a decisão é do dono. http vs https NÃO casam (esquemas diferentes)."""
    graph = [
        ni.GraphSource(id="source:a", canonical="https://blog.x/feed", title="X"),
        ni.GraphSource(id="source:b", canonical="https://y.dev/rss/", title="Y"),
    ]

    def sugg(url: str) -> str | None:
        return ni.feed_match_suggestion(ni.LegacyFeed("id", "name", url), graph)

    assert sugg("https://blog.x/feed") == "source:a"
    # barra final e caixa divergentes ainda casam (normalização de sugestão):
    assert sugg("HTTPS://Y.dev/rss") == "source:b"
    # esquema diferente não casa — vira "sem sugestão", o dono decide:
    assert sugg("http://blog.x/feed") is None


def test_render_feed_map_lists_both_sides_and_flags_suggestion_as_hint() -> None:
    """O relatório do mapa mostra os dois lados e deixa explícito que a sugestão é
    dica, não decisão (instrução do dono no checkpoint)."""
    feeds = [ni.LegacyFeed("1", "Blog X", "https://blog.x/feed")]
    graph = [ni.GraphSource(id="source:a", canonical="https://blog.x/feed", title="X")]
    out = ni.render_feed_map(feeds, graph)
    assert "SÓ dica" in out and "o dono confirma" in out
    assert "Blog X" in out and "source:a" in out


def test_recon_report_accounts_every_source_row() -> None:
    """Reconciliação: imported + preexisting + rejeitados == origem -> reconciled.
    Uma linha a menos (buraco) marca DISCREPÂNCIA no render (nada some em silêncio)."""
    r = ni.ReconReport(corpus="videos", source_count=3)
    r.record_imported()
    r.record_preexisting()
    r.record_skipped("vid-x", "sem external_id")
    assert r.accounted == 3 and r.reconciled is True
    assert "RECONCILIADO" in r.render()
    assert "vid-x: sem external_id" in r.render()


def test_recon_report_flags_discrepancy_when_rows_unaccounted() -> None:
    """Origem=5 mas só 2 contabilizadas -> render acusa 3 não contabilizadas."""
    r = ni.ReconReport(corpus="items", source_count=5)
    r.record_imported()
    r.record_imported()
    assert r.reconciled is False
    assert "DISCREPÂNCIA: 3" in r.render()
