"""Worker `feed` (RSS/Atom) — M5.1. Cobre o contrato exato acordado com o RED.

Unit (default): respx mocka o httpx, `FeedWorker().run(ctx)` chamado direto com
um `RunContext` montado à mão — sem tocar SurrealDB (tests/runtime/test_runner.py
é o modelo). LLM não entra neste worker (não há chamada a provider aqui).

Integração (`@pytest.mark.integration`): e2e via `run_worker` contra um
`http.server.ThreadingHTTPServer` local em loopback — nunca a internet real — e
o grafo real via SurrealDB, no molde de tests/runtime/test_runner.py.

As 4 fixtures hostis (`BROKEN_WITH_ENTRIES`, `BROKEN_NO_ENTRIES`, `GIANT_ENTRY`,
`MALFORMED_DATE`) foram checadas contra `feedparser` diretamente antes de entrar
aqui (bozo/entries reais, não hipóteses) — ver sessão de RED.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx
import pytest
import respx
import structlog
from pydantic import ValidationError

from kubo.contracts.models import ItemPayload
from kubo.runtime.context import EmptyKnowledge, RunContext
from kubo.runtime.integrations import ResolvedIntegration
from kubo.workers.feed import FeedConfig, FeedWorker

_FEED_URL = "https://example.com/feed"
_BYTE_CAP = 10 * 1024 * 1024  # 10 MiB (contrato do RED)
_CONTENT_CAP = 65536

VALID_TWO = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed Title</title>
<link>https://example.com/</link>
<description>desc</description>
<item>
<title>Entry One</title>
<link>https://example.com/1</link>
<guid>guid-1</guid>
<description>Conteudo do item um.</description>
</item>
<item>
<title>Entry Two</title>
<link>https://example.com/2</link>
<guid>guid-2</guid>
<description>Conteudo do item dois.</description>
</item>
</channel>
</rss>
"""

# bozo=1 (ampersand cru, XML malformado) MAS feedparser recupera 1 entry pelo
# parser tolerante — checado diretamente contra feedparser antes de fixar aqui.
BROKEN_WITH_ENTRIES = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed Title</title>
<item>
<title>Broken Entry</title>
<guid>guid-broken-1</guid>
<description>Foo & Bar unescaped</description>
</item>
</channel>
</rss>
"""

# Lixo puro: bozo=1 e ZERO entries.
BROKEN_NO_ENTRIES = b"this is not xml at all, just garbage \x00\x01\x02 blah blah"

# Bem-formado, canal sem nenhum <item> — zero entries SEM bozo.
EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Empty Feed</title>
<link>https://example.com/</link>
<description>desc</description>
</channel>
</rss>
"""

# pubDate ilegível: feedparser bem-formado (bozo=0), mas published_parsed é None.
MALFORMED_DATE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed</title>
<item>
<title>Bad Date Entry</title>
<guid>guid-baddate</guid>
<pubDate>not-a-real-date</pubDate>
<description>content</description>
</item>
</channel>
</rss>
"""

# Entry gigante: content muito acima do teto de 65536 chars.
GIANT_ENTRY = (
    b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed</title>
<item>
<title>Giant Entry</title>
<guid>guid-giant</guid>
<description>"""
    + (b"a" * 200_000)
    + b"""</description>
</item>
</channel>
</rss>
"""
)

# 3 entries cobrindo a cadeia de fallback de external_id:
#   1) sem guid, COM link -> external_id == link
#   2) sem guid, sem link, COM title+pubDate -> sha256(title \x1f published_raw)
#   3) nada identificável (sem id/link/title/date/content) -> SKIP
FALLBACK_MIX = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed</title>
<item>
<link>https://example.com/a</link>
<description>content A</description>
</item>
<item>
<title>Title B</title>
<pubDate>Mon, 01 Jan 2001 00:00:00 GMT</pubDate>
<description>content B</description>
</item>
<item>
</item>
</channel>
</rss>
"""

# guid presente (external_id vem do guid) mas o link é esquema não-http.
JS_SCHEME_LINK = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Feed</title>
<item>
<title>JS Link Entry</title>
<link>javascript:alert(1)</link>
<guid>guid-js</guid>
<description>content</description>
</item>
</channel>
</rss>
"""


def _ctx(config: FeedConfig) -> RunContext:
    """Monta o RunContext à mão, no molde exigido pelo contrato (sem passar por runner)."""
    return RunContext(
        config=config,
        integrations={
            "rss": ResolvedIntegration(
                name="rss",
                kind="http",
                auth_type="none",
                secret=None,
                rate_limit=None,
                base_url=None,
            )
        },
        knowledge=EmptyKnowledge(),
        logger=structlog.get_logger(),
    )


