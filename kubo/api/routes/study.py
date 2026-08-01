"""Rotas de Estudos (ADR-0047, KUBO-161/162): Tema container de N Materiais.

O Tema nasce vazio (`draft`) e o dono adiciona Materiais dentro dele. KUBO-161
entregou o esqueleto (criar/listar/rename). KUBO-162 adiciona upload de
Materiais (dropzone, parse, sumário síncrono), lista de Materiais na tela do
Tema, delete de Material e validação de limite `KUBO_TOPIC_MAX_MATERIALS`.

Rotas SÍNCRONAS (store bloqueante), leitura no molde de `entities.py` e escrita no
molde ADR-0018 de `settings.py`. Dado pessoal: toda rota resolve a sessão e opera
com `tenant_id`/`user_id` do contexto.

O arquivo enviado é gravado ANTES da escrita no banco (a store precisa do
caminho), então o caminho de erro remove o arquivo — material sem registro é
lixo invisível. O nome do arquivo no volume NUNCA vem do nome enviado pelo dono:
é uma chave hex gerada aqui.
"""

from __future__ import annotations

import os
import secrets
import string
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.routing import APIRoute
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.api.session import SessionContext, resolve_session
from kubo.errors import ConfigError, MaterialParseError, MembershipRequiredError, StoreError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.personas import resolve_persona
from kubo.store import client
from kubo.store import study as study_store
from kubo.study.parsing import MaterialFormat, ParsedMaterial, parse_material
from kubo.study.summarizer import Summarizer

_log = structlog.get_logger(__name__)

_NOT_A_MEMBER = "Estudos é pessoal: sua conta não pertence a este workspace."


class _MembershipAwareRoute(APIRoute):
    """Traduz `MembershipRequiredError` em 403 para TODA rota deste router.

    Um ponto de tradução em vez de N try/except — e no router do módulo, não em
    `app.add_exception_handler`, que é GLOBAL e mudaria as rotas que hoje deixam a
    exceção subir de propósito.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Envelopa o handler original com a recusa legível."""
        handler = super().get_route_handler()

        async def _translated(request: Request) -> Response:
            try:
                return await handler(request)
            except MembershipRequiredError:
                _log.warning("study.membership_required", path=_log_key(request.url.path))
                return PlainTextResponse(_NOT_A_MEMBER, status_code=403)

        return _translated


router = APIRouter(route_class=_MembershipAwareRoute)

_TOPICS_LIST_TEMPLATE = "study/topics.html"
_TOPIC_TEMPLATE = "study/topic.html"
_TOPIC_NOT_FOUND_TEMPLATE = "study/topic_not_found.html"
_TOPICS_ROUTE = "/study/topics"
_TOPIC_TABLE = "topic"

_DENIED = "Acesso negado."
_CSRF_INVALID = "CSRF inválido — recarregue a página."
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_EMPTY_TITLE = "O nome do estudo não pode ser vazio."
_BAD_FORMAT = "Formato não suportado: envie um arquivo .epub ou .pdf."
_TOPIC_NOT_DRAFT = "Só é possível adicionar materiais a um estudo em rascunho."
_OVER_LIMIT = "Limite de materiais por estudo atingido."
_STORE_FAILED = "Não foi possível registrar o material. Nada foi guardado — tente de novo."

_MAX_LOG_KEY = 64

# Extensão → formato do parser.
_FORMATS: dict[str, MaterialFormat] = {".epub": "epub", ".pdf": "pdf"}
_DEFAULT_MAX_MB = 50
_DEFAULT_MAX_MATERIALS = 5
_SAFE_KEY = string.ascii_letters + string.digits + "-_"
_SUMMARY_MODEL = "anthropic/claude-haiku-4-5"
_SUMMARY_MAX_TOKENS = 1024

# Rótulos de estado do Tema (ADR-0047 §3) — a lista e o detalhe usam os mesmos.
_STATE_LABELS: dict[str, str] = {
    "draft": "Rascunho",
    "planning": "Planejando",
    "scheduled": "Agendado",
    "running": "Em andamento",
    "archived": "Arquivado",
}


def _log_key(raw: str) -> str:
    """Chave da URL pronta para virar CAMPO DE LOG: aparada e truncada."""
    return raw.strip()[:_MAX_LOG_KEY]


def _session_of(request: Request) -> SessionContext | None:
    """Só a sessão — sem leitura de lista, que as escritas não usam."""
    with client.connect() as db:
        return resolve_session(request, db)


def _topic_of(db: Any, key: str, ctx: SessionContext) -> study_store.Topic | None:
    """Tema do usuário pela chave da URL; None quando não existe ou é de outro."""
    topic_key = key.strip()
    if not topic_key:
        return None
    return study_store.get_topic(
        db,
        topic_id=RecordID(_TOPIC_TABLE, topic_key),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )


def _topic_missing(request: Request, key: str) -> Response:
    """404 da tela do tema — tema de outro usuário é INEXISTENTE, não 'negado'."""
    return templates.TemplateResponse(
        request, _TOPIC_NOT_FOUND_TEMPLATE, {"raw": key}, status_code=404
    )


