"""Worker `github-releases` — coleta releases publicadas de repos assistidos (D51/D52/D54).

v0.2.0 (sessão 0021, marco 21.1): a config estática `repos: list[str]` da v0.1.0 (ADR-0009)
foi REMOVIDA. O worker agora DESCOBRE os repos dinamicamente a partir da watch list do
operador no GitHub (`GET /user/subscriptions`, paginado) em vez de depender de uma lista
mantida à mão em `flow_templates/` — "assistir" no GitHub (Watch, não Star; ver ADR-0021/D51
que fecha a distinção watch-vs-star) já é o sinal de intenção do operador, então duplicar essa
intenção numa config YAML seria uma segunda fonte de verdade fadada a ficar desatualizada.

Config agora só tem `since: datetime` (tz-aware obrigatório, C1): o corte temporal de
`published_at` que decide quais releases já vistas não voltam a virar item. `since` tz-naive
é rejeitado na CONSTRUÇÃO da config (nunca no run) — comparação com `published_at` do GitHub
(sempre UTC `Z`-suffixed) exige tz-aware dos dois lados, senão `datetime` levanta `TypeError`
em runtime no meio do loop.

Integração dedicada `github-watch` (D54): um PAT PRÓPRIO (`GITHUB_TOKEN_WATCH`), não mais o
`GITHUB_TOKEN_READONLY` por trás da antiga integração `github-releases` — o escopo necessário
pra ler `/user/subscriptions` (notificações do usuário autenticado) é diferente do escopo de
leitura pública de releases, e um PAT dedicado deixa esse escopo auditável e revogável
isoladamente (least-privilege, CLAUDE.md §Segurança). A MESMA integração cobre os dois
endpoints (`/user/subscriptions` e `/repos/{owner}/{repo}/releases`) — não há razão pra dois
tokens quando o mesmo PAT autentica ambos.

Paginação de `/user/subscriptions` (C4, achado do advisor — ponto de maior risco da sessão):
segue o header `Link: rel="next"` (RFC 5988) literalmente, com um teto duro de 10 páginas
(`_MAX_SUBSCRIPTION_PAGES`) contra um `Link` patológico/infinito — estourar o teto NÃO é erro,
só para de coletar mais páginas e segue com o que já tem.

Watch list vazia é ERRO, nunca run limpo (C3, CRITICAL): um PAT sem o escopo `notifications`
faz `/user/subscriptions` devolver `200 []` SEM nenhum sinal de erro do GitHub — silenciosamente
indistinguível de "operador não assiste nada". Tratar isso como run vazio normal esconderia uma
misconfiguração de token atrás de "nada de novo hoje" indefinidamente. Por isso: lista de repos
vazia após paginação -> `ErrorInfo(kind="config")` IMEDIATO, sem sequer chamar o endpoint de
releases. Já uma FALHA ao buscar `/user/subscriptions` (403/429/5xx) é outra coisa — não
"aprendi que não há watches", mas "não consegui nem perguntar" — e usa a MESMA classificação de
`kind` (`_is_rate_limit`) do fetch de releases por repo, nunca `kind="config"`.

Filtragem por `since`/`published_at` (C1/C5) é DUAS obrigações separadas: (1) só releases com
`published_at` parseável E `>= since` (boundary INCLUSIVO) viram item — sem data ou data
não-parseável é skip contado em `stats["skipped_no_date"]`, não erro; (2) o `published_at` CRU
(string exata do GitHub) é gravado em `ItemPayload.metadata["published_at"]` — nunca
reformatado a partir do `datetime` parseado, porque a metadata é log estrutural pro operador
auditar, não um valor de cálculo.

Um GET (uma página, `per_page=30`) por repo em `/repos/{owner}/{repo}/releases` — não pagina
releases (diferente de subscriptions): a idempotência do `upsert_item` (chave natural
`external_id`) já cobre re-coleta em runs futuros, uma página basta. Só `draft == false` vira
item; `prerelease == true` é PULADO (não é erro). `body`/`name`/`tag_name` do release são
MARKDOWN DE TERCEIRO NÃO-CONFIÁVEL (CLAUDE.md §Segurança: todo conteúdo coletado é hostil por
padrão) — limpos com a MESMA disciplina de `_clean` do `FeedWorker` antes de virar
`ItemPayload`. `url`/`external_id` são ESTRUTURAIS: vêm da resposta da API (`html_url`/`id`),
nunca de heurística do worker.

Rate limit (429 sempre; 403 só COM `x-ratelimit-remaining: 0` ou `retry-after` — um 403 puro é
permission-denied, ex.: PAT sem escopo, e NUNCA deve virar rate_limit, D55) e qualquer outro
erro de transporte/HTTP NÃO são retriados aqui — retry é trabalho do orquestrador, nunca do
worker. Um repo com erro não aborta os demais: o loop registra o `ErrorInfo` e segue para o
próximo repo, devolvendo os payloads já coletados (mesmo padrão de falha-parcial do
`FeedWorker`). Este worker trata VÁRIOS repos por run, então pode haver mais de um erro —
devolve-se o PRIMEIRO encontrado, e os stats (`rate_limited`) contam quantos repos bateram em
rate limit.

Prazo total do run (D51, não exercido por teste de wall-clock — flaky por natureza): um
`BlockingScheduler` job segura os demais jobs de cron enquanto roda, e até ~260 repos
sequenciais com timeout de 15s cada poderiam, sem teto, rodar por dezenas de minutos. Um
`_RUN_DEADLINE` de 5 minutos interrompe o loop principal (não o fetch em curso) e sinaliza
`kind="timeout"` se nenhum erro anterior já tiver sido registrado.
"""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import urlsplit

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
_SUBSCRIPTIONS_PER_PAGE = 100  # página maior pra watch list — menos GETs de paginação
_MAX_SUBSCRIPTION_PAGES = 10  # teto duro contra Link: rel="next" patológico/infinito (C4)
_API_VERSION = "2022-11-28"
# 5 min: um BlockingScheduler job segura os demais enquanto roda; sem teto, até ~260 repos
# sequenciais com timeout de 15s cada poderiam rodar por dezenas de minutos.
_RUN_DEADLINE = 300.0


