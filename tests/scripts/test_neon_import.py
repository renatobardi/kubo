"""Camada pura de mapeamento do import legado (sessão 0007) — sem I/O, sem DB.

Testa SÓ a transformação legado→args-da-store com valores explícitos (não linhas
do Neon): a fronteira tipada isola estes testes do schema real do Neon. Os handlers
(SQL contra o schema legado) são escritos e validados na sessão de execução, contra
o Neon vivo (ADR-0012 §VII).
"""

from __future__ import annotations

import pytest

from kubo.errors import ConfigError
from scripts import neon_import as ni


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


def test_legacy_metadata_extra_carries_corpus_specifics() -> None:
    """`extra` leva o que é específico do corpus (ex.: sender de email) pro namespace
    legacy; valores None em extra são ignorados (não poluem o metadata)."""
    md = ni.legacy_metadata(
        published_at=None, tags=[], legacy_id="m-1", extra={"sender": "a@b.com", "cc": None}
    )
    assert md == {"legacy": {"id": "m-1", "sender": "a@b.com"}}


def test_pick_content_returns_first_nonempty_in_priority_order() -> None:
    """Prioridade transcript > corpo > "" (ADR-0012 §VII): o primeiro não-vazio vence;
    whitespace conta como vazio; nada preenchido -> "" (item entra como âncora)."""
    assert ni.pick_content("transcrição", "corpo") == "transcrição"
    assert ni.pick_content(None, "corpo") == "corpo"
    assert ni.pick_content("   ", "", "excerpt") == "excerpt"
    assert ni.pick_content(None, "", "  ") == ""


def test_claims_from_structured_extracts_claim_texts() -> None:
    """structured.claims[].text -> claims; evidence/ts_start são perda consciente.
    Robusto a lixo (conteúdo legado hostil): não-dict, sem claims, ou claim malformado
    viram lista vazia / são ignorados, nunca explodem."""
    structured = {
        "claims": [
            {"text": "fato 1", "evidence": "cit", "ts_start": 10},
            {"text": "  fato 2  ", "evidence": ""},
            {"text": "", "evidence": "vazio ignorado"},
            {"evidence": "sem text"},
            "não é dict",
        ]
    }
    assert ni.claims_from_structured(structured) == ["fato 1", "  fato 2  "]
    assert ni.claims_from_structured(None) == []
    assert ni.claims_from_structured({"claims": "não é lista"}) == []
    assert ni.claims_from_structured({}) == []


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
    # esquema diferente não casa (http literal é o caso de teste) — o dono decide:
    assert sugg("http://blog.x/feed") is None  # NOSONAR


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
    r.record_imported(empty=True)  # item sem texto: sub-contagem de imported, não 4ª categoria
    r.record_skipped("vid-x", "sem external_id")
    assert r.accounted == 3 and r.reconciled is True  # a soma continua fechando
    assert r.imported == 2 and r.sem_conteudo == 1
    assert "RECONCILIADO" in r.render()
    assert "sem_conteudo=1" in r.render()
    assert "vid-x: sem external_id" in r.render()


def test_recon_report_flags_discrepancy_when_rows_unaccounted() -> None:
    """Origem=5 mas só 2 contabilizadas -> render acusa 3 não contabilizadas."""
    r = ni.ReconReport(corpus="items", source_count=5)
    r.record_imported()
    r.record_imported()
    assert r.reconciled is False
    assert "DISCREPÂNCIA: 3" in r.render()


# ── Camada I/O/CLI: só o caminho determinístico sem Neon vivo (achado do CodeRabbit) ──
# _neon_dsn (falha explícita sem env) e main (fail-fast ANTES de conectar) são
# testáveis sem conexão. O dispatch com env presente NÃO é testável aqui: main abre
# a conexão psycopg (grupo `import`, ausente no job unit do CI) e depois exige Neon
# vivo — validado na execução, não em teste que dependa de banco (docstring do módulo).
_ENV = "NEON_DATABASE_URL"


