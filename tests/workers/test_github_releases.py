"""Worker `github-releases` — unit-only (sem banco), no molde de test_feed.py.

respx mocka o httpx (sem rede real); `GithubReleasesWorker().run(ctx)` é chamado direto
com um `RunContext` montado à mão (mesmo padrão de tests/workers/test_feed.py). Sem
`@pytest.mark.integration` — este workspace não tem SurrealDB de pé.

v0.2.0 (D51/D52/D54, sessão 0021): a config estática `repos: list[str]` foi REMOVIDA — o
worker agora lê a watch list do operador via `GET /user/subscriptions` (paginado) e coleta
releases de cada repo descoberto, filtrando por `since` (`published_at`). A integração
dedicada é `github-watch` (não mais `github-releases`). `/user/subscriptions` é mockado
com o mesmo respx usado para `/repos/{owner}/{repo}/releases`.

v0.3.0 (D57): a descoberta migra de REST (`GET /user/subscriptions`) para GraphQL
(`POST /graphql`, `viewer.watching`, paginação por cursor) — o REST silenciosamente
subcontava (243 repos vs. 261 reais com o mesmo PAT, mecanismo não diagnosticado). A busca
de releases por repo (`GET /repos/{owner}/{repo}/releases`) permanece REST, inalterada.
Estes testes são o RED da migração — escritos ANTES da implementação, contra o contrato
combinado com o dono; ver `_WATCHING_QUERY`/`_fetch_watched_repos`/`_discover_repos` em
`kubo/workers/github_releases.py` (ainda não implementados neste ponto do ciclo TDD).
"""

from __future__ import annotations

import gzip
import json as jsonlib
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import httpx
import pytest
import respx
import structlog
from pydantic import ValidationError

from kubo.contracts.models import ItemPayload
from kubo.runtime.context import GraphKnowledge, RunContext
from kubo.runtime.integrations import ResolvedIntegration
from kubo.workers import github_releases as _github_releases_module
from kubo.workers.github_releases import GithubReleasesConfig, GithubReleasesWorker

_BASE_URL = "https://api.github.com"
_TOKEN = "ghr-secret-token"  # pragma: allowlist secret
_GRAPHQL_URL = f"{_BASE_URL}/graphql"
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


def _watching_node(full_name: object) -> dict[str, object]:
    """Constrói um node de `viewer.watching.nodes`, no molde real da API GraphQL do GitHub."""
    return {"nameWithOwner": full_name}


def _graphql_response(
    nodes: Sequence[object], *, has_next: bool = False, end_cursor: str | None = None
) -> dict[str, object]:
    """Corpo de SUCESSO de uma resposta `/graphql` (`viewer.watching`), no molde real."""
    return {
        "data": {
            "viewer": {
                "watching": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                }
            }
        }
    }


def _graphql_error_response(error_type: str, message: str = "erro graphql") -> dict[str, object]:
    """Corpo de erro no NÍVEL DO GRAPHQL (HTTP 200) -- `errors[].type`, no molde real."""
    return {"errors": [{"type": error_type, "message": message}]}


def _mock_watching(
    *repos: str, has_next: bool = False, end_cursor: str | None = None
) -> respx.Route:
    """Mocka `POST /graphql` com UMA página de `viewer.watching` cobrindo `repos` (sem
    próxima página por padrão)."""
    nodes = [_watching_node(r) for r in repos]
    return respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200, json=_graphql_response(nodes, has_next=has_next, end_cursor=end_cursor)
        )
    )


def _mock_watch_list(*repos: str) -> None:
    """Mocka `/graphql` com UMA página cobrindo `repos` -- equivalente GraphQL (D57) do
    antigo helper baseado em `GET /user/subscriptions`. Mantido com o mesmo nome pra não
    reescrever cada call site fora da seção de paginação (since-filtering, classificação de
    erro etc. só precisam de UM repo descoberto, mecanismo de descoberta é incidental)."""
    _mock_watching(*repos)


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
    naive_since = datetime(2026, 7, 1)  # noqa: DTZ001 -- naive é o ponto do teste
    with pytest.raises(ValidationError):
        GithubReleasesConfig(since=naive_since)


