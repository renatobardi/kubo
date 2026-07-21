"""Worker `feed` (RSS/Atom) — M5.1 (ADR-0009).

Coleta um feed RSS/Atom por run (um feed por config, item VII do ADR). Todo
conteúdo coletado é hostil por padrão (CLAUDE.md §Segurança): o feed é buscado
com teto de bytes e timeout explícitos, o parse recebe BYTES CRUS (nunca a
URL — `feedparser` faria fetch próprio sem teto), e todo texto extraído passa
por `_clean` antes de virar `ItemPayload`.

SSRF em duas frentes: (1) o link de cada entry é armazenado como DADO, nunca
usado como destino de fetch — fechado por classe (nenhuma URL derivada do feed
chega a `_fetch`); (2) o servidor do feed pode responder um redirect 3xx para
um host interno (metadata da OCI `169.254.169.254`, RFC1918, loopback) — mitigado
por um event hook que valida esquema e IP resolvido a CADA hop (`_guard_request`).
Residual conhecido e aceito: DNS rebinding entre a validação do hook e a resolução
do httpx (fechar exigiria pinning de IP em transporte custom — fora da fase 1).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import ipaddress
import socket
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser
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
from kubo.errors import ContractError

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB — teto de bytes de FIO (iter_raw, sem descompressão)
_CONTENT_CAP = 65536  # ~64k chars de content por item (folgado p/ full-content feeds)
_TITLE_CAP = 500  # título é rótulo, não corpo
_TIMEOUT = httpx.Timeout(15.0)  # timeout POR-chunk do httpx (não é prazo total)
_TOTAL_DEADLINE = 60.0  # prazo TOTAL do fetch — fecha slowloris (chunk lento sob o timeout)
_DNS_TIMEOUT = 5.0  # teto de resolução DNS no guard (getaddrinfo não tem timeout nativo)
_MAX_REDIRECTS = 3  # segue poucos redirects (http->https, moved); não cadeia arbitrária


class FeedConfig(BaseModel):
    """Config declarada do worker `feed`: um feed_url por run (item VII)."""

    model_config = ConfigDict(extra="forbid")

    feed_url: str
    title: str | None = None
    tags: list[str] = []

    @field_validator("feed_url")
    @classmethod
    def _http_scheme_only(cls, v: str) -> str:
        """Rejeita esquema != http/https já na construção da config."""
        if urlsplit(v).scheme.lower() not in _ALLOWED_SCHEMES:
            raise ValueError("feed_url deve ser http:// ou https://")
        return v


def _clean(text: object, cap: int) -> str:
    """Remove controle/formato/surrogate (mantém \\n e \\t) e capa o tamanho."""
    s = str(text)
    cleaned = "".join(ch for ch in s if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C")
    return cleaned[:cap]


def _sha256(s: str) -> str:
    """Hash determinístico usado no fallback de identidade de entry."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _entry_content(entry: Any) -> str:
    """Prefere conteúdo completo (Atom `<content>`), cai para `summary`, senão vazio."""
    content_list = entry.get("content")
    if content_list:
        return str(content_list[0].get("value", ""))
    return str(entry.get("summary", "") or "")


def _entry_link(entry: Any) -> str | None:
    """Link da entry SOMENTE se http/https — é dado armazenado, nunca destino de fetch."""
    link = entry.get("link")
    if link and urlsplit(str(link)).scheme.lower() in _ALLOWED_SCHEMES:
        return str(link)
    return None


def _external_id(entry: Any) -> str | None:
    """Cadeia determinística de fallback de identidade — idempotência depende disto."""
    for key in ("id", "link"):
        val = entry.get(key)
        if val:
            return str(val)
    title = str(entry.get("title") or "")
    published = str(entry.get("published") or "")  # RAW, nunca published_parsed
    if title or published:
        return _sha256("\x1f".join([title, published]))
    content = _entry_content(entry)
    if content:
        return _sha256(content)
    return None


class _FetchError(Exception):
    """Erro interno de fetch — mensagem SEM corpo de resposta, detalhe estruturado."""

    def __init__(self, message: str, detail: dict[str, object]):
        super().__init__(message)
        self.detail = detail


def _reject_non_global_ip(host: str) -> None:
    """Levanta _FetchError se `host` resolve para QUALQUER IP não-global.

    `is_global` cobre num predicado só: loopback, link-local (inclui o metadata da
    OCI 169.254.169.254), privado (RFC1918), unique-local IPv6, shared 100.64/10 e
    reservado. IP literal é validado direto (sem DNS — determinístico). Para hostname,
    TODO endereço resolvido (A e AAAA) precisa ser global: um atacante pode devolver
    registros mistos."""
    if not host:
        raise _FetchError("destino sem host", {"reason": "host"})
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:  # não é IP literal — resolve o hostname (com teto de tempo)
        addrs = [ipaddress.ip_address(info[4][0]) for info in _resolve(host)]
    for addr in addrs:
        if not addr.is_global:
            raise _FetchError("destino não permitido (IP não-global)", {"reason": "ssrf"})


