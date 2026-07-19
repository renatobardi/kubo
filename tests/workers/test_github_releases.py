"""Worker `github-releases` — unit-only (sem banco), no molde de test_feed.py.

respx mocka o httpx (sem rede real); `GithubReleasesWorker().run(ctx)` é chamado direto
com um `RunContext` montado à mão (mesmo padrão de tests/workers/test_feed.py). Sem
`@pytest.mark.integration` — este workspace não tem SurrealDB de pé.

v0.4.0 (#110, ADR-0025 §5): a descoberta dinâmica da watch list (GraphQL `viewer.watching`,
v0.3.0/D57) foi REMOVIDA. O worker coleta releases de UM repo, vindo da config (`repo` +
`since`), que o sweep deriva de um Cadastro `github-repo` — "um run = um Cadastro". A integração
volta a ser `github-readonly` (leitura pública pura, sem o escopo `notifications` que a descoberta
exigia). Estes testes exercitam só o fetch de releases por repo + filtragem por `since` — todo o
caso de descoberta/paginação/GraphQL foi retirado com a feature.
"""

from __future__ import annotations

import gzip
import json as jsonlib
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
import structlog
from pydantic import ValidationError

from kubo.contracts.models import ItemPayload
from kubo.runtime.context import GraphKnowledge, RunContext
from kubo.runtime.integrations import ResolvedIntegration
from kubo.workers.github_releases import GithubReleasesConfig, GithubReleasesWorker

_BASE_URL = "https://api.github.com"
_TOKEN = "ghr-secret-token"  # pragma: allowlist secret
_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_REPO = "acme/widget"


def _release(
    release_id: int,
    *,
    tag_name: str = "v1.0.0",
    name: str | None = "Release One",
    body: str | None = "Body **one**.",
    draft: bool = False,
    prerelease: bool = False,
    html_url: str | None = "https://github.com/acme/widget/releases/tag/v1.0.0",
    published_at: str | None = "2026-06-01T00:00:00Z",
) -> dict[str, object]:
    """Constrói um dict de release da API, no molde da resposta real do GitHub.

    `published_at` default é sempre POSTERIOR a `_SINCE` — testes que não mexem
    explicitamente com filtragem por data continuam qualificando por padrão."""
    return {
        "id": release_id,
        "tag_name": tag_name,
        "name": name,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": html_url,
        "published_at": published_at,
    }


def _config(*, repo: str = _REPO, since: datetime = _SINCE) -> GithubReleasesConfig:
    return GithubReleasesConfig(repo=repo, since=since)


def _ctx(config: GithubReleasesConfig, *, token: str | None = _TOKEN) -> RunContext:
    """Monta o RunContext à mão — sem passar por runner/store."""
    integrations = {}
    if token is not None:
        integrations["github-readonly"] = ResolvedIntegration(
            name="github-readonly",
            kind="http",
            auth_type="bearer",
            secret=token,
            rate_limit=None,
            base_url=_BASE_URL,
        )
    return RunContext(
        config=config,
        integrations=integrations,
        knowledge=GraphKnowledge(None),
        logger=structlog.get_logger(),
    )


def _releases_url(owner: str, repo: str) -> str:
    return f"{_BASE_URL}/repos/{owner}/{repo}/releases"


# ---------------------------------------------------------------------------
# Config shape (#110): `repo` (owner/name) + `since` (tz-aware), ambos obrigatórios.
# ---------------------------------------------------------------------------


def test_config_requires_repo() -> None:
    """`repo` é obrigatório -- config sem ele levanta ValidationError citando o campo."""
    with pytest.raises(ValidationError) as exc_info:
        GithubReleasesConfig(since=_SINCE)  # type: ignore[call-arg]
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("repo",) for err in errors)


def test_config_requires_since() -> None:
    """`since` é obrigatório -- config sem ele levanta ValidationError citando o campo."""
    with pytest.raises(ValidationError) as exc_info:
        GithubReleasesConfig(repo=_REPO)  # type: ignore[call-arg]
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("since",) for err in errors)