def test_config_accepts_aware_since() -> None:
    """`since` tz-aware constrói normalmente e fica acessível no objeto."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    config = GithubReleasesConfig(since=since)
    assert config.since == since


def test_config_rejects_leftover_repos_key() -> None:
    """`extra=\"forbid\"` permanece -- uma chave `repos` remanescente (do contrato v1) é
    rejeitada na construção, não ignorada silenciosamente."""
    since = datetime(2026, 7, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        GithubReleasesConfig(since=since, repos=["acme/widget"])  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Manifest (D54/D57): versão 0.3.0, integração dedicada `github-watch`.
# ---------------------------------------------------------------------------


def test_manifest_version_is_0_3_0() -> None:
    assert GithubReleasesWorker.manifest.version == "0.3.0"


def test_manifest_integrations_lists_github_watch_only() -> None:
    """UMA integração dedicada (`github-watch`) cobre tanto `viewer.watching` (GraphQL)
    quanto `/repos/.../releases` (REST) -- D54, PAT próprio, não o `github-releases`
    antigo."""
    assert GithubReleasesWorker.manifest.integrations == ["github-watch"]


# ---------------------------------------------------------------------------
# Descoberta via GraphQL (D57): `viewer.watching`, paginação por cursor.
# ---------------------------------------------------------------------------


@respx.mock
def test_happy_path_single_page_watching() -> None:
    """UMA resposta GraphQL com 2 repos, `hasNextPage=False` -> releases dos DOIS repos
    são buscadas, e `repos_total`/`repos_discovered` refletem os 2 (nada foi filtrado)."""
    _mock_watching("acme/widget", "acme/gizmo")
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(200, json=[]))
    respx.get(_releases_url("acme", "gizmo")).mock(return_value=httpx.Response(200, json=[]))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    stats = result.stats.model_dump()
    assert stats["repos_seen"] == 2
    assert stats["repos_total"] == 2
    assert stats["repos_discovered"] == 2


@respx.mock
def test_watching_pagination_walks_every_page_and_threads_cursor() -> None:
    """3 páginas (2+2+1 repos) via cursor -> releases de TODOS os repos são buscadas, E o
    `cursor` de cada POST é exatamente o `endCursor` da página anterior (nunca um valor
    inventado/re-derivado pelo worker) -- primeira página sempre com `cursor: None`."""
    page1 = httpx.Response(
        200,
        json=_graphql_response(
            [_watching_node("acme/widget"), _watching_node("acme/gizmo")],
            has_next=True,
            end_cursor="cursor-1",
        ),
    )
    page2 = httpx.Response(
        200,
        json=_graphql_response(
            [_watching_node("acme/foo"), _watching_node("acme/bar")],
            has_next=True,
            end_cursor="cursor-2",
        ),
    )
    page3 = httpx.Response(
        200, json=_graphql_response([_watching_node("acme/baz")], has_next=False)
    )
    route = respx.post(_GRAPHQL_URL).mock(side_effect=[page1, page2, page3])

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
    assert stats["repos_total"] == 5
    assert stats["repos_discovered"] == 5

    bodies = [jsonlib.loads(call.request.content) for call in route.calls]
    assert bodies[0]["variables"]["cursor"] is None
    assert bodies[1]["variables"]["cursor"] == "cursor-1"
    assert bodies[2]["variables"]["cursor"] == "cursor-2"


@respx.mock
def test_watching_pagination_checks_deadline_between_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dívida do roadmap item 1: `_RUN_DEADLINE` só era checado ENTRE repos -- a paginação
    da descoberta podia estourar o orçamento inteira sem interrupção (até
    `_MAX_WATCHING_PAGES × _TIMEOUT` = 150s fora de qualquer checagem). `_RUN_DEADLINE`
    reduzido a um valor minúsculo + a PRIMEIRA página deliberadamente lenta -> o relógio já
    cruzou o deadline quando o loop chega na SEGUNDA página -> falha estruturada
    `kind=\"timeout\"` ANTES do segundo POST, nunca os repos da primeira página devolvidos
    como se fossem a lista completa (a mesma subcontagem silenciosa que motivou o D57)."""
    monkeypatch.setattr(_github_releases_module, "_RUN_DEADLINE", 0.05)

    def _slow_first_page(request: httpx.Request) -> httpx.Response:
        time.sleep(0.15)  # ultrapassa o deadline minúsculo antes da 2ª página
        return httpx.Response(
            200,
            json=_graphql_response(
                [_watching_node("acme/widget")], has_next=True, end_cursor="cursor-1"
            ),
        )

    route = respx.post(_GRAPHQL_URL).mock(side_effect=_slow_first_page)
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert route.call_count == 1  # nunca tenta a 2ª página
    assert result.error is not None
    assert result.error.kind == "timeout"
    assert result.payloads == []
    assert releases_route.call_count == 0