def _resolve(host: str) -> list[Any]:
    """Resolve `host` com TETO de tempo — `socket.getaddrinfo` não tem timeout nativo,
    e um DNS lento/malicioso num hop de redirect penduraria a thread do job antes de
    `_TOTAL_DEADLINE`. Roda num executor de 1 thread e o abandona (`shutdown(wait=False)`)
    se estourar: a thread pendurada não bloqueia o worker e some quando o resolver do SO
    desiste."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(socket.getaddrinfo, host, None).result(timeout=_DNS_TIMEOUT)
    except concurrent.futures.TimeoutError as exc:
        raise _FetchError("resolução DNS excedeu o teto", {"reason": "dns_timeout"}) from exc
    except socket.gaierror as exc:
        raise _FetchError("host do feed não resolve", {"reason": "dns"}) from exc
    finally:
        executor.shutdown(wait=False)


def _make_request_guard(*, trusted: bool) -> Callable[[httpx.Request], None]:
    """Cria o event hook do httpx (um por `_fetch`, com estado próprio).

    O hook dispara a CADA request, inclusive em cada hop de redirect. Modelo de
    confiança: a URL INICIAL é confiável apenas quando vem de `config.feed_url`
    (schedules.yaml, config do dono) — `trusted=True` isenta o IP-check e permite
    testar contra loopback; quando `trusted=False` (formulário, autodiscovery ou chute
    de IA), a URL inicial passa pelo mesmo guard de IP-não-global dos redirects. Os
    DESTINOS DE REDIRECT são sempre controlados pelo servidor remoto e nunca isentos.
    Fecha o SSRF de redirect (feed 3xx -> host interno) e de URL inicial hostil.
    Levanta _FetchError (não httpx.HTTPError) — propaga limpo."""
    state = {"first": True}

    def _guard(request: httpx.Request) -> None:
        if request.url.scheme not in _ALLOWED_SCHEMES:
            raise _FetchError("esquema de URL não permitido", {"reason": "scheme"})
        if state["first"]:
            state["first"] = False
            if not trusted:
                _reject_non_global_ip(request.url.host)
        else:
            _reject_non_global_ip(request.url.host)  # hop de redirect: destino não-confiável

    return _guard


def _fetch(url: str, *, trusted: bool = False) -> bytes:
    """Busca o feed com httpx SÍNCRONO; feedparser recebe os BYTES CRUS (nunca a URL).

    Segurança do fetch: `Accept-Encoding: identity` + contagem em `iter_raw()` (bytes de
    FIO) fecham a decompression bomb (o decoder do httpx descomprimiria um chunk sem teto
    antes de o cap agir). `_TOTAL_DEADLINE` fecha slowloris (o timeout do httpx é só por
    chunk). `_guard_request` valida esquema+IP a cada hop de redirect (SSRF) e, quando
    `trusted=False`, também valida a URL inicial."""
    total = 0
    chunks: list[bytes] = []
    deadline = time.monotonic() + _TOTAL_DEADLINE
    try:
        with httpx.Client(
            timeout=_TIMEOUT,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            event_hooks={"request": [_make_request_guard(trusted=trusted)]},
        ) as client:
            with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > _MAX_BYTES:
                    raise _FetchError("feed excede o teto de bytes", {"cap": _MAX_BYTES})
                for chunk in resp.iter_raw():
                    if time.monotonic() > deadline:
                        raise _FetchError(
                            "fetch excedeu o prazo total", {"deadline": _TOTAL_DEADLINE}
                        )
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise _FetchError("feed excede o teto de bytes", {"cap": _MAX_BYTES})
                    chunks.append(chunk)
    except httpx.HTTPStatusError as exc:
        raise _FetchError(
            "falha HTTP ao buscar o feed", {"status": exc.response.status_code}
        ) from exc
    except httpx.HTTPError as exc:
        raise _FetchError(
            "falha de transporte ao buscar o feed", {"error": type(exc).__name__}
        ) from exc
    return b"".join(chunks)


FetchError = _FetchError  # alias público para call sites fora do worker


def fetch_page(url: str, *, trusted: bool = False) -> bytes:
    """Busca bytes crus de uma URL com o mesmo hardening do FeedWorker.

    Expõe o fetch isolado para reuso (ex.: autodiscovery de site HTML).
    `trusted=False` é o default seguro para URLs vindas de input/browser/IA."""
    return _fetch(url, trusted=trusted)


def fetch_and_parse(url: str, *, trusted: bool = False) -> Any:
    """Busca e parseia um feed RSS/Atom — mesmo caminho do FeedWorker."""
    raw = fetch_page(url, trusted=trusted)
    return feedparser.parse(raw)


@dataclass
class FeedPreview:
    """Amostra enxuta de um feed (título + até N entradas) para preview na UI."""

    title: str | None = None
    entries: list[dict[str, str]] = field(default_factory=list)


def _entry_preview(entry: Any) -> dict[str, str]:
    """Extrai título e link seguro de uma entry para preview."""
    return {
        "title": _clean(str(entry.get("title") or ""), _TITLE_CAP),
        "url": _entry_link(entry) or "",
    }


def preview_feed(url: str, *, trusted: bool = False, max_entries: int = 3) -> FeedPreview:
    """Busca, parseia e devolve uma amostra do feed sem persistir nada."""
    parsed = fetch_and_parse(url, trusted=trusted)
    if parsed.bozo and not parsed.entries:
        raise _FetchError("feed malformado sem entries parseáveis", {"reason": "parse"})
    return FeedPreview(
        title=_clean(str(parsed.feed.get("title") or ""), _TITLE_CAP) or None,
        entries=[_entry_preview(entry) for entry in parsed.entries[:max_entries]],
    )


class _FeedLinkParser(HTMLParser):
    """Extrai `<link rel="alternate" type="application/rss+xml|atom+xml" href="...">`
    do `<head>` de uma página HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.feed_url: str | None = None
        self._in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "head":
            self._in_head = True
            return
        if not self._in_head or self.feed_url:
            return
        if t == "link":
            attr = {k: (v or "") for k, v in attrs}
            if attr.get("rel", "").lower() == "alternate" and attr.get("type", "").lower() in {
                "application/rss+xml",
                "application/atom+xml",
            }:
                href = attr.get("href", "")
                if href:
                    self.feed_url = href

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "head":
            self._in_head = False


