"""Worker `github-releases` — coleta releases publicadas de UM repo (D51/D52/#110).

v0.4.0 (#110, ADR-0025 §5): a descoberta dinâmica da watch list (GraphQL `viewer.watching`,
v0.3.0/D57) foi REMOVIDA. O repo não é mais descoberto — é um Cadastro `github-repo` cadastrado
à mão (UI #105) que dirige a própria coleta. O sweep (`SWEEP_DISPATCH["github-repo"]`,
`kubo/scheduler/sweep.py`) varre os Cadastros ativos e dispara UM run por repo, exatamente como
o sweep `rss` faz para o worker `feed` — "um run = um Cadastro" (ADR-0009/ADR-0025 §4). Todo o
cliente GraphQL, a paginação por cursor, o teto de páginas e o `_RUN_DEADLINE` (que existiam só
para processar ~260 repos descobertos num único run) se aposentaram com a descoberta.

Config agora tem `repo` + `since`, ambos obrigatórios:
- `repo` = `owner/name`, validado na CONSTRUÇÃO (shape `owner/name`, whitelist `[A-Za-z0-9._-]`
  por parte, sem `..`) — o sweep o deriva da canonical do Cadastro (`https://github.com/o/r` →
  `o/r`), então shape inválido é bug de fiação, barrado alto na borda, nunca em runtime no meio
  do loop. É o mesmo regime de validação do antigo `repos: list[str]` da v0.1.0 (config declarada,
  validada por pydantic), não o filtro-em-runtime da v0.3.0 (que validava dado de API externa).
- `since` = corte temporal tz-aware (rejeitado naive na construção — comparar com o `published_at`
  do GitHub, sempre UTC `Z`, exige tz-aware dos dois lados). SEMÂNTICA MUDOU (#110/D2): o sweep
  passa `since = created_at` do Cadastro — o piso de estreia de CADA repo, não mais um watermark
  global congelado no `schedules.yaml` (dívida "since congelado PARA SEMPRE", agora morta). Repo
  cadastrado hoje só coleta releases publicadas a partir de hoje (sem backfill, D52); quem avança
  o corte de run para run é a idempotência do `upsert_item` (chave natural `external_id`), não o
  `since`.

Integração `github-readonly` (#110): sem descoberta, o worker só lê `/repos/{owner}/{repo}/releases`
— leitura pública pura, sem o escopo `notifications` que a v0.2.0/D54 exigia para
`/user/subscriptions`. O PAT dedicado `github-watch` perdeu a justificativa de least-privilege que
o criou; o worker volta ao `github-readonly`/`GITHUB_TOKEN_READONLY` (o mesmo do rito de promoção,
também leitura pura — sem alargamento de escopo, ao contrário do que o D54 evitava, porque a
descoberta que exigia o escrito extra não existe mais).

Um GET (uma página, `per_page=30`) em `/repos/{owner}/{repo}/releases` — não pagina releases: a
idempotência do `upsert_item` já cobre re-coleta em runs futuros, uma página basta. Só
`draft == false` vira item; `prerelease == true` é PULADO (não é erro). `body`/`name`/`tag_name`
do release são MARKDOWN DE TERCEIRO NÃO-CONFIÁVEL (CLAUDE.md §Segurança: todo conteúdo coletado é
hostil por padrão) — limpos com a MESMA disciplina de `_clean` do `FeedWorker` antes de virar
`ItemPayload`. `url`/`external_id` são ESTRUTURAIS: vêm da resposta da API (`html_url`/`id`),
nunca de heurística do worker.

Rate limit (429 sempre; 403 só COM `x-ratelimit-remaining: 0` ou `retry-after` — um 403 puro é
permission-denied, ex.: PAT sem escopo, e NUNCA deve virar rate_limit, D55) e qualquer outro erro
de transporte/HTTP NÃO são retriados aqui — retry é trabalho do orquestrador, nunca do worker. Um
erro de fetch vira `ErrorInfo` estruturado no `RunResult`, nunca explode o runtime.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Any, NamedTuple

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
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB — teto de bytes de FIO (iter_raw), mesmo valor do FeedWorker
_TIMEOUT = httpx.Timeout(15.0)
_PER_PAGE = 30  # uma página basta pra releases — upsert idempotente cobre re-coleta
_API_VERSION = "2022-11-28"

_REPO_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_valid_repo_shape(full_name: str) -> bool:
    """Shape de `owner/repo`: exatamente uma `/`, partes não-vazias dos dois lados, cada parte
    restrita ao whitelist `[A-Za-z0-9._-]` (sem query string vazando pra URL montada em
    `_fetch_releases`), sem `..` em nenhuma delas (whitelist sozinho não barra `..`, que é feito
    só de caracteres permitidos — path traversal continua exigindo a checagem explícita)."""
    owner, sep, repo = full_name.partition("/")
    if not sep or "/" in repo:
        return False
    if not _REPO_PART_RE.fullmatch(owner) or not _REPO_PART_RE.fullmatch(repo):
        return False
    return ".." not in owner and ".." not in repo


class GithubReleasesConfig(BaseModel):
    """Config declarada do worker `github-releases` v0.4.0: `repo` (owner/name) + `since`.

    Ambos vêm do Cadastro `github-repo` via o sweep (`build_config`): `repo` da canonical,
    `since` do `created_at`. RUNTIME/execução — não é catálogo."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    since: datetime

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, v: str) -> str:
        """`repo` fora do shape `owner/name` é rejeitado na CONSTRUÇÃO — o sweep o deriva da
        canonical do Cadastro, então shape inválido é bug de fiação, barrado na borda barata."""
        if not _is_valid_repo_shape(v):
            raise ValueError(f"repo deve ser 'owner/name' válido, veio {v!r}")
        return v

    @field_validator("since")
    @classmethod
    def _since_is_aware(cls, v: datetime) -> datetime:
        """`since` tz-naive é rejeitado na CONSTRUÇÃO — comparação com `published_at` (sempre
        tz-aware na API do GitHub) exige tz-aware dos dois lados, senão comparar levanta
        `TypeError` em runtime no meio do loop, não na borda onde o erro é barato de rastrear."""
        if v.tzinfo is None:
            raise ValueError("since deve ser tz-aware (naive é ambíguo frente a published_at)")
        return v