@respx.mock
def test_watching_pagination_hard_cap_exceeded_fails_structured() -> None:
    """`hasNextPage=True` SEMPRE presente, com `endCursor` sempre novo (paginação
    patológica/infinita, ou watch list real acima de `_MAX_WATCHING_PAGES` páginas) -> o
    worker para depois de um teto fixo de páginas (10), sem travar, mas FALHA estruturada
    (achado do CodeRabbit, PR #61: devolver os repos já vistos como se fossem a lista
    COMPLETA recriaria exatamente a subcontagem silenciosa que motivou a migração D57).
    Nenhum repo é processado -- a descoberta nunca fechou com sucesso."""
    counter = {"n": 0}

    def _endless_response(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json=_graphql_response(
                [_watching_node("acme/widget")],
                has_next=True,
                end_cursor=f"cursor-{counter['n']}",
            ),
        )

    route = respx.post(_GRAPHQL_URL).mock(side_effect=_endless_response)
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert route.call_count == 10
    assert result.error is not None
    assert result.error.kind == "http"
    assert "teto" in result.error.message
    assert result.payloads == []
    assert releases_route.call_count == 0


@respx.mock
def test_watching_has_next_page_true_with_null_cursor_fails_structured() -> None:
    """`hasNextPage=True` mas `endCursor=None` (resposta malformada -- a API promete mais
    páginas sem dar como pedir a próxima) -> FALHA estruturada, nunca sucesso silencioso com
    só os repos já vistos até ali (achado do CodeRabbit, PR #61: tratar isto como sucesso
    parcial recriaria a subcontagem silenciosa que motivou a migração D57)."""
    route = _mock_watching("acme/widget", has_next=True, end_cursor=None)
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert route.call_count == 1
    assert result.error is not None
    assert result.error.kind == "http"
    assert result.payloads == []
    assert releases_route.call_count == 0


@respx.mock
def test_duplicate_repo_across_pages_is_deduped_and_fetched_once() -> None:
    """Achado do CodeRabbit (PR #57), preservado sob GraphQL (D57): o mesmo repo aparecendo
    em 2 páginas da watch list não pode gerar 2 fetches de releases -- dedup por ordem de
    primeira aparição, sem inflar `repos_seen`."""
    page1 = httpx.Response(
        200,
        json=_graphql_response(
            [_watching_node("acme/widget")], has_next=True, end_cursor="cursor-1"
        ),
    )
    page2 = httpx.Response(
        200, json=_graphql_response([_watching_node("acme/widget")], has_next=False)
    )
    respx.post(_GRAPHQL_URL).mock(side_effect=[page1, page2])
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    assert releases_route.call_count == 1
    stats = result.stats.model_dump()
    assert stats["repos_seen"] == 1
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1


@respx.mock
def test_watching_query_is_frozen_constant_cursor_travels_via_variables() -> None:
    """A query GraphQL é uma constante FIXA (`_WATCHING_QUERY`) -- o cursor NUNCA é
    interpolado na string da query (fecha injection-via-string-formatting por construção);
    ele só viaja em `variables.cursor`.

    `getattr` (não import direto do símbolo) DELIBERADO: `_WATCHING_QUERY` ainda não
    existe em produção neste ponto do ciclo TDD (RED) -- um `from ... import` estático
    quebraria a COLETA do arquivo inteiro no pyright/import-time, quando o objetivo é uma
    falha de asserção isolada neste teste."""
    watching_query = getattr(_github_releases_module, "_WATCHING_QUERY", None)
    assert watching_query is not None, "_WATCHING_QUERY ainda não existe em produção (RED)"

    route = _mock_watching("acme/widget", "acme/gizmo")
    respx.get(_releases_url("acme", "widget")).mock(return_value=httpx.Response(200, json=[]))
    respx.get(_releases_url("acme", "gizmo")).mock(return_value=httpx.Response(200, json=[]))

    GithubReleasesWorker().run(_ctx(_config()))

    assert route.call_count >= 1
    for call in route.calls:
        body = jsonlib.loads(call.request.content)
        assert body["query"] == watching_query