def test_neon_dsn_raises_config_error_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem NEON_DATABASE_URL, _neon_dsn falha explícito (invariante 8: conexão só por
    env; falha-fechada, nunca cai num default)."""
    monkeypatch.delenv(_ENV, raising=False)
    with pytest.raises(ConfigError):
        ni._neon_dsn()


def test_main_fails_early_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """main valida a presença da conexão ANTES de despachar/conectar — sem env,
    ConfigError (não chega a importar psycopg nem a tocar o Neon)."""
    monkeypatch.delenv(_ENV, raising=False)
    with pytest.raises(ConfigError):
        ni.main(["--corpus", "sources"])


# ── Funções puras novas dos handlers de I/O (sessão 0007 execução) ─────────────
# Extraídas da casca de I/O porque não tocam o Neon/SurrealDB — testáveis com
# valores explícitos, como o resto da camada pura (docstring do módulo).


def test_resolve_news_source_canonical_uses_manual_map_first() -> None:
    """O mapa manual do dono (checkpoint 2026-07-06) vence — inclusive para nomes
    que também teriam um feed_sources homônimo."""
    assert (
        ni.resolve_news_source_canonical("OpenAI", feed_sources_by_name={"OpenAI": "outro"})
        == "https://openai.com/news/rss.xml"
    )
    assert ni.resolve_news_source_canonical("Hacker News") == "https://news.ycombinator.com"


def test_resolve_news_source_canonical_falls_back_to_feed_sources_then_none() -> None:
    """Nome fora do mapa manual cai pro endpoint do feed_sources homônimo; sem
    nenhum dos dois, None (o I/O conta skipped_invalid "source desconhecida")."""
    assert (
        ni.resolve_news_source_canonical(
            "Blog Novo", feed_sources_by_name={"Blog Novo": "https://x/rss"}
        )
        == "https://x/rss"
    )
    assert ni.resolve_news_source_canonical("Blog Novo") is None
    assert ni.resolve_news_source_canonical("Blog Novo", feed_sources_by_name={}) is None


def test_youtube_canonical_helpers_build_stable_urls() -> None:
    """Canonical determinístico de canal/playlist — a chave de gravação da source."""
    assert ni.youtube_channel_canonical("UC123") == "https://www.youtube.com/channel/UC123"
    assert ni.youtube_playlist_canonical("PL456") == "https://www.youtube.com/playlist?list=PL456"


def test_video_source_prefers_channel_over_playlist_then_none() -> None:
    """Prioridade canal > playlist > None (o I/O usa a source sintética
    legacy:youtube quando nenhum resolve, ADR-0012 §VII)."""
    channels = {1: ("https://www.youtube.com/channel/UC1", "Canal 1")}
    playlists = {9: ("https://www.youtube.com/playlist?list=PL9", "Playlist 9")}
    assert ni._video_source({"channel_id": 1, "playlist_id": 9}, channels, playlists) == (
        "https://www.youtube.com/channel/UC1",
        "Canal 1",
    )
    assert ni._video_source({"channel_id": None, "playlist_id": 9}, channels, playlists) == (
        "https://www.youtube.com/playlist?list=PL9",
        "Playlist 9",
    )
    assert ni._video_source({"channel_id": None, "playlist_id": None}, channels, playlists) is None
    # channel_id/playlist_id presentes mas sem correspondência no dict -> None também:
    assert ni._video_source({"channel_id": 404, "playlist_id": None}, channels, playlists) is None


def test_exceeds_cap_flags_only_content_over_1mib() -> None:
    """Cap de sanidade do import (1 MiB, ADR-0012 §VI) — reject+log, nunca trunca."""
    assert ni._exceeds_cap("conteúdo pequeno") is False
    assert ni._exceeds_cap("x" * ni._MAX_CONTENT_BYTES) is False
    assert ni._exceeds_cap("x" * (ni._MAX_CONTENT_BYTES + 1)) is True


def test_iso_normalizes_datetime_passes_through_none_and_str() -> None:
    """datetime do driver -> ISO 8601; None e string já formatada passam direto."""
    import datetime

    dt = datetime.datetime(2021, 5, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    assert ni._iso(dt) == dt.isoformat()
    assert ni._iso(None) is None
    assert ni._iso("2021-05-01T10:00:00Z") == "2021-05-01T10:00:00Z"


def test_neon_dsn_requires_sslmode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neon exige SSL — DSN sem `sslmode=` é erro do operador, pego cedo e sem
    detalhe da DSN na mensagem (invariante 8)."""
    monkeypatch.setenv(_ENV, "postgresql://host/db")  # sem creds: só falta o sslmode
    with pytest.raises(ConfigError):
        ni._neon_dsn()