def extract_feed_link(html: bytes | str, base_url: str) -> str | None:
    """Parse determinístico de `<link rel="alternate">` no `<head>` de uma página HTML.

    Resolve URLs relativas contra `base_url`. Não usa LLM nem dependência nova."""
    text = html if isinstance(html, str) else html.decode("utf-8", errors="replace")
    parser = _FeedLinkParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 — HTML malformado não quebra a rota
        return None
    if not parser.feed_url:
        return None
    return urljoin(base_url, parser.feed_url)


def _entry_to_payload(
    entry: Any, source: SourcePayload, metadata: dict[str, Any] | None
) -> ItemPayload | None:
    """Converte uma entry em `ItemPayload`, ou None se não houver identidade (skip)."""
    external_id = _external_id(entry)
    if external_id is None:
        return None
    raw_title = entry.get("title")
    return ItemPayload(
        source=source,
        external_id=external_id,
        content=_clean(_entry_content(entry), _CONTENT_CAP),
        url=_entry_link(entry),
        title=_clean(raw_title, _TITLE_CAP) if raw_title else None,
        metadata=metadata,
    )


class FeedWorker:
    """Coleta um feed RSS/Atom sob contrato (ADR-0009). Um feed por run (item VII).
    NUNCA faz fetch de URL vinda de dentro do feed (link de item é dado, não destino —
    SSRF fechado por classe); o vetor de redirect para host interno é mitigado por
    validação de IP por hop em `_fetch` (residual de rebinding documentado lá). Não toca
    a store: devolve RunResult, o runtime persiste."""

    manifest = WorkerManifest(name="feed", version="0.1.0", integrations=["rss"], config=FeedConfig)

    def run(self, ctx: RunContext) -> RunResult:
        """Busca, parseia e converte o feed configurado em `RunResult`."""
        config = ctx.config
        if not isinstance(config, FeedConfig):  # narrowing (ADR-0009 item II)
            raise ContractError(
                f"FeedWorker recebeu config do tipo {type(config).__name__}, esperava FeedConfig"
            )
        # canonical da source é config do dono (schedules.yaml), logável (item VIII) —
        # dá rastro de QUAL feed a run coletou sem logar nenhum payload coletado.
        log = ctx.logger.bind(source=config.feed_url)
        stats: dict[str, int] = {
            "entries_seen": 0,
            "items": 0,
            "bozo": 0,
            "skipped_no_identity": 0,
        }

        try:
            parsed = fetch_and_parse(config.feed_url, trusted=True)
        except _FetchError as exc:
            log.warning("feed_fetch_failed")  # sem payload, sem detalhe sensível
            return RunResult(
                stats=Stats.model_validate(stats),
                error=ErrorInfo(kind="http", message=str(exc)[:500], detail=exc.detail),
            )

        if parsed.bozo:
            stats["bozo"] = 1
        entries = parsed.entries
        if not entries:
            if parsed.bozo:
                return RunResult(
                    stats=Stats.model_validate(stats),
                    error=ErrorInfo(kind="parse", message="feed malformado sem entries parseáveis"),
                )
            return RunResult(stats=Stats.model_validate(stats))  # bem-formado e vazio: ok

        source = SourcePayload(kind="rss", canonical=config.feed_url, title=config.title)
        metadata = {"tags": config.tags} if config.tags else None
        items: list[ItemPayload] = []
        for entry in entries:
            stats["entries_seen"] += 1
            payload = _entry_to_payload(entry, source, metadata)
            if payload is None:
                stats["skipped_no_identity"] += 1
                continue
            items.append(payload)
        stats["items"] = len(items)
        log.info("feed_collected", entries_seen=stats["entries_seen"], items=stats["items"])
        return RunResult(payloads=list(items), stats=Stats.model_validate(stats))