@respx.mock
def test_null_node_in_watching_is_filtered_not_crash() -> None:
    """`nodes` com um `None` LITERAL ao lado de um node válido -> o worker não crasha,
    `repos_total` conta só o node válido (dict), e o repo válido é processado
    normalmente."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json=_graphql_response([_watching_node("acme/widget"), None], has_next=False),
        )
    )
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    stats = result.stats.model_dump()
    assert stats["repos_total"] == 1


# ---------------------------------------------------------------------------
# Watch list vazia é ERRO, nunca run limpo (C3 -- armadilha silenciosa do D51: PAT sem
# escopo `notifications` faz `viewer.watching` devolver `nodes: []` SEM erro do GitHub).
# ---------------------------------------------------------------------------


@respx.mock
def test_empty_watch_list_is_config_error() -> None:
    """`viewer.watching` devolve `nodes: []` -> `ErrorInfo(kind=\"config\")`, NUNCA um run
    limpo e vazio -- e nenhum endpoint de releases é sequer chamado (nada pra buscar)."""
    _mock_watching()
    releases_route = respx.route(method="GET", url__regex=r".*/repos/.+/releases").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "config"
    assert result.payloads == []
    assert releases_route.call_count == 0
    stats = result.stats.model_dump()
    assert stats["repos_total"] == 0
    assert stats["repos_discovered"] == 0


# ---------------------------------------------------------------------------
# Erros no NÍVEL DO GRAPHQL (HTTP 200, `errors[]`) -- distintos de falha de transporte/HTTP.
# ---------------------------------------------------------------------------


@respx.mock
def test_graphql_error_rate_limited_classifies_as_rate_limit() -> None:
    """Erro no corpo GraphQL (HTTP 200) com `type == \"RATE_LIMITED\"` -> classificado como
    `kind=\"rate_limit\"`, com o tipo bruto preservado em `detail` -- descoberta falhou
    ANTES de qualquer repo ser conhecido, então nenhum endpoint de releases é chamado."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200, json=_graphql_error_response("RATE_LIMITED", "API rate limit exceeded")
        )
    )
    releases_route = respx.route(method="GET", url__regex=r".*/repos/.+/releases").mock(
        return_value=httpx.Response(200, json=[])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.detail is not None
    assert result.error.detail.get("graphql_error_type") == "RATE_LIMITED"
    assert result.payloads == []
    assert releases_route.call_count == 0


@respx.mock
def test_graphql_error_forbidden_classifies_as_http_not_config_not_rate_limit() -> None:
    """Erro no corpo GraphQL com `type` DIFERENTE de `RATE_LIMITED` (ex.: `FORBIDDEN`) ->
    `kind=\"http\"`, NUNCA `config` (não é watch list vazia) nem `rate_limit`."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json=_graphql_error_response("FORBIDDEN", "acesso negado"))
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.error.detail is not None
    assert result.error.detail.get("graphql_error_type") == "FORBIDDEN"


@respx.mock
def test_graphql_errors_multiple_types_prefers_rate_limited() -> None:
    """`errors` com VÁRIOS itens (`[{"type": "FORBIDDEN"}, {"type": "RATE_LIMITED"}]`) --
    achado do CodeRabbit (PR #61): inspecionar só `errors[0]` classificaria isto como
    `http`, escondendo o rate limit real que também está na lista. `RATE_LIMITED` em
    QUALQUER posição vence, não só na primeira."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": [
                    {"type": "FORBIDDEN", "message": "acesso negado"},
                    {"type": "RATE_LIMITED", "message": "API rate limit exceeded"},
                ]
            },
        )
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "rate_limit"
    assert result.error.detail is not None
    assert result.error.detail.get("graphql_error_type") == "RATE_LIMITED"


