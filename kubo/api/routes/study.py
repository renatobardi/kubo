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

import json
import os
import secrets
import string
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.routing import APIRoute
from sse_starlette.sse import EventSourceResponse
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.rendering import templates
from kubo.api.session import SessionContext, resolve_session
from kubo.errors import ConfigError, MaterialParseError, MembershipRequiredError, StoreError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.personas import resolve_persona
from kubo.store import client, tenancy
from kubo.store import study as study_store
from kubo.study.mentor import VALID_DEPTHS, Mentor, MentorReply, extract_reply
from kubo.study.parsing import MaterialFormat, ParsedMaterial, parse_material
from kubo.study.planner import Planner
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
_OVERSIZE = "Arquivo muito grande."
_PARSE_FAILED = "Não foi possível ler o arquivo: epub/PDF inválido ou corrompido."
_STORE_FAILED = "Não foi possível registrar o material. Nada foi guardado — tente de novo."
_TOPIC_NOT_DRAFT_CHAT = "Só é possível conversar com o mentor em um estudo em rascunho."
_TOPIC_NO_MATERIALS = "Adicione pelo menos um material antes de conversar com o mentor."
_EMPTY_MESSAGE = "A mensagem não pode ser vazia."
_INVALID_DEPTH = f"Profundidade inválida: use {', '.join(VALID_DEPTHS)}."
_MENTOR_MAX_TOKENS = 2048
_MENTOR_TIMEOUT = 60.0
_PLANNER_MAX_TOKENS = 4096
_PLANNER_TIMEOUT = 120.0

_MAX_LOG_KEY = 64

# Extensão → formato do parser.
_FORMATS: dict[str, MaterialFormat] = {".epub": "epub", ".pdf": "pdf"}
_DEFAULT_MAX_MB = 50
_DEFAULT_MAX_MATERIALS = 5
_SAFE_KEY = string.ascii_letters + string.digits + "-_"
_DEFAULT_MODEL = "anthropic/claude-haiku-4-5"
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
    """Constrói o sumarizador com a persona `summarizer` (modelo vem do catálogo)."""
    with client.connect() as db:
        persona = resolve_persona(db, ctx.tenant_id, ctx.user_id, "summarizer")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _DEFAULT_MODEL,
            max_tokens=_SUMMARY_MAX_TOKENS,
            timeout=30.0,
        ),
        max_attempts=1,
    )
    return Summarizer(executor=executor, prompt=persona.prompt)