def _topic_url(topic_id: RecordID) -> str:
    """URL da tela do tema."""
    return f"{_TOPICS_ROUTE}/{topic_id.id}"


def _materials_dir() -> Path:
    """Diretório do volume de materiais (`KUBO_MATERIALS_DIR`); sem ele, não há escrita."""
    raw = os.environ.get("KUBO_MATERIALS_DIR", "").strip()
    if not raw:
        raise ConfigError("KUBO_MATERIALS_DIR não configurado")
    return Path(raw)


def _max_bytes() -> int:
    """Teto de upload em bytes, lido do env a cada request (`KUBO_MATERIAL_MAX_MB`)."""
    raw = os.environ.get("KUBO_MATERIAL_MAX_MB", "").strip()
    try:
        megabytes = int(raw) if raw else _DEFAULT_MAX_MB
    except ValueError:
        megabytes = _DEFAULT_MAX_MB
    return max(1, megabytes) * 1024 * 1024


def _max_materials() -> int:
    """Limite de materiais por Tema (`KUBO_TOPIC_MAX_MATERIALS`, default 5)."""
    raw = os.environ.get("KUBO_TOPIC_MAX_MATERIALS", "").strip()
    try:
        return max(0, int(raw) if raw else _DEFAULT_MAX_MATERIALS)
    except ValueError:
        return _DEFAULT_MAX_MATERIALS


def _safe_key(value: str) -> str:
    """Chave de record reduzida ao alfabeto seguro para virar componente de caminho."""
    cleaned = "".join(c for c in value if c in _SAFE_KEY)
    return cleaned or "sem-chave"


def _format_of(file: UploadFile | None) -> MaterialFormat | None:
    """Formato derivado da extensão do arquivo enviado; None se não for epub/pdf."""
    if file is None:
        return None
    return _FORMATS.get(Path(file.filename or "").suffix.lower())


def _save_upload(directory: Path, ctx: SessionContext, fmt: MaterialFormat, data: bytes) -> Path:
    """Grava o arquivo em `<dir>/<tenant>/<user>/<chave>.<fmt>` e devolve o caminho."""
    target_dir = directory / _safe_key(str(ctx.tenant_id.id)) / _safe_key(str(ctx.user_id.id))
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{secrets.token_hex(16)}.{fmt}"
    path.write_bytes(data)
    return path


def _summarizer(ctx: SessionContext) -> Summarizer:
    """Constrói o sumarizador com a persona `mentor` (modelo vem do catálogo, KUBO-162)."""
    with client.connect() as db:
        persona = resolve_persona(db, ctx.tenant_id, ctx.user_id, "mentor")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _SUMMARY_MODEL,
            max_tokens=_SUMMARY_MAX_TOKENS,
            timeout=30.0,
        ),
        max_attempts=1,
    )
    return Summarizer(executor=executor, prompt=persona.prompt)


def _validated_upload(
    file: UploadFile | None,
) -> tuple[MaterialFormat, bytes, ParsedMaterial] | None:
    """Valida extensão → tamanho → parse; devolve None se recusado (caller devolve 400)."""
    fmt = _format_of(file)
    if file is None or fmt is None:
        return None
    limit = _max_bytes()
    data = file.file.read(limit + 1)
    if len(data) > limit:
        return None
    try:
        parsed = parse_material(data, fmt)
    except MaterialParseError:
        return None
    return fmt, data, parsed


def _persist_material(
    ctx: SessionContext,
    topic: study_store.Topic,
    fmt: MaterialFormat,
    data: bytes,
    parsed: ParsedMaterial,
    summary: str | None,
    original: str,
) -> study_store.Material | None:
    """Grava arquivo + persiste material; None se a store falhar (arquivo é limpo)."""
    try:
        directory = _materials_dir()
    except ConfigError:
        return None
    path = _save_upload(directory, ctx, fmt, data)
    try:
        with client.connect_rw() as db:
            return study_store.create_material(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                title=parsed.title or original or "Material sem título",
                fmt=fmt,
                original_filename=original,
                file_path=str(path),
                size_bytes=len(data),
                chapters=parsed.chapters,
                summary=summary,
            )
    except (ConfigError, StoreError):
        path.unlink(missing_ok=True)
        return None
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.get("/topics")
def list_topics_page(request: Request) -> Response:
    """Lista os temas do usuário com nome e estado."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        topics = study_store.list_topics(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    rows = [{"topic": t, "state_label": _STATE_LABELS.get(t.state, t.state)} for t in topics]
    return templates.TemplateResponse(
        request, _TOPICS_LIST_TEMPLATE, {"rows": rows, "csrf": csrf_token(request)}
    )


@router.post("/topics")
def create_topic(
    request: Request,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Cria um Tema vazio em `draft` e redireciona pra tela do Tema."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    try:
        with client.connect_rw() as db:
            topic = study_store.create_topic(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                title="Estudo sem nome",
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.topic.store_failed")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_topic_url(topic.id), status_code=303)


@router.get("/topics/{key}")
def topic_detail(request: Request, key: str) -> Response:
    """Tela do tema: lista de Materiais + dropzone (draft) ou estado."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        topic = _topic_of(db, key, ctx)
        if topic is None:
            return _topic_missing(request, key)
        materials = study_store.list_materials_by_topic(
            db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic.id
        )
    return templates.TemplateResponse(
        request,
        _TOPIC_TEMPLATE,
        {
            "topic": topic,
            "state_label": _STATE_LABELS.get(topic.state, topic.state),
            "materials": materials,
            "max_materials": _max_materials(),
            "csrf": csrf_token(request),
        },
    )