def _expected_hash_id(title: str, published_raw: str) -> str:
    """Replica a regra de fallback (título+data) para a asserção — não a implementação."""
    return hashlib.sha256("\x1f".join([title, published_raw]).encode("utf-8")).hexdigest()


@respx.mock
def test_happy_path_two_entries_with_guid() -> None:
    """Feed válido com 2 entries com guid estável -> 2 ItemPayload, stats corretas."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=VALID_TWO))
    config = FeedConfig(feed_url=_FEED_URL, title="Feed X", tags=["tech", "news"])

    result = FeedWorker().run(_ctx(config))

    assert result.error is None
    assert len(result.payloads) == 2
    ids = {p.external_id for p in result.payloads if isinstance(p, ItemPayload)}
    assert ids == {"guid-1", "guid-2"}
    for payload in result.payloads:
        assert isinstance(payload, ItemPayload)
        assert payload.source.kind == "rss"
        assert payload.source.canonical == _FEED_URL
        assert payload.source.title == "Feed X"
        assert payload.content  # populado
        assert payload.metadata == {"tags": ["tech", "news"]}
    stats = result.stats.model_dump()
    assert stats["entries_seen"] == 2
    assert stats["items"] == 2
    assert stats["bozo"] == 0
    assert stats["skipped_no_identity"] == 0
    # Accept-Encoding: identity — sem descompressão no fio, fecha a decompression bomb.
    assert respx.calls.last.request.headers["accept-encoding"] == "identity"


@respx.mock
def test_external_id_fallback_chain_and_skip_when_nothing_identifiable() -> None:
    """link -> hash(title+published_raw) -> skip, na ordem certa; hash é determinístico."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=FALLBACK_MIX))
    config = FeedConfig(feed_url=_FEED_URL)

    result_a = FeedWorker().run(_ctx(config))
    result_b = FeedWorker().run(_ctx(config))  # segunda execução: mesmo hash (determinístico)

    for result in (result_a, result_b):
        assert result.error is None
        items = [p for p in result.payloads if isinstance(p, ItemPayload)]
        assert len(items) == 2  # a 3a entry (nada identificável) foi pulada
        by_id = {p.external_id: p for p in items}
        assert "https://example.com/a" in by_id  # fallback por link
        expected_hash = _expected_hash_id("Title B", "Mon, 01 Jan 2001 00:00:00 GMT")
        assert expected_hash in by_id  # fallback por hash(title+published_raw)
        assert len(expected_hash) == 64
        stats = result.stats.model_dump()
        assert stats["entries_seen"] == 3
        assert stats["items"] == 2
        assert stats["skipped_no_identity"] == 1


@respx.mock
def test_bozo_with_recoverable_entries_proceeds_and_flags_stats() -> None:
    """bozo=1 mas feedparser recupera >=1 entry -> segue normal, stats bozo==1."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=BROKEN_WITH_ENTRIES))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.error is None
    assert len(result.payloads) >= 1
    stats = result.stats.model_dump()
    assert stats["bozo"] == 1
    assert stats["items"] >= 1


@respx.mock
def test_bozo_with_zero_entries_is_parse_error() -> None:
    """bozo=1 e ZERO entries -> RunResult de erro (kind=parse), sem payloads."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=BROKEN_NO_ENTRIES))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.payloads == []
    assert result.error is not None
    assert result.error.kind == "parse"


@respx.mock
def test_well_formed_zero_entries_is_not_an_error() -> None:
    """Feed bem-formado mas vazio (sem bozo) -> payloads vazios, SEM error."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=EMPTY_FEED))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.payloads == []
    assert result.error is None


@respx.mock
def test_malformed_pubdate_is_collected_without_error() -> None:
    """published_parsed None (data ilegível) não é erro — entry ainda é coletada."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=MALFORMED_DATE))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "guid-baddate"