class GithubReleasesConfig(BaseModel):
    """Config declarada do worker `github-releases` v0.2.0: só o corte temporal `since`.

    Repos NÃO fazem mais parte da config (D51) — vêm da watch list do operador no GitHub,
    descoberta em runtime via `/user/subscriptions`. RUNTIME/execução — não é catálogo."""

    model_config = ConfigDict(extra="forbid")

    since: datetime

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
    """Erro interno de fetch (releases OU subscriptions) — mensagem SEM corpo de resposta,
    status estruturado.

    `headers` carrega os headers da resposta HTTP (quando existe uma) para o chamador
    decidir `kind` distinguindo rate-limit de permission-denied em 403 (D55) — nunca o
    corpo, só os headers estruturais."""

    def __init__(
        self, message: str, status: int | None, headers: httpx.Headers | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers


def _is_rate_limit(status: int | None, headers: httpx.Headers | None) -> bool:
    """429 é sempre rate limit. 403 só é rate limit COM `x-ratelimit-remaining: 0`
    (sinal primário) ou `retry-after` presente (sinal secundário, abuse detection) —
    um 403 puro é permission-denied (ex.: PAT sem escopo) e nunca deve virar rate_limit,
    senão o operador espera uma janela que nunca vai abrir (D55)."""
    if status == 429:
        return True
    if status == 403 and headers is not None:
        return headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
    return False


def _classify_fetch_error(exc: _FetchError, repo: str | None = None) -> tuple[ErrorInfo, bool]:
    """Classifica um `_FetchError` em `(ErrorInfo, is_rate_limit)` — mesma lógica usada nos
    dois pontos de falha de `run()` (subscriptions e por-repo, D55), diferindo só em incluir
    `repo` no `detail` estruturado quando presente (fetch por-repo)."""
    is_rate_limit = _is_rate_limit(exc.status, exc.headers)
    kind = "rate_limit" if is_rate_limit else "http"
    detail: dict[str, Any] = {"repo": repo} if repo is not None else {}
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
    client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], httpx.Response]:
    """Streaming + teto de bytes compartilhado por releases e subscriptions: baixa `url`,
    aplica o mesmo pré-check de `Content-Length` e o mesmo teto corrido de `iter_raw` (achado
    do security-reviewer — nunca bufferizar uma resposta gigante inteira antes de cortar),
    parseia como JSON e valida que o topo é uma lista. Devolve a resposta (fechada, mas com
    `.headers`/`.links` ainda acessíveis) pro chamador ler `Link: rel="next"`."""
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
            # `.links` parseia o header `Link` (não o corpo) — acessível fora do `with`
            # mesmo com o stream já consumido via `iter_raw` acima.
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
    return [item for item in data if isinstance(item, dict)], resp