@router.post("/topics/{key}/rename")
def rename_topic(
    request: Request,
    key: str,
    title: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Renomeia o tema inline (editável em estados não-arquivados)."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    title = title.strip()
    if not title:
        return PlainTextResponse(_EMPTY_TITLE, status_code=400)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    try:
        with client.connect_rw() as db:
            topic = _topic_of(db, key, ctx)
            if topic is None:
                return _topic_missing(request, key)
            study_store.set_topic_name(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                title=title,
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.topic.rename_failed", topic=_log_key(key))
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_topic_url(topic.id), status_code=303)


# --- Materiais dentro do Tema (KUBO-162) -------------------------------------------------


def _process_uploads(
    ctx: SessionContext,
    topic: study_store.Topic,
    uploads: list[UploadFile],
    limit: int,
    existing: int,
) -> int:
    """Processa cada arquivo do lote; devolve quantos Materiais foram criados.

    Arquivo inválido (formato/tamanho/parse) é pulado, não aborta o lote.
    Respeita o limite: para de criar quando atinge `limit - existing`.
    """
    summarizer = _summarizer(ctx)
    created = 0
    for upload in uploads:
        if existing + created >= limit:
            break
        validated = _validated_upload(upload)
        if validated is None:
            continue
        fmt, data, parsed = validated
        summary = summarizer.generate(parsed)
        original = Path(upload.filename or "").name if upload is not None else ""
        material = _persist_material(ctx, topic, fmt, data, parsed, summary, original)
        if material is None:
            _log.warning("study.material.store_failed", fmt=fmt)
            continue
        _log.info("study.material.uploaded", material=str(material.id))
        created += 1
    return created


@router.post("/topics/{key}/materials")
def upload_material(
    request: Request,
    key: str,
    file: Annotated[list[UploadFile] | None, File()] = None,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Recebe N epub/PDFs, extrai capítulos, gera sumário e persiste cada Material.

    Ordem: CSRF → sessão → tema (404) → draft (400) → limite (400) → formato
    (400) → tamanho → parse → sumário → grava arquivo → store. Autorizar depois
    de ler/parsear seria gastar memória a mando de quem nem passou no gate.

    Múltiplos arquivos viram múltiplos Materiais (AC#2): um request, um loop,
    um redirect. Arquivo inválido é pulado (não aborta o lote).
    """
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    with client.connect() as db:
        topic = _topic_of(db, key, ctx)
    if topic is None:
        return _topic_missing(request, key)
    if topic.state != "draft":
        return PlainTextResponse(_TOPIC_NOT_DRAFT, status_code=400)

    with client.connect() as db:
        count = study_store.count_materials_by_topic(
            db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic.id
        )
    limit = _max_materials()
    if count >= limit:
        return PlainTextResponse(_OVER_LIMIT, status_code=400)

    uploads = file or []
    if not uploads:
        return PlainTextResponse(_BAD_FORMAT, status_code=400)

    created = _process_uploads(ctx, topic, uploads, limit, count)
    if created == 0:
        return PlainTextResponse(_BAD_FORMAT, status_code=400)
    _log.info("study.material.batch_uploaded", count=created, topic=_log_key(key))
    return RedirectResponse(_topic_url(topic.id), status_code=303)


@router.post("/topics/{key}/materials/{mkey}/delete")
def delete_material(
    request: Request,
    key: str,
    mkey: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Deleta um Material: remove arquivo do volume + registro no banco."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    with client.connect() as db:
        topic = _topic_of(db, key, ctx)
    if topic is None:
        return _topic_missing(request, key)
    if topic.state != "draft":
        return PlainTextResponse(_TOPIC_NOT_DRAFT, status_code=400)
    material_id = RecordID("material", mkey.strip())
    try:
        with client.connect() as db:
            material = study_store.get_material(
                db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, material_id=material_id
            )
        if material is None:
            return PlainTextResponse("Material não encontrado.", status_code=404)
        with client.connect_rw() as db:
            study_store.delete_material(
                db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, material_id=material_id
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.material.delete_failed", material=_log_key(mkey))
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    # Remove o arquivo do volume (best-effort: registro já foi removido do banco).
    try:
        Path(material.file_path).unlink(missing_ok=True)
    except OSError:
        _log.warning("study.material.file_unlink_failed", path=material.file_path)
    _log.info("study.material.deleted", material=_log_key(mkey), topic=_log_key(key))
    return RedirectResponse(_topic_url(topic.id), status_code=303)