def test_config_rejects_naive_since() -> None:
    """`since` sem tzinfo (naive) é rejeitado -- comparação com `published_at` (sempre
    tz-aware na API do GitHub) exige tz-aware dos dois lados."""
    naive_since = datetime(2026, 7, 1)  # noqa: DTZ001 -- naive é o ponto do teste
    with pytest.raises(ValidationError):
        GithubReleasesConfig(repo=_REPO, since=naive_since)


def test_config_accepts_aware_since() -> None:
    """`since` tz-aware constrói normalmente e fica acessível no objeto."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    config = GithubReleasesConfig(repo=_REPO, since=since)
    assert config.since == since
    assert config.repo == _REPO


def test_config_rejects_extra_key() -> None:
    """`extra=\"forbid\"` -- uma chave remanescente (ex.: `repos` do contrato v0.3.0) é
    rejeitada na construção, não ignorada silenciosamente."""
    with pytest.raises(ValidationError):
        GithubReleasesConfig(repo=_REPO, since=_SINCE, repos=["x/y"])  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "bad_repo",
    [
        "no-slash-here",  # sem barra
        "acme/widget?x=1",  # query string (vazaria pra URL montada)
        "acme//widget",  # parte vazia
        "acme/wid get",  # espaço
        "../etc/passwd",  # path traversal
        "acme/..",  # `..` na segunda parte
        "",  # vazio
    ],
)
def test_config_rejects_bad_repo_shape(bad_repo: str) -> None:
    """`repo` fora do shape `owner/name` (whitelist `[A-Za-z0-9._-]` por parte, sem `..`) é
    rejeitado na CONSTRUÇÃO -- o sweep deriva `repo` da canonical do Cadastro, shape inválido
    é bug de fiação barrado na borda barata, nunca em runtime no meio do fetch."""
    with pytest.raises(ValidationError):
        GithubReleasesConfig(repo=bad_repo, since=_SINCE)


# ---------------------------------------------------------------------------
# Manifest (#110): versão 0.4.0, integração `github-readonly` (leitura pública pura).
# ---------------------------------------------------------------------------


def test_manifest_version_is_0_4_0() -> None:
    assert GithubReleasesWorker.manifest.version == "0.4.0"


def test_manifest_integrations_lists_github_readonly_only() -> None:
    """Sem descoberta, o worker só lê `/repos/.../releases` (público) -- volta ao
    `github-readonly`/`GITHUB_TOKEN_READONLY`, sem o escopo `notifications` do `github-watch`
    que a v0.2.0/D54 exigia para `/user/subscriptions`."""
    assert GithubReleasesWorker.manifest.integrations == ["github-readonly"]


# ---------------------------------------------------------------------------
# Caminho feliz: coleta de UM repo.
# ---------------------------------------------------------------------------


@respx.mock
def test_happy_path_single_repo_multiple_releases() -> None:
    """1 repo, 2 releases qualificados -> 2 ItemPayload, source/stats corretos."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1), _release(2, tag_name="v1.1.0")])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 2
    ids = {p.external_id for p in items}
    assert ids == {"1", "2"}
    assert items[0].source.kind == "github-repo"
    assert items[0].source.canonical == "https://github.com/acme/widget"
    assert items[0].source.title == "acme/widget releases"
    assert items[0].content == "Body **one**."
    assert items[0].url == "https://github.com/acme/widget/releases/tag/v1.0.0"
    assert items[0].metadata == {
        "tag_name": "v1.0.0",
        "repo": "acme/widget",
        "published_at": "2026-06-01T00:00:00Z",
    }
    stats = result.stats.model_dump()
    assert stats["releases_seen"] == 2
    assert stats["items"] == 2
    assert stats["rate_limited"] == 0
    # header de auth do token resolvido, nunca na URL.
    assert respx.calls.last.request.headers["authorization"] == f"Bearer {_TOKEN}"
    # Accept-Encoding: identity é OBRIGATÓRIO (achado do smoke físico, sessão 0021 passo 4):
    # sem ele, o GitHub responde gzip por padrão e `_stream_json_list` usa `iter_raw()` (bytes
    # de FIO, NUNCA decodificados) -- json.loads no corpo ainda comprimido falha com "resposta
    # da API não é JSON válido" em TODO request real, mascarado pelos mocks do respx (que nunca
    # comprimem de verdade). Mesma disciplina de feed.py._fetch.
    assert respx.calls.last.request.headers["accept-encoding"] == "identity"


