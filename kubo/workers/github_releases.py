"""Worker `github-releases` — coleta releases publicadas de repos GitHub (ADR-0009).

Um GET (uma página, `per_page=30`) por repo configurado em `/repos/{owner}/{repo}/releases`
— não pagina: a idempotência do `upsert_item` (chave natural `external_id`) já cobre
re-coleta em runs futuros, então uma página basta na v1. Só `draft == false` vira item;
`prerelease == true` é PULADO (não é erro, item 3 do enunciado). `body`/`name` do release são
MARKDOWN DE TERCEIRO NÃO-CONFIÁVEL (CLAUDE.md §Segurança: todo conteúdo coletado é hostil por
padrão) — limpos com a MESMA disciplina de `_clean` do `FeedWorker` antes de virar
`ItemPayload`. `url`/`external_id` são ESTRUTURAIS: vêm da resposta da API (`html_url`/`id`),
nunca de heurística do worker (item 4).

Rate limit (403/429) e qualquer outro erro de transporte/HTTP NÃO são retriados aqui — retry é
trabalho do orquestrador, nunca do worker (item 5). Um repo com erro não aborta os demais: o
loop registra o `ErrorInfo` e segue para o próximo repo, devolvendo os payloads já coletados
(ADR-0009 §VII, o mesmo padrão de falha-parcial do `FeedWorker`). Diferença DELIBERADA do
`FeedWorker` (que trata um feed por run): este worker trata VÁRIOS repos por run, então pode
haver mais de um erro — devolve-se o PRIMEIRO encontrado, e os stats (`rate_limited`) contam
quantos repos bateram em rate limit.
"""

from __future__ import annotations

import unicodedata
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from kubo.contracts.models import (
    ErrorInfo,
    ItemPayload,
    RunResult,
    SourcePayload,
    Stats,
    WorkerManifest,
)
from kubo.contracts.worker import RunContext
from kubo.errors import ConfigError, ContractError

_CONTENT_CAP = 65536  # mesmo teto do FeedWorker — folgado p/ release notes longas
_TITLE_CAP = 500  # título é rótulo, não corpo
_TIMEOUT = httpx.Timeout(15.0)
_PER_PAGE = 30  # uma página basta na v1 — upsert idempotente cobre re-coleta
_API_VERSION = "2022-11-28"
_RATE_LIMIT_STATUSES = frozenset({403, 429})


def _owner_repo_shape(v: str) -> str:
    """Valida `owner/repo`: não-vazio, exatamente uma barra, sem path traversal.

    Mesmo idioma de `feed.py._http_scheme_only` — falha na CONSTRUÇÃO da config,
    nunca no run."""
    parts = v.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"repo {v!r} deve estar no formato 'owner/repo' (exatamente uma barra)")
    owner, repo = parts
    if ".." in owner or ".." in repo:
        raise ValueError(f"repo {v!r} contém sequência de path traversal ('..')")
    return v


class GithubReleasesConfig(BaseModel):
    """Config declarada do worker `github-releases`: repos a coletar (item VII).

    RUNTIME/execução — não é catálogo (a agendamento fica fora desta sessão)."""

    model_config = ConfigDict(extra="forbid")

    repos: list[str]

    @field_validator("repos")
    @classmethod
    def _repos_shape(cls, v: list[str]) -> list[str]:
        """Valida cada entrada como `owner/repo` já na construção da config."""
        return [_owner_repo_shape(repo) for repo in v]