@respx.mock
def test_giant_entry_content_is_capped() -> None:
    """Content de ~200k chars volta capado em <= 65536."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=GIANT_ENTRY))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert len(items[0].content) <= _CONTENT_CAP


@respx.mock
def test_disallowed_link_scheme_is_stored_as_none() -> None:
    """Link com esquema não-http/https (javascript:) -> ItemPayload.url é None."""
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=JS_SCHEME_LINK))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "guid-js"  # guid disponível, não depende do link
    assert items[0].url is None


@respx.mock
def test_fetch_failure_is_http_error_without_leaking_body() -> None:
    """HTTP 503 -> RunResult de erro (kind=http), sem payloads e sem vazar o corpo da resposta."""
    respx.get(_FEED_URL).mock(
        return_value=httpx.Response(503, content=b"SECRET_INTERNAL_BODY_LEAK_MARKER")
    )
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.payloads == []
    assert result.error is not None
    assert result.error.kind == "http"
    assert "SECRET_INTERNAL_BODY_LEAK_MARKER" not in result.error.message


def test_disallowed_config_scheme_rejected_at_construction() -> None:
    """feed_url com esquema != http/https é rejeitado na validação da CONFIG, não no run."""
    with pytest.raises(ValidationError):
        FeedConfig(feed_url="gopher://x/feed")  # gopher: vetor SSRF clássico, fora do allowlist


@respx.mock
def test_byte_cap_exceeded_is_http_error() -> None:
    """Resposta acima do teto de 10 MiB -> erro http, sem nenhum item emitido."""
    oversized = b"a" * (_BYTE_CAP + 1024)
    respx.get(_FEED_URL).mock(return_value=httpx.Response(200, content=oversized))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.payloads == []
    assert result.error is not None
    assert result.error.kind == "http"


@respx.mock
def test_redirect_to_internal_ip_is_refused() -> None:
    """Servidor de feed que responde 302 apontando para um IP interno (metadata da
    OCI 169.254.169.254) é RECUSADO no hop de redirect — SSRF de exfiltração fechado.
    O IP-literal torna o teste determinístico (o guard valida sem DNS)."""
    # O IP de metadata da OCI é literal DE PROPÓSITO: é o destino que o teste prova ser recusado.
    internal = "http://169.254.169.254/opc/v2/instance/"  # NOSONAR
    respx.get(_FEED_URL).mock(return_value=httpx.Response(302, headers={"Location": internal}))
    # O destino interno é mockado com um feed VÁLIDO de propósito: sem o guard, o worker
    # SEGUIRIA o redirect, parsearia e coletaria a resposta interna (payloads != []). Com o
    # guard, o hop é recusado. Assim o teste falha se o event hook for removido — não passa
    # só porque o respx bloquearia uma URL não-mockada.
    respx.get(internal).mock(return_value=httpx.Response(200, content=VALID_TWO))
    config = FeedConfig(feed_url=_FEED_URL)

    result = FeedWorker().run(_ctx(config))

    assert result.payloads == []
    assert result.error is not None
    assert result.error.kind == "http"


class _ValidTwoHandler(http.server.BaseHTTPRequestHandler):
    """Handler mínimo que serve `VALID_TWO` em `/feed` — servidor local, nunca internet real."""

    def do_GET(self) -> None:  # noqa: N802 — nome exigido pela API de http.server
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(VALID_TWO)))
        self.end_headers()
        self.wfile.write(VALID_TWO)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — assinatura da stdlib
        """Silencia o log de acesso padrão — ruído irrelevante para o teste."""


@pytest.fixture
def _feed_server() -> Iterator[str]:
    """Servidor HTTP local (porta efêmera, loopback) servindo o feed válido."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ValidTwoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/feed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
def test_e2e_feed_worker_persists_graph_and_second_run_is_idempotent(
    _feed_server: str,
) -> None:
    """Ponta a ponta com HTTP real (loopback): 2 items no grafo, arestas from_source
    e collected_by corretas. Reexecução com o MESMO feed é no-op (external_id
    estável) — critério de aceite de re-coleta idempotente com feed real."""
    from kubo.runtime import runner
    from kubo.store import client, migrations

    db_name = "test_feed_worker_e2e"
    cfg = replace(client.config(), database=db_name)
    with client.connect(cfg) as db:
        db.query(f"REMOVE DATABASE IF EXISTS {db_name};")
        db.use(cfg.namespace, cfg.database)
        migrations.apply_migrations(db)
        try:
            run_id_1 = runner.run_worker(db, FeedWorker(), config={"feed_url": _feed_server})

            row = db.query("SELECT status FROM $r;", {"r": run_id_1})[0]
            assert row["status"] == "ok"
            items = db.query(
                "SELECT id, ->from_source->source AS s, ->collected_by->run AS r FROM item;"
            )
            assert len(items) == 2
            for item in items:
                assert (
                    item["s"] == [db.query("SELECT id FROM source;")[0]["id"]]
                    or len(item["s"]) == 1
                )
                assert item["r"] == [run_id_1]
            sources = db.query("SELECT count() FROM source GROUP ALL;")
            assert int(sources[0]["count"]) == 1

            run_id_2 = runner.run_worker(db, FeedWorker(), config={"feed_url": _feed_server})

            row_2 = db.query("SELECT status FROM $r;", {"r": run_id_2})[0]
            assert row_2["status"] == "ok"
            items_after = db.query("SELECT count() FROM item GROUP ALL;")
            assert int(items_after[0]["count"]) == 2  # no-op: mesmo external_id, sem duplicar
            sources_after = db.query("SELECT count() FROM source GROUP ALL;")
            assert int(sources_after[0]["count"]) == 1
        finally:
            db.query(f"REMOVE DATABASE IF EXISTS {db_name};")
