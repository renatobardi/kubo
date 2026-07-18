"""Rota de Fontes (ADR-0014 UI): lista das fontes com os fatos da coleta (kind, última
coleta, itens acumulados, badge de recência E4) E — a partir do #105 (ADR-0025) — a ação
de ESCRITA "Adicionar fonte". A tela deixa de ser read-only e vira superfície de gestão.

A escrita segue o molde ADR-0018 (idêntico às ações de gate em `flows.py`): credencial
`kubo_rw` (EDITOR) por-request, CSRF (synchronizer token), validação de entrada por pydantic
na borda, fail-fast 503 sem a credencial. Duplicata (kind+canonical) é recusada pela STORE
(constraint, não checagem na view) e vira aviso SOFT. Rotas SÍNCRONAS (store bloqueante)."""

from __future__ import annotations

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
    StaleSourceError,
    format_validation_error,
)
from kubo.store import client, knowledge
from kubo.store.knowledge import SourceDetail, SourceStat

_log = structlog.get_logger(__name__)
_LIST_TEMPLATE = "sources/list.html"


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
    UNIQUE(kind, canonical) não pega (`/o/r/` vs forma canônica). github-repo → forma de-facto do
    worker (`_github_canonical`); rss → trim + validação estrutural (urlparse) exigindo esquema
    http(s) E host. Levanta ValueError na borda; a query no feed é permitida."""
    raw = raw.strip()
    if kind == "github-repo":
        return _github_canonical(raw)
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
        return PlainTextResponse("Escrita indisponível por erro de configuração.", status_code=503)


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
        return PlainTextResponse("Escrita indisponível por erro de configuração.", status_code=503)