def _fetch_releases(base_url: str, token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """Busca uma página de releases via GET; sem retry (retry é do orquestrador).

    `follow_redirects=False` EXPLÍCITO (achado do security-reviewer): `base_url` vem fixo
    da integração resolvida (não é input hostil, ao contrário do `feed_url` de terceiro em
    `feed.py`) — não há redirect legítimo a seguir aqui, então desligar fecha por construção
    a classe de SSRF-via-redirect que `feed.py` precisa de um guard dedicado pra mitigar."""
    url = f"{base_url}/repos/{owner}/{repo}/releases"
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
        data, _resp = _stream_json_list(client, url, _headers(token), {"per_page": _PER_PAGE})
    return data


def _is_same_origin(url: str, base_url: str) -> bool:
    """`url` deve ter o MESMO esquema+host de `base_url` (https, mesmo netloc) — defesa em
    profundidade (achado convergente do security-reviewer e do CodeRabbit, PR #57): a URL de
    `Link: rel="next"` vem de um header de resposta, não de `base_url` (config confiável).
    Não é explorável no modelo de ameaça atual (resposta de 1ª parte do GitHub, TLS) mas o
    guard é barato e fecha por construção qualquer futuro onde isso deixe de ser verdade."""
    parsed = urlsplit(url)
    base = urlsplit(base_url)
    return parsed.scheme == "https" and parsed.netloc == base.netloc


def _fetch_subscriptions(base_url: str, token: str) -> list[dict[str, Any]]:
    """Busca a watch list do operador (`GET /user/subscriptions`), paginada via `Link:
    rel="next"` (RFC 5988) até esgotar as páginas OU até `_MAX_SUBSCRIPTION_PAGES` (C4).

    Segue a URL EXATA que o header `Link` do GitHub devolve pra próxima página, nunca
    re-derivando `?page=N` à mão — é a maneira correta de consumir paginação RFC 5988 e é
    literalmente o que os testes mockam. Estourar o teto de páginas NÃO é erro: só para de
    coletar mais e segue com o que já tem (guarda contra `Link` patológico/infinito)."""
    subscriptions: list[dict[str, Any]] = []
    url: str | None = f"{base_url}/user/subscriptions"
    params: dict[str, Any] | None = {"per_page": _SUBSCRIPTIONS_PER_PAGE}
    headers = _headers(token)
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
        for _page in range(_MAX_SUBSCRIPTION_PAGES):
            if url is None:
                break
            data, resp = _stream_json_list(client, url, headers, params)
            subscriptions.extend(data)
            next_url = resp.links.get("next", {}).get("url")
            if next_url is not None and not _is_same_origin(next_url, base_url):
                break  # Link fora da origem esperada -- para a paginação, sem erro
            url = next_url
            params = None  # a URL do Link já traz os query params completos
    return subscriptions


def _is_valid_repo_shape(full_name: str) -> bool:
    """Shape mínimo de `owner/repo`: exatamente uma `/`, partes não-vazias dos dois lados,
    sem `..` em nenhuma delas.

    `full_name` vem de `/user/subscriptions` — entrada externa hostil por padrão (CLAUDE.md
    §Segurança), não config do operador (por isso filtro em runtime, não validador pydantic
    de construção como o antigo `_owner_repo_shape` de v0.1.0, que validava `repos: list[str]`
    declarado à mão)."""
    owner, sep, repo = full_name.partition("/")
    if not sep or "/" in repo:
        return False
    if not owner or not repo:
        return False
    return ".." not in owner and ".." not in repo


def _discover_repos(base_url: str, token: str, log: Any) -> tuple[list[str], int, RunResult | None]:
    """Descobre a watch list do operador (`_fetch_subscriptions`) e resolve os dois casos
    de encerramento antecipado de `run()`: falha ao buscar subscriptions, e watch list
    vazia após paginar tudo (`ErrorInfo(kind="config")` IMEDIATO, C3 CRITICAL — nunca run
    limpo). Devolve `(repos, skipped_bad_repo_shape, None)` no caminho feliz, ou
    `([], skipped_bad_repo_shape, <RunResult>)` — o resultado que `run()` deve devolver
    imediatamente — quando a descoberta encerra o run cedo.

    `full_name` malformado (sem exatamente uma `/`, parte vazia, ou `..`) é filtrado ANTES
    de virar repo a processar — dado de API externa, não confiado por padrão — e contado
    em `skipped_bad_repo_shape`, nunca vira erro nem aborta a descoberta dos demais.

    Deduplica por ordem de primeira aparição (achado do CodeRabbit, PR #57): o mesmo repo
    pode aparecer em mais de uma página (overlap de paginação, ou o mesmo repo assistido por
    conta pessoal E organização) — sem isso, `repos_seen`/fetches de release dobrariam por
    repo repetido."""
    try:
        subscriptions = _fetch_subscriptions(base_url, token)
    except _FetchError as exc:
        error, is_rate_limit = _classify_fetch_error(exc)
        log.warning("github_subscriptions_fetch_failed", kind=error.kind)
        stats = Stats.model_validate(
            {
                "repos_seen": 0,
                "releases_seen": 0,
                "items": 0,
                "rate_limited": 1 if is_rate_limit else 0,
                "skipped_no_date": 0,
                "skipped_bad_repo_shape": 0,
            }
        )
        return [], 0, RunResult(payloads=[], stats=stats, error=error)

    repos: list[str] = []
    seen: set[str] = set()
    skipped_bad_repo_shape = 0
    for sub in subscriptions:
        full_name = sub.get("full_name")
        if not isinstance(full_name, str) or not full_name:
            continue
        if not _is_valid_repo_shape(full_name):
            skipped_bad_repo_shape += 1
            log.warning("github_watch_full_name_malformed", full_name=full_name)
            continue
        if full_name in seen:
            continue
        seen.add(full_name)
        repos.append(full_name)

    if not repos:
        log.warning("github_watch_list_empty")
        stats = Stats.model_validate(
            {
                "repos_seen": 0,
                "releases_seen": 0,
                "items": 0,
                "rate_limited": 0,
                "skipped_no_date": 0,
                "skipped_bad_repo_shape": skipped_bad_repo_shape,
            }
        )
        return (
            [],
            skipped_bad_repo_shape,
            RunResult(
                payloads=[],
                stats=stats,
                error=ErrorInfo(
                    kind="config",
                    message=(
                        "watch list vazia — token sem escopo 'notifications'? "
                        "nenhum repo assistido pelo operador?"
                    ),
                ),
            ),
        )

    return repos, skipped_bad_repo_shape, None


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
    """Resultado de processar um repo (`_process_repo`): payloads convertidos, deltas de
    stats do repo e o erro de fetch classificado (se houve falha) — `run()` decide se vira
    `first_error`, sem reclassificar nada."""

    payloads: list[ItemPayload]
    releases_seen: int
    skipped_no_date: int
    error: ErrorInfo | None
    is_rate_limit: bool


def _process_repo(base_url: str, token: str, repo: str, since: datetime, log: Any) -> _RepoOutcome:
    """Processa um repo da watch list: busca releases (`_fetch_releases`) e, em caso de
    falha, classifica o erro (`_classify_fetch_error`) sem abortar o run — um repo com erro
    não aborta os demais (mesmo padrão de falha-parcial do `FeedWorker`). No caminho feliz,
    converte cada release qualificada em `ItemPayload` via `_release_to_payload`."""
    owner, _, name = repo.partition("/")
    try:
        releases = _fetch_releases(base_url, token, owner, name)
    except _FetchError as exc:
        error, is_rate_limit = _classify_fetch_error(exc, repo=repo)
        log.warning("github_releases_fetch_failed", repo=repo, kind=error.kind)
        return _RepoOutcome([], 0, 0, error, is_rate_limit)

    source = SourcePayload(
        kind="github-releases",
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
    """Coleta releases publicadas (`draft == false`) dos repos da watch list do operador,
    descoberta dinamicamente via `/user/subscriptions` (D51), filtradas por `since` (C1). Não
    fala com a store: devolve `RunResult`, o runtime persiste."""

    manifest = WorkerManifest(
        name="github-releases",
        version="0.2.0",
        integrations=["github-watch"],
        config=GithubReleasesConfig,
    )

    def run(self, ctx: RunContext) -> RunResult:
        """Descobre os repos assistidos pelo operador e busca releases de cada um, filtrando
        por `since`/`published_at` (C1/C5).

        Token/base_url vêm da integração `github-watch` resolvida pelo runtime (nunca de
        `os.environ`); ausente vira `ConfigError` (nunca skip silencioso). Descoberta
        (watch list vazia -> `ErrorInfo(kind="config")` IMEDIATO, C3) é `_discover_repos`;
        coleta por repo (fetch+classificação de erro+conversão em payload) é `_process_repo`."""
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
        deadline = time.monotonic() + _RUN_DEADLINE

        repos, skipped_bad_repo_shape, early_result = _discover_repos(base_url, token, log)
        if early_result is not None:
            return early_result

        items: list[ItemPayload] = []
        first_error: ErrorInfo | None = None
        repos_seen = 0
        releases_seen = 0
        rate_limited = 0
        skipped_no_date = 0

        for repo in repos:
            if time.monotonic() > deadline:
                log.warning("github_releases_run_deadline_exceeded", repos_processed=repos_seen)
                if first_error is None:
                    first_error = ErrorInfo(
                        kind="timeout",
                        message="prazo total do run excedido, repos restantes não processados",
                    )
                break
            repos_seen += 1
            outcome = _process_repo(base_url, token, repo, config.since, log)
            items.extend(outcome.payloads)
            releases_seen += outcome.releases_seen
            skipped_no_date += outcome.skipped_no_date
            rate_limited += int(outcome.is_rate_limit)
            first_error = first_error or outcome.error  # primeiro erro vence (ErrorInfo é truthy)

        stats = Stats.model_validate(
            {
                "repos_seen": repos_seen,
                "releases_seen": releases_seen,
                "items": len(items),
                "rate_limited": rate_limited,
                "skipped_no_date": skipped_no_date,
                "skipped_bad_repo_shape": skipped_bad_repo_shape,
            }
        )
        log.info("github_releases_collected", repos_seen=repos_seen, items=len(items))
        return RunResult(payloads=list(items), stats=stats, error=first_error)