@respx.mock
def test_source_kind_is_github_repo_matching_cadastro() -> None:
    """A `SourcePayload` emitida tem `kind=\"github-repo\"` (#110): casa a chave natural
    (kind, canonical) do Cadastro que dirigiu a coleta, para `upsert_source` (lookup-first)
    reusar o MESMO record em vez de criar um source paralelo `github-releases`."""
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config(repo="acme/gizmo")))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].source.kind == "github-repo"
    assert items[0].source.canonical == "https://github.com/acme/gizmo"


@respx.mock
def test_empty_release_list_is_clean_run() -> None:
    """Repo sem releases -> run limpo (sem erro, sem itens), não é config error (o repo existe
    como Cadastro; a lista vazia é ausência de novidade, não misconfiguração)."""
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(200, json=[]))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    assert result.payloads == []
    assert result.stats.model_dump()["items"] == 0


# ---------------------------------------------------------------------------
# Filtragem por `since`/`published_at` (C1/C5 -- filtrar e gravar são DUAS obrigações
# separadas, testadas explicitamente em separado).
# ---------------------------------------------------------------------------


@respx.mock
def test_release_before_since_is_excluded() -> None:
    """Release com `published_at` ANTES de `since` não vira item."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at="2025-12-31T23:59:59Z")])
    )

    result = GithubReleasesWorker().run(_ctx(_config(since=datetime(2026, 1, 1, tzinfo=UTC))))

    assert result.error is None
    assert result.payloads == []


@respx.mock
def test_release_after_since_is_included() -> None:
    """Release com `published_at` DEPOIS de `since` vira item."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at="2026-01-02T00:00:00Z")])
    )

    result = GithubReleasesWorker().run(_ctx(_config(since=datetime(2026, 1, 1, tzinfo=UTC))))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "1"


@respx.mock
def test_release_at_since_boundary_is_included() -> None:
    """`published_at` IGUAL a `since` é incluído -- boundary inclusivo (`>=`)."""
    since = datetime(2026, 1, 1, tzinfo=UTC)
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at="2026-01-01T00:00:00Z")])
    )

    result = GithubReleasesWorker().run(_ctx(_config(since=since)))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1


@respx.mock
def test_qualifying_release_metadata_includes_published_at() -> None:
    """`ItemPayload.metadata[\"published_at\"]` carrega o `published_at` do release -- filtrar
    (teste acima) e GRAVAR (este teste) são obrigações separadas."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at="2026-03-15T10:00:00Z")])
    )

    result = GithubReleasesWorker().run(_ctx(_config(since=datetime(2026, 1, 1, tzinfo=UTC))))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].metadata is not None
    assert items[0].metadata["published_at"] == "2026-03-15T10:00:00Z"


# ---------------------------------------------------------------------------
# `published_at` ausente numa release qualificada -> skip + contado (C5).
# ---------------------------------------------------------------------------


@respx.mock
def test_release_with_published_at_none_is_skipped_and_counted() -> None:
    """`published_at == None` numa release não-draft/não-prerelease com `id` -> NÃO vira
    item, conta em `stats[\"skipped_no_date\"]`."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at=None)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    assert result.payloads == []
    assert result.stats.model_dump()["skipped_no_date"] == 1


@respx.mock
def test_release_missing_published_at_key_is_skipped_and_counted() -> None:
    """`published_at` AUSENTE do dict inteiro (não só `None`) -> mesmo tratamento."""
    release = {
        "id": 1,
        "tag_name": "v1.0.0",
        "name": "Release One",
        "body": "Body.",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/acme/widget/releases/tag/v1.0.0",
    }
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[release])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    assert result.payloads == []
    assert result.stats.model_dump()["skipped_no_date"] == 1