class _UploadRejection(Exception):
    """Motivo tipado de rejeição de upload (formato/tamanho/parse)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _validated_upload(
    file: UploadFile | None,
) -> tuple[MaterialFormat, bytes, ParsedMaterial]:
    """Valida extensão → tamanho → parse; levanta `_UploadRejection` se recusado."""
    fmt = _format_of(file)
    if file is None or fmt is None:
        raise _UploadRejection(_BAD_FORMAT)
    limit = _max_bytes()
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise _UploadRejection(_OVERSIZE)
    try:
        parsed = parse_material(data, fmt)
    except MaterialParseError as exc:
        raise _UploadRejection(_PARSE_FAILED) from exc
    return fmt, data, parsed


def _persist_material(
    ctx: SessionContext,
    topic: study_store.Topic,
    fmt: MaterialFormat,
    data: bytes,
    parsed: ParsedMaterial,
    summary: str | None,
    original: str,
) -> study_store.Material:
    """Grava arquivo + persiste material; levanta ConfigError/StoreError se falhar."""
    directory = _materials_dir()  # levanta ConfigError se faltar config
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
        raise
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
        chat_messages = (
            study_store.list_chat_messages(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                phase="draft",
            )
            if topic.state == "draft"
            else []
        )
        plan, plan_entries = (
            study_store.get_plan_for_topic(
                db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic.id
            )
            if topic.state == "planning"
            else (None, [])
        )
    return templates.TemplateResponse(
        request,
        _TOPIC_TEMPLATE,
        {
            "topic": topic,
            "state_label": _STATE_LABELS.get(topic.state, topic.state),
            "materials": materials,
            "max_materials": _max_materials(),
            "chat_messages": chat_messages,
            "valid_depths": VALID_DEPTHS,
            "csrf": csrf_token(request),
            "plan": plan,
            "plan_entries": plan_entries,
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
        # StoreError aqui = tema arquivado (regra de negócio, não config).
        _log.warning("study.topic.rename_rejected", topic=_log_key(key))
        return PlainTextResponse("Não é possível renomear um estudo arquivado.", status_code=400)
    return RedirectResponse(_topic_url(topic.id), status_code=303)


# --- Materiais dentro do Tema (KUBO-162) -------------------------------------------------


def _process_uploads(
    ctx: SessionContext,
    topic: study_store.Topic,
    uploads: list[UploadFile],
    limit: int,
    existing: int,
) -> tuple[int, list[str]]:
    """Processa cada arquivo do lote; devolve (criados, rejeições).

    Arquivo inválido (formato/tamanho/parse) é pulado com motivo, não aborta o lote.
    Respeita o limite: para de criar quando atinge `limit - existing`.
    """
    summarizer = _summarizer(ctx)
    created = 0
    rejections: list[str] = []
    for upload in uploads:
        if existing + created >= limit:
            rejections.append(_OVER_LIMIT)
            break
        try:
            fmt, data, parsed = _validated_upload(upload)
        except _UploadRejection as exc:
            rejections.append(exc.message)
            continue
        summary = summarizer.generate(parsed)
        original = Path(upload.filename or "").name if upload is not None else ""
        try:
            material = _persist_material(ctx, topic, fmt, data, parsed, summary, original)
        except (ConfigError, StoreError):
            rejections.append(_STORE_FAILED)
            continue
        _log.info("study.material.uploaded", material=str(material.id))
        created += 1
    return created, rejections


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

    created, rejections = _process_uploads(ctx, topic, uploads, limit, count)
    if created == 0:
        # Todos falharam — mostra o primeiro motivo.
        msg = rejections[0] if rejections else _BAD_FORMAT
        return PlainTextResponse(msg, status_code=400)
    if rejections:
        _log.info(
            "study.material.batch_partial",
            created=created,
            rejected=len(rejections),
            topic=_log_key(key),
        )
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
        # Valida que o material pertence ao Tema da URL (não a outro Tema).
        if material.topic != topic.id:
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


# --- Chat com mentor (KUBO-163, ADR-0047 §6) --------------------------------------------


def _mentor(ctx: SessionContext) -> Mentor:
    """Constrói o mentor com a persona `mentor` (modelo vem do catálogo)."""
    with client.connect() as db:
        persona = resolve_persona(db, ctx.tenant_id, ctx.user_id, "mentor")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _DEFAULT_MODEL,
            max_tokens=_MENTOR_MAX_TOKENS,
            timeout=_MENTOR_TIMEOUT,
        ),
    )
    return Mentor(executor=executor, prompt=persona.prompt)


def _work_context_of(ctx: SessionContext) -> str:
    """Lê o work_context do perfil do usuário (consumido automaticamente pelo mentor)."""
    with client.connect() as db:
        profile = tenancy.get_user_profile(db, ctx.user_id)
    return (profile.work_context or "") if profile else ""


def _material_summaries_of(db: Any, ctx: SessionContext, topic_id: RecordID) -> list[str]:
    """Sumários dos Materiais do Tema (não conteúdo completo — contexto para o mentor)."""
    materials = study_store.list_materials_by_topic(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic_id
    )
    return [m.summary for m in materials if m.summary]


def _chat_history_of(db: Any, ctx: SessionContext, topic_id: RecordID) -> list[tuple[str, str]]:
    """Histórico da conversa como lista de (role, content) para o mentor."""
    messages = study_store.list_chat_messages(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic_id, phase="draft"
    )
    return [(m.role, m.content) for m in messages]


def _chat_precheck(
    request: Request, key: str, ctx: SessionContext
) -> Response | tuple[study_store.Topic, list[str], list[tuple[str, str]]]:
    """Valida tema/draft/materiais; devolve Response de erro OU contexto do chat.

    Gate é ≥1 Material (não ≥1 sumário): Material com sumário falho ainda
    libera o chat (ADR-0047 §5).
    """
    with client.connect() as db:
        topic = _topic_of(db, key, ctx)
        if topic is None:
            return _topic_missing(request, key)
        if topic.state != "draft":
            return PlainTextResponse(_TOPIC_NOT_DRAFT_CHAT, status_code=400)
        count = study_store.count_materials_by_topic(
            db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic.id
        )
        if count == 0:
            return PlainTextResponse(_TOPIC_NO_MATERIALS, status_code=400)
        summaries = _material_summaries_of(db, ctx, topic.id)
        history = _chat_history_of(db, ctx, topic.id)
    return topic, summaries, history


@router.post("/topics/{key}/chat")
def chat_with_mentor(
    request: Request,
    key: str,
    message: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Chat SSE com mentor: persiste mensagem do dono, streama resposta, persiste no fim.

    Ordem: CSRF → sessão → tema (404) → draft (400) → ≥1 Material (400).
    Streaming é síncrono: cada chunk vira um evento SSE `data`.
    Ao final, envia evento `done` com sugestões extraídas (nome, foco, profundidade).
    """
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    message = message.strip()
    if not message:
        return PlainTextResponse(_EMPTY_MESSAGE, status_code=400)

    precheck = _chat_precheck(request, key, ctx)
    if isinstance(precheck, Response):
        return precheck
    topic, summaries, history = precheck

    work_context = _work_context_of(ctx)
    mentor = _mentor(ctx)

    # Persiste a mensagem do dono antes de streamar.
    with client.connect_rw() as db:
        study_store.create_chat_message(
            db,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            topic_id=topic.id,
            phase="draft",
            role="user",
            content=message,
        )

    def _stream() -> Any:
        """Generator que envia chunks SSE e persiste a resposta ao final."""
        chunks: list[str] = []
        try:
            for chunk in mentor.stream_chat(
                user_message=message,
                material_summaries=summaries,
                history=history,
                work_context=work_context,
            ):
                chunks.append(chunk)
                yield {"event": "chunk", "data": chunk}
        except Exception:  # noqa: BLE001
            _log.warning("study.chat.stream_failed", topic=_log_key(key))
            yield {"event": "error", "data": "Falha ao gerar resposta."}
            return
        full = "".join(chunks)
        reply = extract_reply(full)
        # Persiste o texto LIMPO (sem marcações internas do protocolo).
        _persist_assistant(ctx, topic.id, reply.text, key)
        yield {"event": "done", "data": json.dumps(_done_data(reply))}

    return EventSourceResponse(_stream())