def _clean(text: object, cap: int) -> str:
    """Remove controle/formato/surrogate (mantém \\n e \\t) e capa o tamanho.

    Mesma disciplina de `feed.py._clean` — `body`/`name` do release são markdown
    de terceiro não-confiável."""
    s = str(text)
    cleaned = "".join(ch for ch in s if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C")
    return cleaned[:cap]


class _FetchError(Exception):
    """Erro interno de fetch por repo — mensagem SEM corpo de resposta, status estruturado."""

    def __init__(self, message: str, status: int | None) -> None:
        super().__init__(message)
        self.status = status


def _headers(token: str) -> dict[str, str]:
    """Headers da API do GitHub — token no `Authorization`, nunca na URL."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }


def _fetch_releases(base_url: str, token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """Busca uma página de releases via GET; sem retry (item 5 — retry é do orquestrador)."""
    url = f"{base_url}/repos/{owner}/{repo}/releases"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, headers=_headers(token), params={"per_page": _PER_PAGE})
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _FetchError(
            f"GitHub respondeu HTTP {exc.response.status_code}", exc.response.status_code
        ) from None
    except httpx.HTTPError as exc:
        raise _FetchError(f"falha de transporte ({type(exc).__name__})", None) from None
    try:
        data = resp.json()
    except ValueError:
        raise _FetchError("resposta da API não é JSON válido", None) from None
    if not isinstance(data, list):
        raise _FetchError("resposta da API não é uma lista de releases", None)
    return [item for item in data if isinstance(item, dict)]


def _html_url(value: Any) -> str | None:
    """`html_url` é estrutural (vem da API) — só aceito se for de fato uma string."""
    return value if isinstance(value, str) and value else None


def _release_to_payload(
    release: dict[str, Any], source: SourcePayload, owner: str, repo: str
) -> ItemPayload | None:
    """Converte um release qualificado (`draft == false`, não-prerelease) em `ItemPayload`.

    Devolve `None` (skip, não erro) quando draft/prerelease ou sem `id` (sem chave de
    dedupe estável, item 4)."""
    if release.get("draft") or release.get("prerelease"):
        return None
    release_id = release.get("id")
    if release_id is None:
        return None
    raw_title = release.get("name") or release.get("tag_name") or ""
    return ItemPayload(
        source=source,
        external_id=str(release_id),
        content=_clean(release.get("body") or "", _CONTENT_CAP),
        url=_html_url(release.get("html_url")),
        title=_clean(raw_title, _TITLE_CAP) if raw_title else None,
        metadata={"tag_name": release.get("tag_name"), "repo": f"{owner}/{repo}"},
    )


class GithubReleasesWorker:
    """Coleta releases publicadas (`draft == false`) dos repos configurados, um GET por repo
    (item 2). Não fala com a store: devolve `RunResult`, o runtime persiste."""

    manifest = WorkerManifest(
        name="github-releases",
        version="0.1.0",
        integrations=["github-releases"],
        config=GithubReleasesConfig,
    )

    def run(self, ctx: RunContext) -> RunResult:
        """Busca as releases de cada repo configurado e converte em `ItemPayload`s.

        Token/base_url vêm da integração resolvida pelo runtime (nunca de `os.environ` —
        item 1); ausente vira `ConfigError` (nunca skip silencioso)."""
        config = ctx.config
        if not isinstance(config, GithubReleasesConfig):  # narrowing (padrão do FeedWorker)
            raise ContractError(
                f"GithubReleasesWorker recebeu config {type(config).__name__}, "
                "esperava GithubReleasesConfig"
            )
        integration_name = self.manifest.integrations[0]
        integration = ctx.integrations.get(integration_name)
        if integration is None or not integration.secret:
            raise ConfigError(
                f"worker github-releases requer a integração {integration_name!r} "
                "com token resolvido"
            )
        token = integration.secret
        base_url = integration.base_url or "https://api.github.com"
        log = ctx.logger.bind(worker="github-releases")

        items: list[ItemPayload] = []
        first_error: ErrorInfo | None = None
        repos_seen = 0
        releases_seen = 0
        rate_limited = 0

        for repo in config.repos:
            repos_seen += 1
            owner, _, name = repo.partition("/")
            try:
                releases = _fetch_releases(base_url, token, owner, name)
            except _FetchError as exc:
                is_rate_limit = exc.status in _RATE_LIMIT_STATUSES
                kind = "rate_limit" if is_rate_limit else "http"
                rate_limited += 1 if is_rate_limit else 0
                log.warning("github_releases_fetch_failed", repo=repo, kind=kind)
                detail: dict[str, Any] = {"repo": repo}
                if exc.status is not None:
                    detail["status"] = exc.status
                error = ErrorInfo(kind=kind, message=str(exc)[:500], detail=detail)
                if first_error is None:
                    first_error = error
                continue  # um repo com erro não aborta os demais (item 5)

            source = SourcePayload(
                kind="github-releases",
                canonical=f"https://github.com/{owner}/{name}",
                title=f"{owner}/{name} releases",
            )
            for release in releases:
                releases_seen += 1
                payload = _release_to_payload(release, source, owner, name)
                if payload is not None:
                    items.append(payload)

        stats = Stats.model_validate(
            {
                "repos_seen": repos_seen,
                "releases_seen": releases_seen,
                "items": len(items),
                "rate_limited": rate_limited,
            }
        )
        log.info("github_releases_collected", repos_seen=repos_seen, items=len(items))
        return RunResult(payloads=list(items), stats=stats, error=first_error)
