"""Rota de Fontes (ADR-0014 UI): lista das fontes com os fatos da coleta (kind, última
coleta, itens acumulados, badge de recência E4) E — a partir do #105 (ADR-0025) — a ação
de ESCRITA "Adicionar fonte". A tela deixa de ser read-only e vira superfície de gestão.

A escrita segue o molde ADR-0018 (idêntico às ações de gate em `flows.py`): credencial
`kubo_rw` (EDITOR) por-request, CSRF (synchronizer token), validação de entrada por pydantic
na borda, fail-fast 503 sem a credencial. Duplicata (kind+canonical) é recusada pela STORE
(constraint, não checagem na view) e vira aviso SOFT. Rotas SÍNCRONAS (store bloqueante)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Form, Request
from pydantic import BaseModel, ValidationError, field_validator, model_validator
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.errors import ConfigError, DuplicateSourceError, format_validation_error
from kubo.store import client, knowledge
from kubo.store.knowledge import SourceStat

_log = structlog.get_logger(__name__)
_LIST_TEMPLATE = "sources/list.html"


_FEED_SCHEMES = ("http", "https")


def _scheme(url: str) -> str:
    """Esquema de uma URL (parte antes de `://`), em minúsculas; `""` se não houver `://`."""
    return url.split("://", 1)[0].lower() if "://" in url else ""


def _github_canonical(raw: str) -> str:
    """Reduz uma entrada de repo (`owner/name`, `github.com/owner/name` ou URL completa) à
    forma de-facto que o worker `github_releases` já grava: `https://github.com/{owner}/{name}`
    (sem barra final nem `.git`). Sem essa normalização, `/o/r` e `/o/r/` virariam dois
    Cadastros que o índice UNIQUE(kind, canonical) não pega. Sem exatamente owner+name → erro."""
    s = raw.removesuffix("/")
    if "://" in s:
        s = s.split("://", 1)[1]  # descarta o esquema (qualquer que seja), depois o host github
    s = s.removeprefix("www.").removeprefix("github.com/").strip("/").removesuffix(".git")
    parts = [p for p in s.split("/") if p]
    if len(parts) != 2:
        raise ValueError("repositório do GitHub inválido: use owner/name")
    return f"https://github.com/{parts[0]}/{parts[1]}"


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
    def _normalize_canonical(self) -> NewSource:
        """Normaliza a canonical conforme o kind (github-repo → forma do worker; rss → trim)."""
        raw = self.canonical.strip()
        if self.kind == "github-repo":
            self.canonical = _github_canonical(raw)
        elif _scheme(raw) in _FEED_SCHEMES:
            self.canonical = raw
        else:
            raise ValueError("URL de feed inválida: precisa usar esquema http ou https")
        return self


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
