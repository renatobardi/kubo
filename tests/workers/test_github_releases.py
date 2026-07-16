"""Worker `github-releases` — unit-only (sem banco), no molde de test_feed.py.

respx mocka o httpx (sem rede real); `GithubReleasesWorker().run(ctx)` é chamado direto
com um `RunContext` montado à mão (mesmo padrão de tests/workers/test_feed.py). Sem
`@pytest.mark.integration` — este workspace não tem SurrealDB de pé.

v0.2.0 (D51/D52/D54, sessão 0021): a config estática `repos: list[str]` foi REMOVIDA — o
worker agora lê a watch list do operador via `GET /user/subscriptions` (paginado) e coleta
releases de cada repo descoberto, filtrando por `since` (`published_at`). A integração
dedicada é `github-watch` (não mais `github-releases`). `/user/subscriptions` é mockado
com o mesmo respx usado para `/repos/{owner}/{repo}/releases`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
import structlog
from pydantic import ValidationError

from kubo.contracts.models import ItemPayload, RunResult
from kubo.runtime.context import GraphKnowledge, RunContext
from kubo.runtime.integrations import ResolvedIntegration
from kubo.workers.github_releases import GithubReleasesConfig, GithubReleasesWorker

_BASE_URL = "https://api.github.com"
_TOKEN = "ghr-secret-token"  # pragma: allowlist secret
_SUBSCRIPTIONS_URL = f"{_BASE_URL}/user/subscriptions"
_SINCE = datetime(2026, 1, 1, tzinfo=UTC)


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


def _subscription(full_name: str, *, sub_id: int = 1, private: bool = False) -> dict[str, object]:
    """Constrói um item de `/user/subscriptions`, no molde real do GitHub."""
    return {"id": sub_id, "full_name": full_name, "private": private}


def _mock_watch_list(*repos: str) -> None:
    """Mocka `/user/subscriptions` com UMA página (sem `Link` -> sem próxima página)."""
    respx.get(_SUBSCRIPTIONS_URL).mock(
        return_value=httpx.Response(
            200, json=[_subscription(r, sub_id=i) for i, r in enumerate(repos, start=1)]
        )
    )


def _config(*, since: datetime = _SINCE) -> GithubReleasesConfig:
    return GithubReleasesConfig(since=since)


def _ctx(config: GithubReleasesConfig, *, token: str | None = _TOKEN) -> RunContext:
    """Monta o RunContext à mão — sem passar por runner/store."""
    integrations = {}
    if token is not None:
        integrations["github-watch"] = ResolvedIntegration(
            name="github-watch",
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
# Config shape (D52): `repos` sumiu, `since` (tz-aware) é obrigatório.
# ---------------------------------------------------------------------------


def test_config_requires_since() -> None:
    """`since` é obrigatório -- config sem ele levanta ValidationError citando o campo."""
    with pytest.raises(ValidationError) as exc_info:
        GithubReleasesConfig()  # type: ignore[call-arg]
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("since",) for err in errors)


def test_config_rejects_naive_since() -> None:
    """`since` sem tzinfo (naive) é rejeitado -- comparação com `published_at` (sempre
    tz-aware na API do GitHub) exige tz-aware dos dois lados."""
    with pytest.raises(ValidationError):
        GithubReleasesConfig(since=datetime(2026, 7, 1))  # noqa: DTZ001 -- naive é o ponto do teste


def test_config_accepts_aware_since() -> None:
    """`since` tz-aware constrói normalmente e fica acessível no objeto."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    config = GithubReleasesConfig(since=since)
    assert config.since == since


def test_config_rejects_leftover_repos_key() -> None:
    """`extra=\"forbid\"` permanece -- uma chave `repos` remanescente (do contrato v1) é
    rejeitada na construção, não ignorada silenciosamente."""
    with pytest.raises(ValidationError):
        since = datetime(2026, 7, 1, tzinfo=UTC)
        GithubReleasesConfig(since=since, repos=["acme/widget"])  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Manifest (D54): versão 0.2.0, integração dedicada `github-watch`.
# ---------------------------------------------------------------------------


def test_manifest_version_is_0_2_0() -> None:
    assert GithubReleasesWorker.manifest.version == "0.2.0"


