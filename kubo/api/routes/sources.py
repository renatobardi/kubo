"""Rota de Fontes (ADR-0014 UI): lista das fontes com os fatos da coleta (kind, última
coleta, itens acumulados, badge de recência E4) E — a partir do #105 (ADR-0025) — a ação
de ESCRITA "Adicionar fonte". A tela deixa de ser read-only e vira superfície de gestão.

A escrita segue o molde ADR-0018 (idêntico às ações de gate em `flows.py`): credencial
`kubo_rw` (EDITOR) por-request, CSRF (synchronizer token), validação de entrada por pydantic
na borda, fail-fast 503 sem a credencial. Duplicata (kind+canonical) é recusada pela STORE
(constraint, não checagem na view) e vira aviso SOFT. Rotas SÍNCRONAS (store bloqueante)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel, ValidationError, field_validator, model_validator
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.errors import (
    ConfigError,
    DuplicateSourceError,
    SourceHasHistoryError,
    StaleSourceError,
    format_validation_error,
)
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.personas import load_persona
from kubo.store import client, knowledge
from kubo.store.knowledge import SourceDetail, SourceStat
from kubo.workers import feed as feed_mod
from kubo.workers.feed import FeedPreview, preview_feed
from kubo.workers.finder import Finder

_log = structlog.get_logger(__name__)
_LIST_TEMPLATE = "sources/list.html"
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."


_FEED_SCHEMES = ("http", "https")


def _github_path(raw: str) -> str:
    """Extrai o path `owner/name` de uma entrada de repo, validando estruturalmente com
    `urlparse` (não por matching de string): entrada com esquema precisa apontar para o host
    `github.com` e não pode ter query nem fragment; a forma curta (`owner/name` ou
    `github.com/owner/name`) é aceita direto. Rejeita host não-GitHub, query/fragment e
    qualquer coisa fora de `owner/name` — senão `https://evil.com/o/r` viraria um repo 'válido'."""
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.query or parsed.fragment:
            raise ValueError("repositório do GitHub inválido: sem query nem fragment")
        if (parsed.hostname or "").removeprefix("www.").lower() != "github.com":
            raise ValueError("repositório do GitHub inválido: o host precisa ser github.com")
        return parsed.path
    return raw.removeprefix("www.").removeprefix("github.com")


def _github_canonical(raw: str) -> str:
    """Reduz uma entrada de repo (`owner/name`, `github.com/owner/name` ou URL completa) à
    forma de-facto que o worker `github_releases` já grava: `https://github.com/{owner}/{name}`
    (sem barra final nem `.git`). Sem essa normalização, `/o/r` e `/o/r/` virariam dois
    Cadastros que o índice UNIQUE(kind, canonical) não pega. Sem exatamente owner+name → erro."""
    path = _github_path(raw.strip()).strip("/").removesuffix(".git")
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        raise ValueError("repositório do GitHub inválido: use owner/name")
    return f"https://github.com/{parts[0]}/{parts[1]}"


def _normalize_canonical(kind: str, raw: str) -> str:
    """Normaliza a canonical conforme o kind — a MESMA regra no cadastro (#105) e na edição
    (#106), extraída para não divergir: divergência fabricaria quase-duplicatas que o índice
    UNIQUE(kind, canonical) não pega (`/o/r/` vs forma canônica).

    `github-repo` → forma de-facto do worker (`_github_canonical`); `rss` → trim + validação
    estrutural (urlparse) exigindo esquema http(s) E host (query no feed é permitida). Qualquer
    OUTRO kind (ex.: `youtube` legado, que a lista mostra e a edição não restringe) NÃO tem
    normalizador dedicado: a canonical passa CRUA (só trim), sem impor o regime de feed — senão
    editar só o título de uma fonte legada com canonical fora das regras de RSS falharia com 400
    (achado do CodeRabbit). O create nunca chega aqui com kind fora de rss/github-repo (Literal
    do NewSource); o passe-cru só é alcançável pela edição de uma fonte legada. Levanta ValueError
    só no regime rss."""
    raw = raw.strip()
    if kind == "github-repo":
        return _github_canonical(raw)
    if kind != "rss":
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in _FEED_SCHEMES:
        raise ValueError("URL de feed inválida: precisa usar esquema http ou https")
    if not parsed.netloc:
        raise ValueError("URL de feed inválida: falta o host")
    return raw


class NewSource(BaseModel):
    """Entrada validada do form "Adicionar fonte" (#105) — a fronteira pydantic (CLAUDE.md).

    A canonical é normalizada POR KIND antes de chegar à store: `github-repo` vira a forma
    de-facto do worker (`_github_canonical`); `rss` recebe só trim + exigência de esquema
    http(s) — canonicals legadas vieram cruas do `schedules.yaml`, e normalização agressiva
    (lowercase de host, strip de slash) criaria mismatch com records vivos (advisor). `title`
    em branco vira None (a coluna é opcional)."""

    kind: Literal["rss", "github-repo"]
    canonical: str
    title: str | None = None

    @field_validator("title", mode="after")
    @classmethod
    def _blank_title_is_none(cls, value: str | None) -> str | None:
        """Título só-espaços/vazio → None: não grava um título em branco."""
        stripped = (value or "").strip()
        return stripped or None

    @model_validator(mode="after")
    def _normalize(self) -> NewSource:
        """Normaliza a canonical conforme o kind, pela regra compartilhada com a edição."""
        self.canonical = _normalize_canonical(self.kind, self.canonical)
        return self


_MAX_TAGS = 20
_MAX_TAG_LEN = 40


class EditSource(BaseModel):
    """Entrada validada do form de edição (#106) — a fronteira pydantic. Diferente de `NewSource`,
    NÃO carrega `kind`: o kind seleciona o regime de normalização e não pode vir do form (um form
    adulterado com `kind=rss` num github-repo passaria canonical sem normalizar), então a rota o
    lê do banco. A canonical é normalizada na rota (precisa do kind do banco), não aqui.

    `tags` chega como texto separado por vírgula (um campo de form); vira lista limpa e com teto —
    input renderizável tem cap (ADR-0018 §VI, mesma regra do `reason`)."""

    title: str | None = None
    tags: list[str] = []
    canonical: str

    @field_validator("title", mode="after")
    @classmethod
    def _blank_title_is_none(cls, value: str | None) -> str | None:
        """Título só-espaços/vazio → None (simétrico ao create): não grava título em branco."""
        stripped = (value or "").strip()
        return stripped or None

    @field_validator("tags", mode="before")
    @classmethod
    def _split_tags(cls, value: object) -> object:
        """Aceita o texto do form (vírgula-separado) e o parte em lista antes da limpeza."""
        if isinstance(value, str):
            return value.split(",")
        return value

    @field_validator("tags", mode="after")
    @classmethod
    def _clean_and_cap_tags(cls, value: list[str]) -> list[str]:
        """Tira espaços, descarta vazias e impõe o teto (quantidade e tamanho) — na borda, 400."""
        cleaned = [t for t in (s.strip() for s in value) if t]
        if len(cleaned) > _MAX_TAGS:
            raise ValueError(f"máximo de {_MAX_TAGS} tags")
        if any(len(t) > _MAX_TAG_LEN for t in cleaned):
            raise ValueError(f"cada tag pode ter até {_MAX_TAG_LEN} caracteres")
        return cleaned

    @field_validator("canonical", mode="after")
    @classmethod
    def _canonical_present(cls, value: str) -> str:
        """Origem em branco é rejeitada na borda (a normalização por-kind vem depois, na rota)."""
        if not value.strip():
            raise ValueError("a origem é obrigatória")
        return value


class SourceTestForm(BaseModel):
    """Entrada validada do form "Testar" (KUBO-50): modo + valor digitado."""

    mode: Literal["feed", "site", "name"]
    value: str

    @field_validator("value", mode="after")
    @classmethod
    def _strip_and_require(cls, value: str) -> str:
        """Tira espaços e rejeita vazio — não importa o modo."""
        value = value.strip()
        if not value:
            raise ValueError("o valor é obrigatório")
        return value


_FINDER_PATH = Path(__file__).parents[3] / "catalogs" / "personas" / "finder.yaml"
_FINDER_INSTANCE: Finder | None = None


def get_finder() -> Finder:
    """Singleton lazy do finder: lê o YAML do catálogo e monta o ApiExecutor."""
    global _FINDER_INSTANCE
    if _FINDER_INSTANCE is None:
        persona = load_persona(_FINDER_PATH)
        executor = ApiExecutor(
            ApiExecutorConfig(
                model=persona.model or "groq/llama-3.3-70b-versatile",
                max_tokens=256,
                timeout=15.0,
            )
        )
        _FINDER_INSTANCE = Finder(executor=executor, prompt=persona.prompt)
    return _FINDER_INSTANCE


def _sort_key(s: SourceStat) -> tuple[int, str]:
    """Chave de ordenação (com reverse=True): fontes que já coletaram no topo (por último
    carimbo, mais recente primeiro), as sem coleta por último — a recência é o eixo da tela."""
    return (1 if s.last_collected_at else 0, s.last_collected_at or "")


def _render_list(
    request: Request,
    *,
    notice: str | None = None,
    status: int = 200,
    db: Any = None,
) -> Response:
    """Renderiza a lista de Fontes (com o csrf do form e um `notice` opcional). Reusa a
    conexão `db` quando já há uma aberta (re-render pós-escrita); senão abre uma leitura
    kubo_ro. Único ponto que monta o contexto da tela — GET e re-render pós-POST."""
    if db is None:
        with client.connect() as ro:
            sources = knowledge.sources_with_stats(ro)
    else:
        sources = knowledge.sources_with_stats(db)
    sources = sorted(sources, key=_sort_key, reverse=True)
    return templates.TemplateResponse(
        request,
        _LIST_TEMPLATE,
        {"sources": sources, "csrf": csrf_token(request), "notice": notice},
        status_code=status,
    )


router = APIRouter()


@router.get("")
def list_page(request: Request) -> Response:
    """Lista as fontes com contagem de itens e recência da coleta (E4) + o form de adicionar."""
    return _render_list(request)


@router.post("")
def create(
    request: Request,
    kind: Annotated[str, Form()] = "",
    canonical: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Cadastra uma fonte nova (RSS ou github-repo) — a escrita da UI (#105/ADR-0025).

    Molde ADR-0018: CSRF (403) → validação pydantic da entrada (400) → `connect_rw` (503 sem a
    credencial) → `create_source`. Sucesso = redirect 303 (PRG) à lista. Duplicata
    (kind+canonical, recusada pela store) reabre a lista com aviso SOFT (409), sem gravar."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    try:
        payload = NewSource(kind=kind, canonical=canonical, title=title)  # type: ignore[arg-type]
    except ValidationError as exc:
        return _render_list(request, notice=format_validation_error(exc), status=400)
    try:
        with client.connect_rw() as db:
            try:
                knowledge.create_source(
                    db, kind=payload.kind, canonical=payload.canonical, title=payload.title
                )
            except DuplicateSourceError:
                return _render_list(
                    request, notice="Essa fonte já está cadastrada.", status=409, db=db
                )
            return RedirectResponse("/sources", status_code=303)
    except ConfigError:
        _log.warning("sources.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


_EDIT_TEMPLATE = "sources/edit.html"
_STALE_NOTICE = "Essa fonte não está mais disponível para edição."


def _render_edit(
    request: Request, detail: SourceDetail, *, notice: str | None = None, status: int = 200
) -> Response:
    """Renderiza o form de edição a partir do Cadastro do BANCO (nunca do input submetido: mesma
    não-reflexão do create, fecha XSS refletido). `tags` viram texto vírgula-separado no campo."""
    return templates.TemplateResponse(
        request,
        _EDIT_TEMPLATE,
        {
            "source": detail,
            "tags_str": ", ".join(detail.tags),
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


@router.get("/{sid}/edit")
def edit_page(request: Request, sid: str) -> Response:
    """Form de edição de UMA fonte (#106): title/tags/canonical pré-preenchidos, kind read-only.
    Fonte inexistente ou arquivada (fora do estado editável) volta para a lista."""
    with client.connect() as ro:
        detail = knowledge.get_source(ro, RecordID("source", sid))
    if detail is None or detail.archived_at is not None:
        return RedirectResponse("/sources", status_code=303)
    return _render_edit(request, detail)


@router.post("/{sid}/edit")
def edit(
    request: Request,
    sid: str,
    title: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    canonical: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Edita title/tags/canonical de uma fonte (#106/ADR-0025), preservando id e histórico.

    Molde ADR-0018: CSRF (403) → validação pydantic (400) → pré-check de staleness em RO (409 se a
    fonte sumiu/arquivou) → normalização da canonical PELO KIND DO BANCO (400) → `connect_rw` (503)
    → `edit_source`. Sucesso = redirect 303 (PRG). Colisão (kind+canonical) reabre o form com aviso
    SOFT (409). O kind nunca vem do form."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    source_id = RecordID("source", sid)
    try:
        payload = EditSource(title=title, tags=tags, canonical=canonical)  # type: ignore[arg-type]
    except ValidationError as exc:
        # Erro de forma (raro): volta à lista com o aviso, como o create — sem tocar a store
        # para buscar o detalhe, e sem refletir o input submetido (format_validation_error só
        # lê loc+msg, nunca `input`).
        return _render_list(request, notice=format_validation_error(exc), status=400)
    with client.connect() as ro:
        detail = knowledge.get_source(ro, source_id)
    if detail is None or detail.archived_at is not None:
        return _render_list(request, notice=_STALE_NOTICE, status=409)
    return _apply_edit(request, source_id, detail, payload)


def _apply_edit(
    request: Request, source_id: RecordID, detail: SourceDetail, payload: EditSource
) -> Response:
    """Normaliza a canonical pelo kind do banco e grava (connect_rw). Mapeia cada falha ao seu
    status: normalização inválida (400), duplicata (409 soft), staleness sob corrida (409), sem
    credencial (503). Extraído do handler para manter a complexidade sob o teto (C901)."""
    try:
        canonical = _normalize_canonical(detail.kind, payload.canonical)
    except ValueError as exc:
        return _render_edit(request, detail, notice=str(exc), status=400)
    try:
        with client.connect_rw() as db:
            try:
                knowledge.edit_source(
                    db, id=source_id, title=payload.title, tags=payload.tags, canonical=canonical
                )
            except DuplicateSourceError:
                return _render_edit(
                    request, detail, notice="Já existe uma fonte com essa origem.", status=409
                )
            except StaleSourceError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse("/sources", status_code=303)
    except ConfigError:
        _log.warning("sources.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


# ── #107: ciclo de vida (pausar/arquivar/restaurar/apagar), molde ADR-0018 ───────────────

_DELETE_TEMPLATE = "sources/delete.html"


def _lifecycle_action(request: Request, csrf: str, action: Callable[[Any], None]) -> Response:
    """Executa uma ação de ciclo de vida (`action(db)`) no molde ADR-0018 comum a
    pausar/retomar/arquivar/restaurar: CSRF (403) → `connect_rw` (503 sem a credencial) → a
    escrita da store → redirect 303 (PRG). `StaleSourceError` (Cadastro saiu do estado da ação)
    reabre a lista com aviso SOFT (409). Extraído para não repetir o molde por rota (e manter a
    complexidade sob o teto C901): a única variação entre as quatro é o método da store chamado."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    try:
        with client.connect_rw() as db:
            try:
                action(db)
            except StaleSourceError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse("/sources", status_code=303)
    except ConfigError:
        _log.warning("sources.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


@router.post("/{sid}/disable")
def disable(request: Request, sid: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Pausa a coleta de uma fonte (`enabled=false`, ADR-0025 §8 emenda #107) — sai do sweep sem
    arquivar. Reversível por `enable`. Não destrutivo: um POST simples, sem dupla verificação."""
    rid = RecordID("source", sid)
    return _lifecycle_action(
        request, csrf, lambda db: knowledge.set_source_enabled(db, id=rid, enabled=False)
    )


@router.post("/{sid}/enable")
def enable(request: Request, sid: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Retoma a coleta de uma fonte pausada (`enabled=true`) — volta ao sweep."""
    rid = RecordID("source", sid)
    return _lifecycle_action(
        request, csrf, lambda db: knowledge.set_source_enabled(db, id=rid, enabled=True)
    )


@router.post("/{sid}/archive")
def archive(request: Request, sid: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Arquiva uma fonte (soft delete, ADR-0025 §8): tira da operação PRESERVANDO o histórico.
    Atômico na store (`enabled=false` + `archived_at`). Reversível por `restore`."""
    rid = RecordID("source", sid)
    return _lifecycle_action(request, csrf, lambda db: knowledge.archive_source(db, id=rid))


@router.post("/{sid}/restore")
def restore(request: Request, sid: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Restaura uma fonte arquivada (volta ao estado ATIVO, varrida pelo sweep de novo)."""
    rid = RecordID("source", sid)
    return _lifecycle_action(request, csrf, lambda db: knowledge.restore_source(db, id=rid))


def _render_delete(
    request: Request,
    detail: SourceDetail,
    items: int,
    *,
    notice: str | None = None,
    status: int = 200,
) -> Response:
    """Renderiza a tela de confirmação de apagar (dupla verificação pura-HTML, #107) a partir do
    Cadastro do BANCO — nunca do input (mesma não-reflexão do create/edit). `items` decide a tela:
    zero → oferece apagar de vez; >0 → explica o histórico e orienta a arquivar (US#11), sem
    convite a apagar. Também serve a recusa quando a corrida do item acontece (SourceHasHistory)."""
    return templates.TemplateResponse(
        request,
        _DELETE_TEMPLATE,
        {
            "source": detail,
            "items": items,
            "csrf": csrf_token(request),
            "notice": notice,
        },
        status_code=status,
    )


@router.get("/{sid}/delete")
def delete_page(request: Request, sid: str) -> Response:
    """Tela de confirmação de apagar (#107): a "dupla verificação" idiomática pura-HTML (mesmo
    precedente do gate de reject). Zero itens → confirma; com itens → orienta a arquivar. Fonte
    inexistente volta para a lista (não 500)."""
    rid = RecordID("source", sid)
    with client.connect() as ro:
        detail = knowledge.get_source(ro, rid)
        items = knowledge.source_item_count(ro, rid) if detail is not None else 0
    if detail is None:
        return RedirectResponse("/sources", status_code=303)
    return _render_delete(request, detail, items)


@router.post("/{sid}/delete")
def delete(request: Request, sid: str, csrf: Annotated[str, Form()] = "") -> Response:
    """Apaga de vez uma fonte com ZERO itens (hard delete, ADR-0025 §8) — o único caminho de
    delete da store. Molde ADR-0018: CSRF (403) → `connect_rw` (503) → `delete_source`. Sucesso =
    redirect 303 à lista. Fonte que ganhou itens na corrida (`SourceHasHistoryError`) reabre a tela
    de confirmação orientando a arquivar (409); fonte já sumida (`StaleSourceError`) volta à lista
    com aviso de staleness (409). O detalhe do re-render vem da MESMA conexão de escrita."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    rid = RecordID("source", sid)
    try:
        with client.connect_rw() as db:
            try:
                knowledge.delete_source(db, id=rid)
            except SourceHasHistoryError:
                detail = knowledge.get_source(db, rid)
                if detail is None:
                    return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
                items = knowledge.source_item_count(db, rid)
                return _render_delete(
                    request,
                    detail,
                    items,
                    notice="Essa fonte recebeu itens e não pode mais ser apagada — arquive.",
                    status=409,
                )
            except StaleSourceError:
                return _render_list(request, notice=_STALE_NOTICE, status=409, db=db)
            return RedirectResponse("/sources", status_code=303)
    except ConfigError:
        _log.warning("sources.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)


# ── KUBO-50: descoberta e validação assistida de feed RSS ────────────────────────────────

_TEST_TEMPLATE = "sources/test_result.html"


def _render_test_result(request: Request, ctx: dict[str, Any]) -> Response:
    """Renderiza o snippet HTML (HTMX) de sucesso/falha do teste de feed."""
    return templates.TemplateResponse(request, _TEST_TEMPLATE, ctx)


def _failure_ctx(label: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "steps": [{"label": label, "detail": detail}]}


def _failure_steps(steps: list[tuple[str, str]]) -> dict[str, Any]:
    return {"ok": False, "steps": [{"label": lbl, "detail": det} for lbl, det in steps]}


def _success_ctx(url: str, preview: FeedPreview, via: str | None) -> dict[str, Any]:
    return {
        "ok": True,
        "via": via,
        "discovered_url": url,
        "title": preview.title or "",
        "entries": preview.entries,
    }


def _test_feed_mode(request: Request, value: str) -> Response:
    """Modo (a): URL de feed direto — normaliza e busca uma amostra."""
    try:
        url = _normalize_canonical("rss", value)
        preview = preview_feed(url, trusted=False)
    except (ValueError, feed_mod.FetchError) as exc:
        return _render_test_result(request, _failure_ctx("URL do feed", str(exc)))
    return _render_test_result(request, _success_ctx(url, preview, via=None))


def _test_site_mode(request: Request, value: str) -> Response:
    """Modo (b): URL de site — autodiscovery por `<link rel="alternate">` no `<head>`."""
    try:
        base_url = _normalize_canonical("rss", value)
        html = feed_mod.fetch_page(base_url, trusted=False)
        feed_url = feed_mod.extract_feed_link(html, base_url)
        if not feed_url:
            return _render_test_result(
                request,
                _failure_ctx("Autodiscovery no site", 'sem <link rel="alternate"> no <head>'),
            )
        preview = preview_feed(feed_url, trusted=False)
    except (ValueError, feed_mod.FetchError) as exc:
        return _render_test_result(request, _failure_ctx("Autodiscovery no site", str(exc)))
    return _render_test_result(request, _success_ctx(feed_url, preview, via="autodiscovery"))


def _test_name_mode(request: Request, value: str) -> Response:
    """Modo (c): nome da empresa — chute da IA (finder) + fallback de autodiscovery."""
    guess = get_finder().guess(value)
    if not guess:
        return _render_test_result(
            request, _failure_ctx("IA (finder)", "não conseguiu chutar uma URL")
        )

    try:
        url = _normalize_canonical("rss", guess)
        preview = preview_feed(url, trusted=False)
        return _render_test_result(request, _success_ctx(url, preview, via="IA (finder)"))
    except (ValueError, feed_mod.FetchError):
        pass

    parsed = urlparse(guess)
    domain = parsed.netloc
    if not domain:
        return _render_test_result(
            request,
            _failure_steps(
                [
                    ("IA (finder)", f"{guess} não respondeu"),
                    ("Autodiscovery no domínio chutado", "domínio não identificado"),
                ]
            ),
        )

    base_url = f"https://{domain}"
    try:
        html = feed_mod.fetch_page(base_url, trusted=False)
        feed_url = feed_mod.extract_feed_link(html, base_url)
        if not feed_url:
            return _render_test_result(
                request,
                _failure_steps(
                    [
                        ("IA (finder)", f"{guess} não respondeu"),
                        (
                            "Autodiscovery no domínio chutado",
                            'sem <link rel="alternate"> no <head>',
                        ),
                    ]
                ),
            )
        preview = preview_feed(feed_url, trusted=False)
        return _render_test_result(request, _success_ctx(feed_url, preview, via="autodiscovery"))
    except (ValueError, feed_mod.FetchError) as exc:
        return _render_test_result(
            request,
            _failure_steps(
                [
                    ("IA (finder)", f"{guess} não respondeu"),
                    ("Autodiscovery no domínio chutado", str(exc)),
                ]
            ),
        )


@router.post("/test")
def test_source(
    request: Request,
    mode: Annotated[str, Form()] = "",
    canonical: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Testa/descobre um feed RSS sem persistir (dry-run). Retorna HTML parcial para HTMX.

    Modos: `feed` (URL direta), `site` (autodiscovery), `name` (finder + fallback).
    Falhas renderizam o snippet de erro; sucesso devolve a URL descoberta + amostra de entradas
    e atualiza os campos do form via hx-swap-oob."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse("CSRF inválido — recarregue a página.", status_code=403)
    try:
        payload = SourceTestForm.model_validate({"mode": mode, "value": canonical})
    except ValidationError as exc:
        return _render_test_result(
            request,
            _failure_ctx("Entrada inválida", format_validation_error(exc)),
        )
    if payload.mode == "feed":
        return _test_feed_mode(request, payload.value)
    if payload.mode == "site":
        return _test_site_mode(request, payload.value)
    return _test_name_mode(request, payload.value)