@respx.mock
def test_watching_response_missing_data_field_fails_structured() -> None:
    """Corpo `{}` (sem `data` nem `errors`) -- resposta GraphQL malformada -- FALHA
    estruturada explícita, nunca um `{}` silencioso tratado como "zero repos encontrados"
    (achado do CodeRabbit, PR #61: o envelope precisa ser validado, não assumido)."""
    respx.post(_GRAPHQL_URL).mock(return_value=httpx.Response(200, json={}))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.payloads == []


@respx.mock
def test_watching_response_missing_page_info_fails_structured() -> None:
    """`watching.pageInfo` ausente -- envelope malformado além de `nodes` -- FALHA
    estruturada, mesma disciplina que `data` ausente."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"viewer": {"watching": {"nodes": [_watching_node("acme/widget")]}}}},
        )
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.payloads == []


@respx.mock
def test_graphql_errors_present_alongside_data_still_counts_as_failure() -> None:
    """`errors` presente E `data.viewer.watching` populado ao mesmo tempo (o GraphQL
    permite essa forma de sucesso-parcial) -> política do worker é `errors` presente SEMPRE
    vence -- nunca um sucesso/parcial misto que descubra alguns repos e ignore o erro."""
    body = _graphql_response([_watching_node("acme/widget")], has_next=False)
    body["errors"] = [{"type": "FORBIDDEN", "message": "acesso negado"}]
    respx.post(_GRAPHQL_URL).mock(return_value=httpx.Response(200, json=body))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert result.error.detail is not None
    assert result.error.detail.get("graphql_error_type") == "FORBIDDEN"


@respx.mock
def test_graphql_transport_failure_classifies_via_http_status_unchanged() -> None:
    """Falha de TRANSPORTE pura (503, sem corpo GraphQL válido) -> classificação continua
    via status HTTP (caminho INALTERADO de hoje, `graphql_error_type` nunca setado)."""
    respx.post(_GRAPHQL_URL).mock(return_value=httpx.Response(503))

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert not (result.error.detail or {}).get("graphql_error_type")


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
    # Accept-Encoding: identity é OBRIGATÓRIO (achado do smoke físico, sessão 0021 passo 4):
    # sem ele, o GitHub responde gzip por padrão e `_stream_json_list` usa `iter_raw()` (bytes
    # de FIO, NUNCA decodificados) -- json.loads no corpo ainda comprimido falha com "resposta
    # da API não é JSON válido" em TODO request real, mascarado pelos mocks do respx (que nunca
    # comprimem de verdade). Mesma disciplina de feed.py._fetch.
    assert respx.calls.last.request.headers["accept-encoding"] == "identity"


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
def test_gzip_encoded_response_fails_safely_not_silently() -> None:
    """Achado do smoke físico (sessão 0021 passo 4): `_stream_json_list` lê via `iter_raw()`
    (bytes de FIO, NUNCA decodificados -- mesma disciplina de `feed.py._fetch` contra
    decompression bomb), então uma resposta gzip nunca vira JSON válido NESTE worker, ponto.
    A defesa real é `Accept-Encoding: identity` no request (o GitHub honra e não comprime,
    confirmado ao vivo no smoke) -- este teste não prova decompressão (o worker
    deliberadamente não decodifica), prova que SE alguma resposta ainda chegar gzip (proxy/CDN
    que ignore o header), a falha é ESTRUTURADA (`kind='http'`, mensagem clara), nunca um
    crash nem dado perdido silenciosamente."""
    releases_json = jsonlib.dumps([_release(1)]).encode()
    _mock_watch_list("acme/widget")
    respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(
            200,
            content=gzip.compress(releases_json),
            headers={"content-encoding": "gzip"},
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
def test_watching_response_exceeding_byte_cap_is_rejected_without_buffering() -> None:
    """Achado do security-reviewer (D57, BAIXO): `_stream_json` é compartilhado entre GET
    (releases) e POST (watching GraphQL) -- o teste acima só cobria o path REST. Mesma
    disciplina precisa valer pro POST /graphql: resposta declarando Content-Length acima
    do teto é rejeitada ANTES de bufferizar, e a falha na DESCOBERTA (não num repo
    individual) encerra o run cedo, sem chegar a chamar releases."""
    watching_route = respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(200, headers={"content-length": "99999999999"}, content=b"{}")
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is not None
    assert result.error.kind == "http"
    assert "teto de bytes" in result.error.message
    assert result.payloads == []
    # nenhuma rota de releases foi registrada -- se o worker tentasse chamar uma, o respx
    # levantaria AllMockedAssertionError; o único POST que aconteceu foi a descoberta.
    assert watching_route.call_count == 1


@respx.mock
def test_streaming_byte_cap_is_rejected_without_content_length_header() -> None:
    """Dívida do roadmap item 3: o teste de teto de bytes acima cobria só o pré-check de
    `Content-Length` DECLARADO -- o laço `iter_raw` + acumulador corrido (a defesa real
    contra chunked transfer sem `Content-Length` honesto, justo o caminho de ataque) seguia
    sem teste. Corpo construído via GENERATOR (não bytes prontos): httpx não computa
    `Content-Length` pra conteúdo streamado, então o pré-check nem dispara -- só o
    acumulador do laço pode cortar."""
    _mock_watch_list("acme/widget")

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
def test_malformed_watching_full_name_is_skipped_and_counted() -> None:
    """`nameWithOwner` malformado (sem `/`) num node de `viewer.watching` é FILTRADO antes
    de virar repo a processar -- não vira erro, não é fetchado, e é contado em
    `stats[\"skipped_bad_repo_shape\"]`. `repos_total` conta o BRUTO (nodes pós-filtro-de-
    nulos, PRÉ validação de shape); `repos_discovered` conta só o que sobrou depois do
    filtro de shape/dedup. O repo bem-formado da mesma watch list segue coletado
    normalmente."""
    _mock_watching("acme/widget", "no-slash-here")
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
    assert stats["repos_total"] == 2
    assert stats["repos_discovered"] == 1
    assert releases_route.call_count == 1


@respx.mock
def test_full_name_with_query_string_is_counted_as_bad_shape() -> None:
    """Dívida do PR #57/roadmap item 4: `_is_valid_repo_shape` validava só "uma barra, sem
    `..`", deixando passar `acme/widget?x=1` -- query string vazando pra URL montada em
    `_fetch_releases`. Whitelist de caracteres por parte (`[A-Za-z0-9._-]`) fecha isso:
    `?`, `#`, espaço etc. viram `skipped_bad_repo_shape`, nunca um repo processado."""
    _mock_watching("acme/widget", "acme/widget?x=1")
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    stats = result.stats.model_dump()
    assert stats["skipped_bad_repo_shape"] == 1
    assert releases_route.call_count == 1


@respx.mock
def test_missing_or_blank_full_name_is_counted_as_bad_shape() -> None:
    """`nameWithOwner` ausente, vazio ou não-string num node de `viewer.watching` (dívida
    do PR #57, item 2: essa forma de malformação era descartada em SILÊNCIO, sem contar
    em `skipped_bad_repo_shape` -- só a forma "sem barra" contava) agora entra no MESMO
    contador que a forma errada -- é a mesma família de registro malformado, e o operador
    olhando os stats não tem como distinguir "watch list limpa" de "alguns registros sem
    `full_name` sumiram sem rastro" se só um dos dois casos for contado."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json=_graphql_response(
                [
                    _watching_node("acme/widget"),
                    {},  # chave ausente
                    _watching_node(""),  # vazio
                    _watching_node(123),  # não-string
                ],
                has_next=False,
            ),
        )
    )
    releases_route = respx.get(_releases_url("acme", "widget")).mock(
        return_value=httpx.Response(200, json=[_release(1)])
    )

    result = GithubReleasesWorker().run(_ctx(_config()))

    assert result.error is None
    items = [p for p in result.payloads if isinstance(p, ItemPayload)]
    assert len(items) == 1
    stats = result.stats.model_dump()
    assert stats["skipped_bad_repo_shape"] == 3
    assert stats["repos_total"] == 4
    assert stats["repos_discovered"] == 1
    assert releases_route.call_count == 1


def test_missing_integration_secret_raises_config_error() -> None:
    """Integração ausente (sem token resolvido) -> ConfigError, nunca skip silencioso.

    Agora exercita `github-watch` (D54: PAT dedicado, não mais `github-releases`)."""
    from kubo.errors import ConfigError

    config = _config()

    with pytest.raises(ConfigError):
        GithubReleasesWorker().run(_ctx(config, token=None))