# ---------------------------------------------------------------------------
# Filtragem draft/prerelease + dedupe stability.
# ---------------------------------------------------------------------------


@respx.mock
def test_draft_and_prerelease_are_filtered() -> None:
    """draft=true e prerelease=true são PULADOS (não viram item, não é erro)."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(
            200,
            json=[_release(1, draft=True), _release(2, prerelease=True), _release(3)],
        )
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "3"
    stats = result.stats.model_dump()
    assert stats["releases_seen"] == 3
    assert stats["items"] == 1


@respx.mock
def test_dedupe_key_stability_across_edits() -> None:
    """external_id (release id) é ESTÁVEL mesmo quando o release é editado depois de
    publicado (nome/corpo mudam, id não) — upsert_item trata como o MESMO item."""
    route = respx.get(_releases_url("acme", "widget"))

    route.mock(return_value=httpx.Response(200, json=[_release(42, name="v1")]))
    result_before = GithubReleasesWorker().run(_ctx(_config()))

    route.mock(return_value=httpx.Response(200, json=[_release(42, name="v1 (edited)")]))
    result_after = GithubReleasesWorker().run(_ctx(_config()))

    payload_before = result_before.payloads[0]
    payload_after = result_after.payloads[0]
    assert isinstance(payload_before, ItemPayload)  # narrowing (padrão do FeedWorker)
    assert isinstance(payload_after, ItemPayload)
    assert payload_before.external_id == payload_after.external_id == "42"


# ---------------------------------------------------------------------------
# Classificação de erro de fetch (D55): rate_limit vs http, por status/headers.
# ---------------------------------------------------------------------------


@respx.mock
def test_rate_limit_429_is_rate_limit_kind() -> None:
    """429 -> error kind rate_limit, detail com repo+status, stats[rate_limited]=1."""
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(429))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.detail == {"repo": "acme/widget", "status": 429}
    assert result.payloads == []
    assert result.stats.model_dump()["rate_limited"] == 1


@respx.mock
def test_forbidden_without_rate_limit_headers_is_http_kind() -> None:
    """403 SEM header de rate limit é permission-denied puro (ex.: PAT sem escopo) —
    NUNCA rate_limit, senão o operador espera uma janela que nunca vai abrir (D55)."""
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(403))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.payloads == []


@respx.mock
def test_forbidden_with_ratelimit_remaining_zero_is_rate_limit_kind() -> None:
    """403 COM `x-ratelimit-remaining: 0` é o sinal primário de rate limit do GitHub ->
    kind=rate_limit (D55)."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.payloads == []


@respx.mock
def test_forbidden_with_retry_after_is_rate_limit_kind() -> None:
    """403 COM `retry-after` é o sinal de rate limit secundário (abuse detection) do GitHub
    -> kind=rate_limit, mesmo sem `x-ratelimit-remaining` (D55)."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(403, headers={"retry-after": "60"})
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.payloads == []


@respx.mock
def test_other_http_error_is_http_kind() -> None:
    """5xx -> kind=http (não rate_limit), com repo+status no detail."""
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(503))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.error.detail == {"repo": "acme/widget", "status": 503}
    assert result.payloads == []
    assert result.stats.model_dump()["rate_limited"] == 0


# ---------------------------------------------------------------------------
# Releases malformados: skip sem crash.
# ---------------------------------------------------------------------------


@respx.mock
def test_malformed_release_missing_id_is_skipped_without_crashing() -> None:
    """Release sem `id` não tem chave de dedupe estável -> skip, não crash."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[{"draft": False, "prerelease": False}, _release(9)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "9"
    stats = result.stats.model_dump()
    assert stats["releases_seen"] == 2
    assert stats["items"] == 1