def test_manifest_integrations_lists_github_watch_only() -> None:
    """UMA integração dedicada (`github-watch`) cobre tanto `/user/subscriptions` quanto
    `/repos/.../releases` -- D54, PAT próprio, não o `github-releases` antigo."""
    assert GithubReleasesWorker.manifest.integrations == ["github-watch"]


# ---------------------------------------------------------------------------
# Paginação de /user/subscriptions (C4 -- achado do advisor, bug mais provável da sessão).
# ---------------------------------------------------------------------------


@respx.mock
def test_subscriptions_pagination_walks_every_page() -> None:
    """3 páginas (2+2+1 repos) via `Link: rel=\"next\"` -> releases de TODOS os repos são
    buscadas, não só a primeira página."""
    page1 = httpx.Response(
        200,
        json=[_subscription("acme/widget", sub_id=1), _subscription("acme/gizmo", sub_id=2)],
        headers={"Link": f'<{_SUBSCRIPTIONS_URL}?per_page=100&page=2>; rel="next"'},
    )
    page2 = httpx.Response(
        200,
        json=[_subscription("acme/foo", sub_id=3), _subscription("acme/bar", sub_id=4)],
        headers={"Link": f'<{_SUBSCRIPTIONS_URL}?per_page=100&page=3>; rel="next"'},
    )
    page3 = httpx.Response(200, json=[_subscription("acme/baz", sub_id=5)])
    respx.get(_SUBSCRIPTIONS_URL).mock(side_effect=[page1, page2, page3])

    repos = ["acme/widget", "acme/gizmo", "acme/foo", "acme/bar", "acme/baz"]
    for idx, owner_repo in enumerate(repos, start=1):
        owner, _, name = owner_repo.partition("/")
        respx.get(_releases_url(owner, name)).mock(
            return_value=httpx.Response(200, json=[_release(idx)])
        )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 5
    repos_covered = {p.metadata["repo"] for p in items if p.metadata}
    assert repos_covered == set(repos)
    stats = result.stats.model_dump()
    assert stats["repos_seen"] == 5


@respx.mock
def test_subscriptions_pagination_hard_cap_stops_after_ten_pages() -> None:
    """`Link: rel=\"next\"` SEMPRE presente (paginação patológica/infinita) -> o worker
    para depois de um teto fixo de páginas (10), sem travar, e ainda devolve `RunResult`."""
    endless_page = httpx.Response(
        200,
        json=[_subscription("acme/widget", sub_id=1)],
        headers={"Link": f'<{_SUBSCRIPTIONS_URL}?per_page=100&page=2>; rel="next"'},
    )
    route = respx.get(_SUBSCRIPTIONS_URL).mock(return_value=endless_page)
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert isinstance(result, RunResult)
    assert route.call_count <= 10


# ---------------------------------------------------------------------------
# Watch list vazia é ERRO, nunca run limpo (C3 -- armadilha silenciosa do D51: PAT sem
# escopo `notifications` faz /user/subscriptions devolver 200 [] SEM erro do GitHub).
# ---------------------------------------------------------------------------


@respx.mock
def test_empty_watch_list_is_config_error() -> None:
    """`/user/subscriptions` devolve `200 []` -> `ErrorInfo(kind=\"config\")`, NUNCA um run
    limpo e vazio -- e nenhum endpoint de releases é sequer chamado (nada pra buscar)."""
    respx.get(_SUBSCRIPTIONS_URL).mock(return_value=httpx.Response(200, json=[]))
    releases_route = respx.route(method="GET", url__regex=r".*/repos/.+/releases").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "config"
    assert result.payloads == []
    assert releases_route.call_count == 0


# ---------------------------------------------------------------------------
# Filtragem por `since`/`published_at` (C1/C5 -- filtrar e gravar são DUAS obrigações
# separadas, testadas explicitamente em separado).
# ---------------------------------------------------------------------------


@respx.mock
def test_release_before_since_is_excluded() -> None:
    """Release com `published_at` ANTES de `since` não vira item."""
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at="2025-12-31T23:59:59Z")])
    )

    result = GithubReleasesWorker().run(_ctx(_config(since=datetime(2026, 1, 1, tzinfo=UTC))))

    assert result.error is None
    assert result.payloads == []