def _clean(text: object, cap: int) -> str:
    """Remove controle/formato/surrogate (mantém \\n e \\t) e capa o tamanho.

    Mesma disciplina de `feed.py._clean` — `body`/`name`/`tag_name` do release são markdown
    de terceiro não-confiável."""
    s = str(text)
    cleaned = "".join(ch for ch in s if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C")
    return cleaned[:cap]


class _FetchError(Exception):
    """Erro interno de fetch de releases — mensagem SEM corpo de resposta, status estruturado.

    `headers` carrega os headers da resposta HTTP (quando existe uma) para o chamador decidir
    `kind` distinguindo rate-limit de permission-denied em 403 (D55) — nunca o corpo, só os
    headers estruturais."""

    def __init__(
        self, message: str, status: int | None, headers: httpx.Headers | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers


def _is_rate_limit(status: int | None, headers: httpx.Headers | None) -> bool:
    """429 é sempre rate limit. 403 só é rate limit COM `x-ratelimit-remaining: 0` (sinal
    primário) ou `retry-after` presente (sinal secundário, abuse detection) — um 403 puro é
    permission-denied (ex.: PAT sem escopo) e nunca deve virar rate_limit, senão o operador
    espera uma janela que nunca vai abrir (D55)."""
    if status == 429:
        return True
    if status == 403 and headers is not None:
        return headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
    return False


def _classify_fetch_error(exc: _FetchError, repo: str) -> tuple[ErrorInfo, bool]:
    """Classifica um `_FetchError` do fetch de releases em `(ErrorInfo, is_rate_limit)`.

    Classificação por status/headers HTTP, nunca por casar texto de mensagem (D55): 429/403-com-
    sinal-de-rate-limit → `rate_limit`; o resto → `http`. `repo` sempre no `detail` estruturado."""
    is_rate_limit = _is_rate_limit(exc.status, exc.headers)
    kind = "rate_limit" if is_rate_limit else "http"
    detail: dict[str, Any] = {"repo": repo}
    if exc.status is not None:
        detail["status"] = exc.status
    return ErrorInfo(kind=kind, message=str(exc)[:500], detail=detail), is_rate_limit


def _headers(token: str) -> dict[str, str]:
    """Headers da API do GitHub — token no `Authorization`, nunca na URL.

    `Accept-Encoding: identity` é OBRIGATÓRIO (achado do smoke físico, sessão 0021 passo 4):
    o GitHub responde `Content-Encoding: gzip` por padrão, e `_stream_json_list` lê via
    `iter_raw()` (bytes de FIO, nunca decodificados, mesma disciplina de `feed.py._fetch`
    contra decompression bomb) — sem este header, `json.loads` no corpo ainda comprimido
    falha em TODO request real. Mockado, isso não aparecia: respx nunca comprime de verdade
    a menos que o teste construa o corpo comprimido à mão."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
        "Accept-Encoding": "identity",
    }


def _stream_json_list(
    client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Baixa `url` (GET) com o mesmo pré-check de `Content-Length` e o mesmo teto corrido de
    `iter_raw` do `FeedWorker` (achado do security-reviewer — nunca bufferizar uma resposta
    gigante inteira antes de cortar), parseia como JSON e valida que o topo é uma lista."""
    total = 0
    chunks: list[bytes] = []
    try:
        with client.stream("GET", url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            declared = resp.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > _MAX_BYTES:
                raise _FetchError("resposta da API excede o teto de bytes", None)
            for chunk in resp.iter_raw():
                total += len(chunk)
                if total > _MAX_BYTES:
                    raise _FetchError("resposta da API excede o teto de bytes", None)
                chunks.append(chunk)
    except httpx.HTTPStatusError as exc:
        raise _FetchError(
            f"GitHub respondeu HTTP {exc.response.status_code}",
            exc.response.status_code,
            exc.response.headers,
        ) from None
    except httpx.HTTPError as exc:
        raise _FetchError(f"falha de transporte ({type(exc).__name__})", None) from None
    try:
        data = json.loads(bytes(b"".join(chunks)))
    except ValueError:
        raise _FetchError("resposta da API não é JSON válido", None) from None
    if not isinstance(data, list):
        raise _FetchError("resposta da API não é uma lista", None)
    return [item for item in data if isinstance(item, dict)]


def _fetch_releases(base_url: str, token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """Busca uma página de releases via GET; sem retry (retry é do orquestrador).

    `follow_redirects=False` EXPLÍCITO (achado do security-reviewer): `base_url` vem fixo
    da integração resolvida (não é input hostil, ao contrário do `feed_url` de terceiro em
    `feed.py`) — não há redirect legítimo a seguir aqui, então desligar fecha por construção
    a classe de SSRF-via-redirect que `feed.py` precisa de um guard dedicado pra mitigar."""
    url = f"{base_url}/repos/{owner}/{repo}/releases"
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
        return _stream_json_list(client, url, _headers(token), {"per_page": _PER_PAGE})


def _html_url(value: Any) -> str | None:
    """`html_url` é estrutural (vem da API) — só aceito se for de fato uma string."""
    return value if isinstance(value, str) and value else None


def _parse_published_at(value: Any) -> datetime | None:
    """Parseia `published_at` (string ISO-8601 `Z`-suffixed do GitHub) em `datetime` tz-aware.

    `None`, chave ausente ou string não-parseável -> `None` (chamador decide skip+contagem,
    C5). Python 3.12 `datetime.fromisoformat` já entende o sufixo `Z` nativamente (verificado
    localmente: `datetime.fromisoformat("2026-06-01T00:00:00Z")` resolve sem erro)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _release_to_payload(
    release: dict[str, Any], source: SourcePayload, owner: str, repo: str, since: datetime
) -> tuple[ItemPayload | None, bool]:
    """Converte um release qualificado (`draft == false`, não-prerelease) em `ItemPayload`.

    Devolve `(None, False)` (skip, não erro) quando draft/prerelease ou sem `id` (sem chave
    de dedupe estável). Devolve `(None, True)` quando `published_at` está ausente/não-parseável
    (skip CONTADO em `skipped_no_date`, C5) OU quando a data parseia mas é anterior a `since`
    (filtro silencioso, não contado — já visto em run anterior)."""
    if release.get("draft") or release.get("prerelease"):
        return None, False
    release_id = release.get("id")
    if release_id is None:
        return None, False
    raw_published_at = release.get("published_at")
    published_at = _parse_published_at(raw_published_at)
    if published_at is None:
        return None, True
    if published_at < since:
        return None, False
    raw_title = release.get("name") or release.get("tag_name") or ""
    payload = ItemPayload(
        source=source,
        external_id=str(release_id),
        content=_clean(release.get("body") or "", _CONTENT_CAP),
        url=_html_url(release.get("html_url")),
        title=_clean(raw_title, _TITLE_CAP) if raw_title else None,
        # tag_name/published_at passam pela MESMA limpeza que content/title (achado do
        # security-reviewer): gravados crus, um surrogate solto sobreviveria até o encoder
        # CBOR estrito do SDK SurrealDB e abortaria a persistência do BATCH inteiro.
        metadata={
            "tag_name": _clean(release.get("tag_name") or "", _TITLE_CAP),
            "repo": f"{owner}/{repo}",
            "published_at": _clean(raw_published_at, _TITLE_CAP),
        },
    )
    return payload, False


class _RepoOutcome(NamedTuple):
    """Resultado de processar o repo (`_process_repo`): payloads convertidos, deltas de stats
    e o erro de fetch classificado (se houve falha) — `run()` decide o `RunResult`, sem
    reclassificar nada."""

    payloads: list[ItemPayload]
    releases_seen: int
    skipped_no_date: int
    error: ErrorInfo | None
    is_rate_limit: bool


def _process_repo(base_url: str, token: str, repo: str, since: datetime, log: Any) -> _RepoOutcome:
    """Processa o repo do Cadastro: busca releases (`_fetch_releases`) e, em caso de falha,
    classifica o erro (`_classify_fetch_error`) sem explodir — o erro vira `ErrorInfo`
    estruturado no `RunResult`. No caminho feliz, converte cada release qualificada em
    `ItemPayload` via `_release_to_payload`.

    A `SourcePayload` emitida tem `kind="github-repo"` (#110): casa a chave natural
    (kind, canonical) do Cadastro que dirigiu esta coleta, então `upsert_source` (lookup-first)
    reusa o MESMO record — o item aponta de volta pro Cadastro, sem criar um source paralelo.
    `title` é fallback: `upsert_source` faz coalesce (`title ?? $title`), então um título que o
    dono definiu na UI sobrevive."""
    owner, _, name = repo.partition("/")
    try:
        releases = _fetch_releases(base_url, token, owner, name)
    except _FetchError as exc:
        error, is_rate_limit = _classify_fetch_error(exc, repo)
        log.warning("github_releases_fetch_failed", repo=repo, kind=error.kind)
        return _RepoOutcome([], 0, 0, error, is_rate_limit)

    source = SourcePayload(
        kind="github-repo",
        canonical=f"https://github.com/{owner}/{name}",
        title=f"{owner}/{name} releases",
    )
    payloads: list[ItemPayload] = []
    releases_seen = 0
    skipped_no_date = 0
    for release in releases:
        releases_seen += 1
        payload, no_date = _release_to_payload(release, source, owner, name, since)
        if payload is not None:
            payloads.append(payload)
        elif no_date:
            skipped_no_date += 1
    return _RepoOutcome(payloads, releases_seen, skipped_no_date, None, False)


class GithubReleasesWorker:
    """Coleta releases publicadas (`draft == false`) de UM repo (o Cadastro `github-repo` que o
    sweep despachou), filtradas por `since` (#110). Não fala com a store: devolve `RunResult`,
    o runtime persiste."""

    manifest = WorkerManifest(
        name="github-releases",
        version="0.4.0",
        integrations=["github-readonly"],
        config=GithubReleasesConfig,
    )

    def run(self, ctx: RunContext) -> RunResult:
        """Busca releases do repo da config, filtrando por `since`/`published_at` (C1/C5).

        Token/base_url vêm da integração `github-readonly` resolvida pelo runtime (nunca de
        `os.environ`); ausente vira `ConfigError` (nunca skip silencioso). A coleta (fetch +
        classificação de erro + conversão em payload) é `_process_repo` — sem descoberta, sem
        loop de repos, sem deadline de run (um repo = um GET)."""
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

        outcome = _process_repo(base_url, token, config.repo, config.since, log)

        stats = Stats.model_validate(
            {
                "releases_seen": outcome.releases_seen,
                "items": len(outcome.payloads),
                "rate_limited": int(outcome.is_rate_limit),
                "skipped_no_date": outcome.skipped_no_date,
            }
        )
        log.info("github_releases_collected", repo=config.repo, items=len(outcome.payloads))
        return RunResult(payloads=list(outcome.payloads), stats=stats, error=outcome.error)