@respx.mock
def test_malformed_release_missing_optional_fields_falls_back_cleanly() -> None:
    """Sem `name`/`body`/`html_url` -> título cai para tag_name, content vazio, url None."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 99,
                    "tag_name": "v9.9.9",
                    "name": None,
                    "body": None,
                    "draft": False,
                    "prerelease": False,
                    "html_url": None,
                    "published_at": "2026-06-01T00:00:00Z",
                }
            ],
        )
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].title == "v9.9.9"
    assert items[0].content == ""
    assert items[0].url is None
    assert items[0].metadata == {
        "tag_name": "v9.9.9",
        "repo": "acme/widget",
        "published_at": "2026-06-01T00:00:00Z",
    }


@respx.mock
def test_tag_name_in_metadata_is_sanitized_like_other_fields() -> None:
    """Achado do security-reviewer (ALTO): `tag_name` passa pela MESMA limpeza que
    content/title antes de virar `metadata` — um caractere de controle solto não pode
    sobreviver até a persistência (CBOR estrito) e abortar o batch inteiro."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, tag_name="v1.0.0\x00\x01evil")])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].metadata is not None
    assert items[0].metadata["tag_name"] == "v1.0.0evil"  # caracteres de controle removidos


# ---------------------------------------------------------------------------
# Defesas de fronteira de bytes/encoding (achados de security-reviewer + smoke físico).
# ---------------------------------------------------------------------------


@respx.mock
def test_gzip_encoded_response_fails_safely_not_silently() -> None:
    """Achado do smoke físico (sessão 0021 passo 4): `_stream_json_list` lê via `iter_raw()`
    (bytes de FIO, NUNCA decodificados -- mesma disciplina de `feed.py._fetch` contra
    decompression bomb), então uma resposta gzip nunca vira JSON válido NESTE worker, ponto.
    A defesa real é `Accept-Encoding: identity` no request (o GitHub honra e não comprime,
    confirmado ao vivo no smoke) -- este teste prova que SE alguma resposta ainda chegar gzip
    (proxy/CDN que ignore o header), a falha é ESTRUTURADA (`kind='http'`, mensagem clara),
    nunca um crash nem dado perdido silenciosamente."""
    releases_json = jsonlib.dumps([_release(1)]).encode()
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(
            200, content=gzip.compress(releases_json), headers={"content-encoding": "gzip"}
        )
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert "JSON válido" in result.error.message
    assert result.payloads == []


@respx.mock
def test_response_exceeding_byte_cap_is_rejected_without_buffering() -> None:
    """Achado do security-reviewer (MÉDIO): resposta declarando Content-Length acima do
    teto (mesmo padrão de feed.py._fetch) é rejeitada ANTES de bufferizar o corpo."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, headers={"content-length": "99999999999"}, content=b"[]")
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert "teto de bytes" in result.error.message
    assert result.payloads == []


@respx.mock
def test_streaming_byte_cap_is_rejected_without_content_length_header() -> None:
    """O teto de bytes com `Content-Length` DECLARADO acima cobria o pré-check; este cobre o
    laço `iter_raw` + acumulador corrido (a defesa real contra chunked transfer sem
    `Content-Length` honesto, justo o caminho de ataque). Corpo via GENERATOR: httpx não
    computa `Content-Length` pra conteúdo streamado, então o pré-check nem dispara -- só o
    acumulador do laço pode cortar."""

    def _oversized_chunks() -> Iterator[bytes]:
        chunk = b"a" * (1024 * 1024)
        for _ in range(11):  # 11 MiB > _MAX_BYTES (10 MiB)
            yield chunk

    route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, content=_oversized_chunks())
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert "content-length" not in route.calls.last.response.headers
    assert result.error is not None
    assert result.error.kind == "http"
    assert "teto de bytes" in result.error.message
    assert result.payloads == []


# ---------------------------------------------------------------------------
# Integração ausente -> ConfigError.
# ---------------------------------------------------------------------------


def test_missing_integration_secret_raises_config_error() -> None:
    """Integração ausente (sem token resolvido) -> ConfigError, nunca skip silencioso.

    Exercita `github-readonly` (#110: worker voltou ao PAT de leitura pura)."""
    from kubo.errors import ConfigError

    with pytest.raises(ConfigError):
        GithubReleasesWorker().run(_ctx(_config(), token=None))