@respx.mock
def test_release_after_since_is_included() -> None:
    """Release com `published_at` DEPOIS de `since` vira item."""
    _mock_watch_list("acme/widget")
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
    _mock_watch_list("acme/widget")
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
    _mock_watch_list("acme/widget")
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
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, published_at=None)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    assert result.payloads == []
    stats = result.stats.model_dump()
    assert stats["skipped_no_date"] == 1


@respx.mock
def test_release_missing_published_at_key_is_skipped_and_counted() -> None:
    """`published_at` AUSENTE do dict inteiro (não só `None`) -> mesmo tratamento."""
    _mock_watch_list("acme/widget")
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
    stats = result.stats.model_dump()
    assert stats["skipped_no_date"] == 1


# ---------------------------------------------------------------------------
# `repos_seen` reflete a watch list dinâmica, não uma lista estática de config.
# ---------------------------------------------------------------------------


@respx.mock
def test_repos_seen_reflects_watch_list_size_across_pages() -> None:
    """2 páginas, 3 repos totais -> `stats[\"repos_seen\"] == 3`."""
    page1 = httpx.Response(
        200,
        json=[_subscription("acme/widget", sub_id=1), _subscription("acme/gizmo", sub_id=2)],
        headers={"Link": f'<{_SUBSCRIPTIONS_URL}?per_page=100&page=2>; rel="next"'},
    )
    page2 = httpx.Response(200, json=[_subscription("acme/foo", sub_id=3)])
    respx.get(_SUBSCRIPTIONS_URL).mock(side_effect=[page1, page2])
    for owner, name in (("acme", "widget"), ("acme", "gizmo"), ("acme", "foo")):
        respx.get(_releases_url(owner, name)).mock(return_value=httpx.Response(200, json=[]))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    stats = result.stats.model_dump()
    assert stats["repos_seen"] == 3


# ---------------------------------------------------------------------------
# Regressão: comportamento pré-existente do worker, adaptado ao novo contrato de config.
# ---------------------------------------------------------------------------


@respx.mock
def test_happy_path_multiple_repos_multiple_releases() -> None:
    """2 repos (via watch list), 2 releases qualificados cada -> 4 ItemPayload, source/stats
    corretos."""
    _mock_watch_list("acme/widget", "acme/gizmo")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1), _release(2, tag_name="v1.1.0")])
    )
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(10), _release(11, tag_name="v2.0.0")])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    assert len(result.payloads) == 4
    ids = {p.external_id for p in result.payloads if isinstance(p, ItemPayload)}
    assert ids == {"1", "2", "10", "11"}
    widget_items = [
        p
        for p in result.payloads
        if isinstance(p, ItemPayload) and p.source.canonical == "https://github.com/acme/widget"
    ]
    assert len(widget_items) == 2
    assert widget_items[0].source.kind == "github-releases"
    assert widget_items[0].source.title == "acme/widget releases"
    assert widget_items[0].content == "Body **one**."
    assert widget_items[0].url == "https://github.com/acme/widget/releases/tag/v1.0.0"
    assert widget_items[0].metadata == {
        "tag_name": "v1.0.0",
        "repo": "acme/widget",
        "published_at": "2026-06-01T00:00:00Z",
    }
    stats = result.stats.model_dump()
    assert stats["repos_seen"] == 2
    assert stats["releases_seen"] == 4
    assert stats["items"] == 4
    assert stats["rate_limited"] == 0
    # header de auth do token resolvido, nunca na URL.
    assert respx.calls.last.request.headers["authorization"] == f"Bearer {_TOKEN}"


@respx.mock
def test_draft_and_prerelease_are_filtered() -> None:
    """draft=true e prerelease=true são PULADOS (não viram item, não é erro)."""
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(
            200,
            json=[
                _release(1, draft=True),
                _release(2, prerelease=True),
                _release(3),
            ],
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
    _mock_watch_list("acme/widget")
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


@respx.mock
def test_rate_limit_on_one_repo_does_not_abort_others() -> None:
    """429 num repo -> rate_limit registrado, mas o PRÓXIMO repo ainda é coletado."""
    _mock_watch_list("acme/widget", "acme/gizmo")
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(429))
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(5)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.detail == {"repo": "acme/widget", "status": 429}
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "5"
    stats = result.stats.model_dump()
    assert stats["repos_seen"] == 2
    assert stats["rate_limited"] == 1