def _persist_assistant(ctx: SessionContext, topic_id: RecordID, content: str, key: str) -> None:
    """Persiste a resposta do mentor; falha de store é logada, não derruba o stream."""
    try:
        with client.connect_rw() as db:
            study_store.create_chat_message(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic_id,
                phase="draft",
                role="assistant",
                content=content,
            )
    except (ConfigError, StoreError):
        _log.warning("study.chat.assistant_persist_failed", topic=_log_key(key))


def _done_data(reply: MentorReply) -> dict[str, str | None]:
    """Monta o payload do evento SSE `done` com sugestões extraídas."""
    data: dict[str, str | None] = {"text": reply.text}
    if reply.suggested_name:
        data["suggested_name"] = reply.suggested_name
    if reply.suggested_focus:
        data["suggested_focus"] = reply.suggested_focus
    if reply.suggested_depth:
        data["suggested_depth"] = reply.suggested_depth
    return data


@router.post("/topics/{key}/fields")
def set_topic_fields(
    request: Request,
    key: str,
    field: Annotated[str, Form()] = "",
    value: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Atualiza um campo estruturado do Tema (focus ou depth) inferido pelo mentor.

    Atualização parcial: só o campo indicado por `field` é alterado. O outro
    campo preserva o valor existente (sentinela `_UNSET` na store).
    """
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    field = field.strip()
    value = value.strip()
    if field not in ("focus", "depth"):
        return PlainTextResponse("Campo inválido.", status_code=400)
    if field == "depth" and value and value not in VALID_DEPTHS:
        return PlainTextResponse(_INVALID_DEPTH, status_code=400)
    with client.connect() as db:
        topic = _topic_of(db, key, ctx)
        if topic is None:
            return _topic_missing(request, key)
        if topic.state != "draft":
            return PlainTextResponse(_TOPIC_NOT_DRAFT_CHAT, status_code=400)
    kwargs: dict[str, str | None] = {field: value or None}
    try:
        with client.connect_rw() as db:
            study_store.set_topic_fields(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                **kwargs,  # type: ignore[arg-type]
            )
    except (ConfigError, StoreError):
        _log.warning("study.fields.update_failed", topic=_log_key(key))
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_topic_url(topic.id), status_code=303)


# --- Fechar Tema + planner propõe Plano (KUBO-164, ADR-0047 §2) -------------------------


_TOPIC_NOT_DRAFT_CLOSE = "Só é possível fechar um estudo em rascunho."
_TOPIC_EMPTY_CLOSE = "Adicione pelo menos um material antes de fechar o estudo."


def _planner(ctx: SessionContext) -> Planner:
    """Constrói o planner com a persona `planner` (modelo vem do catálogo)."""
    with client.connect() as db:
        persona = resolve_persona(db, ctx.tenant_id, ctx.user_id, "planner")
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or _DEFAULT_MODEL,
            max_tokens=_PLANNER_MAX_TOKENS,
            timeout=_PLANNER_TIMEOUT,
        ),
    )
    return Planner(executor=executor, prompt=persona.prompt)


def _collect_all_chapters(
    db: Any, ctx: SessionContext, topic_id: RecordID
) -> list[study_store.MaterialChapter]:
    """Coleta todos os capítulos de todos os materiais do Tema, com seq GLOBAL.

    O planner precisa de seqs únicos globais (capítulos de materiais diferentes
    podem ter o mesmo seq local). A renumeração é 1-based na ordem de leitura:
    material por material (ordem de criação), seq crescente dentro de cada.
    """
    materials = study_store.list_materials_by_topic(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic_id
    )
    all_chapters: list[study_store.MaterialChapter] = []
    global_seq = 0
    for material in materials:
        chapters = study_store.list_all_chapters(
            db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, material_id=material.id
        )
        for ch in chapters:
            global_seq += 1
            all_chapters.append(
                study_store.MaterialChapter(
                    id=ch.id,
                    material=ch.material,
                    seq=global_seq,
                    title=ch.title,
                    part=ch.part,
                    content=ch.content,
                )
            )
    return all_chapters


def _mentor_transcript_of(db: Any, ctx: SessionContext, topic_id: RecordID) -> str:
    """Transcript cru da conversa com mentor (KUBO-164: transcript cru, resumo fica p/ KUBO-168)."""
    messages = study_store.list_chat_messages(
        db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic_id, phase="draft"
    )
    lines = [f"{'Dono' if m.role == 'user' else 'Mentor'}: {m.content}" for m in messages]
    return "\n".join(lines)


@router.post("/topics/{key}/close")
def close_topic(
    request: Request,
    key: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Fecha o Tema: draft → planning + planner propõe Plano automaticamente.

    Exige ≥1 Material. O planner recebe campos estruturados + transcript do
    mentor + sumários + estrutura de capítulos. Falha do LLM cai no
    `mechanical_proposal` (determinístico) — não trava a tela.
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
            return PlainTextResponse(_TOPIC_NOT_DRAFT_CLOSE, status_code=400)
        count = study_store.count_materials_by_topic(
            db, tenant_id=ctx.tenant_id, user_id=ctx.user_id, topic_id=topic.id
        )
        if count == 0:
            return PlainTextResponse(_TOPIC_EMPTY_CLOSE, status_code=400)
        # Coleta input do planner.
        chapters = _collect_all_chapters(db, ctx, topic.id)
        transcript = _mentor_transcript_of(db, ctx, topic.id)
        summaries = _material_summaries_of(db, ctx, topic.id)

    # Propõe o plano (LLM ou fallback mecânico).
    from kubo.study.planner import mechanical_proposal

    planner = _planner(ctx)
    proposal = planner.propose(
        chapters,
        focus=topic.focus,
        depth=topic.depth,
        mentor_transcript=transcript,
        material_summaries=summaries,
    )
    if proposal is None:
        proposal = mechanical_proposal(chapters)
        _log.info("study.close.mechanical_fallback", topic=_log_key(key))

    # Resolve chapter_seqs globais → RecordIDs.
    seq_to_id = {ch.seq: ch.id for ch in chapters}
    entries = [
        (lesson.title, [seq_to_id[seq] for seq in lesson.chapter_seqs])
        for lesson in proposal.lessons
    ]

    try:
        with client.connect_rw() as db:
            study_store.save_plan_proposal(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                entries=entries,
            )
            study_store.set_topic_state(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                topic_id=topic.id,
                state="planning",
            )
    except (ConfigError, StoreError):
        _log.warning("study.close.failed", topic=_log_key(key))
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    return RedirectResponse(_topic_url(topic.id), status_code=303)
