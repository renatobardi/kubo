"""Worker `github-releases` — unit-only (sem banco), no molde de test_feed.py.

respx mocka o httpx (sem rede real); `GithubReleasesWorker().run(ctx)` é chamado direto
com um `RunContext` montado à mão (mesmo padrão de tests/workers/test_feed.py). Sem
`@pytest.mark.integration` — este workspace não tem SurrealDB de pé.
"""

from __future__ import annotations

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


def _release(
    release_id: int,
    *,
    tag_name: str = "v1.0.0",
    name: str | None = "Release One",
    body: str | None = "Body **one**.",
    draft: bool = False,
    prerelease: bool = False,
    html_url: str | None = "https://github.com/acme/widget/releases/tag/v1.0.0",
) -> dict[str, object]:
    """Constrói um dict de release da API, no molde da resposta real do GitHub."""
    return {
        "id": release_id,
        "tag_name": tag_name,
        "name": name,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": html_url,
    }


def _ctx(config: GithubReleasesConfig, *, token: str | None = _TOKEN) -> RunContext:
    """Monta o RunContext à mão — sem passar por runner/store."""
    integrations = {}
    if token is not None:
        integrations["github-releases"] = ResolvedIntegration(
            name="github-releases",
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


@respx.mock
def test_happy_path_multiple_repos_multiple_releases() -> None:
    """2 repos, 2 releases qualificados cada -> 4 ItemPayload, source/stats corretos."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1), _release(2, tag_name="v1.1.0")])
    )
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(10), _release(11, tag_name="v2.0.0")])
    )
    config = GithubReleasesConfig(repos=["acme/widget", "acme/gizmo"])

    result = GithubReleasesWorker().run(_ctx(config))

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
    assert widget_items[0].metadata == {"tag_name": "v1.0.0", "repo": "acme/widget"}
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
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

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
    config = GithubReleasesConfig(repos=["acme/widget"])

    route.mock(return_value=httpx.Response(200, json=[_release(42, name="v1")]))
    result_before = GithubReleasesWorker().run(_ctx(config))

    route.mock(return_value=httpx.Response(200, json=[_release(42, name="v1 (edited)")]))
    result_after = GithubReleasesWorker().run(_ctx(config))

    payload_before = result_before.payloads[0]
    payload_after = result_after.payloads[0]
    assert isinstance(payload_before, ItemPayload)  # narrowing (padrão do FeedWorker)
    assert isinstance(payload_after, ItemPayload)
    assert payload_before.external_id == payload_after.external_id == "42"


@respx.mock
def test_rate_limit_on_one_repo_does_not_abort_others() -> None:
    """429 num repo -> rate_limit registrado, mas o PRÓXIMO repo ainda é coletado."""
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(429))
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(5)])
    )
    config = GithubReleasesConfig(repos=["acme/widget", "acme/gizmo"])

    result = GithubReleasesWorker().run(_ctx(config))

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
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(403))
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

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
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

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
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.payloads == []


@respx.mock
def test_tag_name_in_metadata_is_sanitized_like_other_fields() -> None:
    """Achado do security-reviewer (ALTO): `tag_name` passa pela MESMA limpeza que
    content/title antes de virar `metadata` — um caractere de controle solto não pode
    sobreviver até a persistência (CBOR estrito) e abortar o batch inteiro."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1, tag_name="v1.0.0\x00\x01evil")])
    )
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].metadata is not None
    assert items[0].metadata["tag_name"] == "v1.0.0evil"  # caracteres de controle removidos


@respx.mock
def test_response_exceeding_byte_cap_is_rejected_without_buffering() -> None:
    """Achado do security-reviewer (MÉDIO): resposta declarando Content-Length acima do
    teto (mesmo padrão de feed.py._fetch) é rejeitada ANTES de bufferizar o corpo."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, headers={"content-length": "99999999999"}, content=b"[]")
    )
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

    assert result.error is not None
    assert result.error.kind == "http"
    assert "teto de bytes" in result.error.message
    assert result.payloads == []


@respx.mock
def test_other_http_error_is_http_kind_and_does_not_abort_others() -> None:
    """5xx num repo -> kind=http (não rate_limit), sem abortar o próximo repo."""
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(503))
    respx.get(_releases_url("acme", "gizmo")).mock(
        return_value=httpx.Response(200, json=[_release(7)])
    )
    config = GithubReleasesConfig(repos=["acme/widget", "acme/gizmo"])

    result = GithubReleasesWorker().run(_ctx(config))

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
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(429))
    respx.get(_releases_url("acme", "gizmo")).mock(return_value=httpx.Response(503))
    config = GithubReleasesConfig(repos=["acme/widget", "acme/gizmo"])

    result = GithubReleasesWorker().run(_ctx(config))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.detail == {"repo": "acme/widget", "status": 429}
    stats = result.stats.model_dump()
    assert stats["rate_limited"] == 1


@respx.mock
def test_malformed_release_missing_id_is_skipped_without_crashing() -> None:
    """Release sem `id` não tem chave de dedupe estável -> skip, não crash."""
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[{"draft": False, "prerelease": False}, _release(9)])
    )
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

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
                }
            ],
        )
    )
    config = GithubReleasesConfig(repos=["acme/widget"])

    result = GithubReleasesWorker().run(_ctx(config))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    assert items[0].title == "v9.9.9"
    assert items[0].content == ""
    assert items[0].url is None
    assert items[0].metadata == {"tag_name": "v9.9.9", "repo": "acme/widget"}


@pytest.mark.parametrize(
    "repo",
    [
        "no-slash-here",
        "owner/repo/extra",
        "/repo",
        "owner/",
        "../etc",
        "owner/..",
        "",
    ],
)
def test_invalid_repo_shape_rejected_at_construction(repo: str) -> None:
    """Formato inválido de 'owner/repo' é rejeitado na validação da CONFIG, não no run."""
    with pytest.raises(ValidationError):
        GithubReleasesConfig(repos=[repo])


def test_missing_integration_secret_raises_config_error() -> None:
    """Integração ausente (sem token resolvido) -> ConfigError, nunca skip silencioso."""
    from kubo.errors import ConfigError

    config = GithubReleasesConfig(repos=["acme/widget"])

    with pytest.raises(ConfigError):
        GithubReleasesWorker().run(_ctx(config, token=None))