@respx.mock
def test_forbidden_without_rate_limit_headers_is_http_kind() -> None:
    """403 SEM header de rate limit é permission-denied puro (ex.: PAT sem escopo) —
    NUNCA rate_limit, senão o operador espera uma janela que nunca vai abrir (D55)."""
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(403))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.payloads == []


@respx.mock
def test_forbidden_with_ratelimit_remaining_zero_is_rate_limit_kind() -> None:
    """403 COM `x-ratelimit-remaining: 0` é o sinal primário de rate limit do GitHub ->
    kind=rate_limit (D55)."""
    _mock_watch_list("acme/widget")
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
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(403, headers={"retry-after": "60"})
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.payloads == []


@respx.mock
def test_tag_name_in_metadata_is_sanitized_like_other_fields() -> None:
    """Achado do security-reviewer (ALTO): `tag_name` passa pela MESMA limpeza que
    content/title antes de virar `metadata` — um caractere de controle solto não pode
    sobreviver até a persistência (CBOR estrito) e abortar o batch inteiro."""
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, tag_name="v1.0.0\x00\x01evil")])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].metadata is not None
    assert items[0].metadata["tag_name"] == "v1.0.0evil"  # caracteres de controle removidos


@respx.mock
def test_response_exceeding_byte_cap_is_rejected_without_buffering() -> None:
    """Achado do security-reviewer (MÉDIO): resposta declarando Content-Length acima do
    teto (mesmo padrão de feed.py._fetch) é rejeitada ANTES de bufferizar o corpo."""
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, headers={"content-length": "99999999999"}, content=b"[]")
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert "teto de bytes" in result.error.message
    assert result.payloads == []


@respx.mock
def test_other_http_error_is_http_kind_and_does_not_abort_others() -> None:
    """5xx num repo -> kind=http (não rate_limit), sem abortar o próximo repo."""
    _mock_watch_list("acme/widget", "acme/gizmo")
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(503))
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(7)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.error.detail == {"repo": "acme/widget", "status": 503}
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    stats = result.stats.model_dump()
    assert stats["rate_limited"] == 0


@respx.mock
def test_multiple_repo_errors_returns_first_error_only() -> None:
    """2 repos com erro -> `error` é o PRIMEIRO encontrado (diferença deliberada do feed)."""
    _mock_watch_list("acme/widget", "acme/gizmo")
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(429))
    respx.get(_releases_url("acme", "gizmo")).mock(return_value=httpx.Response(503))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.detail == {"repo": "acme/widget", "status": 429}
    stats = result.stats.model_dump()
    assert stats["rate_limited"] == 1


@respx.mock
def test_malformed_release_missing_id_is_skipped_without_crashing() -> None:
    """Release sem `id` não tem chave de dedupe estável -> skip, não crash."""
    _mock_watch_list("acme/widget")
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
    _mock_watch_list("acme/widget")
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
def test_malformed_subscription_full_name_is_skipped_and_counted() -> None:
    """`full_name` malformado (sem `/`) numa subscription é FILTRADO antes de virar repo a
    processar -- não vira erro, não é fetchado, e é contado em
    `stats["skipped_bad_repo_shape"]`. O repo bem-formado da mesma watch list segue coletado
    normalmente."""
    _mock_watch_list("acme/widget", "no-slash-here")
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].external_id == "1"
    stats = result.stats.model_dump()
    assert stats["skipped_bad_repo_shape"] == 1
    assert stats["repos_seen"] == 1
    assert releases_route.call_count == 1


def test_missing_integration_secret_raises_config_error() -> None:
    """Integração ausente (sem token resolvido) -> ConfigError, nunca skip silencioso.

    Agora exercita `github-watch` (D54: PAT dedicado, não mais `github-releases`)."""
    from kubo.errors import ConfigError

    config = _config()

    with pytest.raises(ConfigError):
        GithubReleasesWorker().run(_ctx(config, token=None))