# ── Achados de segurança/arquitetura (review + advisor, sessão 0007 execução) ──


def test_safe_error_redacts_dsn_from_exception_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """`str(exc)` do psycopg pode ecoar a DSN com senha (invariante 8) — _safe_error
    redige antes de virar `run.error`/log."""
    fake_dsn = "postgresql://u:pw@ep/db?sslmode=require"  # pragma: allowlist secret
    monkeypatch.setenv(_ENV, fake_dsn)
    err = ni._safe_error(RuntimeError(f"connection failed: {fake_dsn}"))
    assert fake_dsn not in err["message"]
    assert "<NEON_DATABASE_URL redigida>" in err["message"]
    assert err["kind"] == "RuntimeError"


def test_safe_error_passes_through_message_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem a DSN no texto do erro, a mensagem passa intacta (nada a redigir)."""
    monkeypatch.setenv(_ENV, "postgresql://host/db?sslmode=require")  # sem creds
    err = ni._safe_error(ValueError("algo não relacionado"))
    assert err == {"kind": "ValueError", "message": "algo não relacionado"}


def test_resolve_source_returns_id_on_hit_and_raises_fatal_on_miss() -> None:
    """_resolve_source é a fronteira do achado do advisor #1: handler de item NUNCA
    upserta source — miss é erro FATAL (força rodar --corpus sources antes)."""
    from surrealdb import RecordID

    sid = RecordID("source", "a")
    index = {"https://x/feed": sid}
    assert ni._resolve_source(index, "https://x/feed") is sid
    with pytest.raises(RuntimeError, match="rode --corpus sources primeiro"):
        ni._resolve_source(index, "https://outra/feed")


def test_source_index_maps_canonical_to_id_via_list_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_source_index usa SÓ `knowledge.list_sources` (invariante 2: nenhum SELECT
    cru de `source` no script) — testável sem SurrealDB via monkeypatch do único
    ponto de leitura (list_sources já é validado contra banco real em
    tests/store/test_knowledge.py)."""
    from surrealdb import RecordID

    from kubo.store.knowledge import SourceInfo

    sources = [
        SourceInfo(id=RecordID("source", "a"), canonical="https://x/feed", kind="rss", title="X"),
        SourceInfo(id=RecordID("source", "b"), canonical="legacy:email", kind="email", title=None),
    ]
    monkeypatch.setattr(ni.knowledge, "list_sources", lambda db: sources)
    index = ni._source_index(object())
    assert index == {"https://x/feed": sources[0].id, "legacy:email": sources[1].id}


def test_item_args_with_extra_carries_extra_into_legacy_metadata() -> None:
    """Como item_args, mas com extra (sender/author) no namespace legacy — usado
    pelos handlers de email/linkedin, que item_args (camada pública) não cobre."""
    args = ni._item_args_with_extra(
        external_id="msg-1",
        content="corpo",
        url=None,
        title="Assunto",
        published_at="2021-01-01T00:00:00Z",
        legacy_id="msg-1",
        extra={"sender": "a@b.com"},
    )
    assert args is not None
    assert args.metadata == {
        "legacy": {
            "published_at": "2021-01-01T00:00:00Z",
            "id": "msg-1",
            "sender": "a@b.com",
        }
    }
    assert (
        ni._item_args_with_extra(
            external_id=None,
            content="x",
            url=None,
            title=None,
            published_at=None,
            legacy_id=None,
            extra={},
        )
        is None
    )
