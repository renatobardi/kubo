"""Rotas de Estudos (ADR-0043, KUBO-135): ingestão e conferência de Material.

Rotas SÍNCRONAS (store bloqueante), leitura no molde de `entities.py` e escrita no
molde ADR-0018 de `settings.py`. Material é dado pessoal: toda rota resolve a sessão
e opera com `tenant_id`/`user_id` do contexto.

O arquivo enviado é gravado ANTES da escrita no banco (a store precisa do caminho),
então o caminho de erro remove o arquivo — material sem registro é lixo invisível.
O nome do arquivo no volume NUNCA vem do nome enviado pelo dono: é uma chave hex
gerada aqui (`../../etc/passwd.epub` não escolhe onde grava).
"""

from __future__ import annotations

import os
import secrets
import string
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.api.session import SessionContext, resolve_session
from kubo.errors import ConfigError, MaterialParseError, StoreError
from kubo.executors.api import ApiExecutor, ApiExecutorConfig
from kubo.runtime.catalog_defaults import DEFAULT_PERSONAS
from kubo.runtime.personas import Persona, load_personas
from kubo.store import client
from kubo.store import study as study_store
from kubo.study.parsing import MaterialFormat, ParsedMaterial, parse_material
from kubo.study.planner import Planner, PlanProposal, mechanical_proposal
from kubo.study.planning import compute_target_date

_log = structlog.get_logger(__name__)
router = APIRouter()

_LIST_TEMPLATE = "study/list.html"
_DETAIL_TEMPLATE = "study/detail.html"
_NOT_FOUND_TEMPLATE = "study/not_found.html"
_MATERIALS_ROUTE = "/study/materials"
_MATERIAL_TABLE = "material"

_DENIED = "Acesso negado."
_CSRF_INVALID = "CSRF inválido — recarregue a página."
_WRITE_UNAVAILABLE = "Escrita indisponível por erro de configuração."
_STORE_FAILED = "Não foi possível registrar o material. Nada foi guardado — tente de novo."
_BAD_FORMAT = "Formato não suportado: envie um arquivo .epub ou .pdf."

# Extensão → formato do parser. A extensão só ESCOLHE o parser; quem valida o
# conteúdo é `parse_material` (arquivo renomeado falha no parse, não passa).
_FORMATS: dict[str, MaterialFormat] = {".epub": "epub", ".pdf": "pdf"}
_DEFAULT_MAX_MB = 50
# Caracteres aceitos numa chave de record ao virar componente de caminho.
_SAFE_KEY = string.ascii_letters + string.digits + "-_"


def _max_bytes() -> int:
    """Teto de upload em bytes, lido do env A CADA REQUEST (`KUBO_MATERIAL_MAX_MB`).

    Ler no import congelaria o limite no boot do processo — mudar o teto exigiria
    redeploy.
    """
    raw = os.environ.get("KUBO_MATERIAL_MAX_MB", "").strip()
    try:
        megabytes = int(raw) if raw else _DEFAULT_MAX_MB
    except ValueError:
        megabytes = _DEFAULT_MAX_MB
    return max(1, megabytes) * 1024 * 1024


def _materials_dir() -> Path:
    """Diretório do volume de materiais (`KUBO_MATERIALS_DIR`); sem ele, não há escrita."""
    raw = os.environ.get("KUBO_MATERIALS_DIR", "").strip()
    if not raw:
        raise ConfigError("KUBO_MATERIALS_DIR não configurado")
    return Path(raw)


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


def _render_list(
    request: Request,
    materials: list[study_store.Material],
    *,
    notice: str | None = None,
    status: int = 200,
) -> Response:
    """Renderiza a lista de materiais com o formulário de envio."""
    return templates.TemplateResponse(
        request,
        _LIST_TEMPLATE,
        {"materials": materials, "csrf": csrf_token(request), "notice": notice},
        status_code=status,
    )


def _session_of(request: Request) -> SessionContext | None:
    """Só a sessão — sem a leitura da lista, que o upload não usa."""
    with client.connect() as db:
        return resolve_session(request, db)


def _list_for(request: Request) -> tuple[SessionContext | None, list[study_store.Material]]:
    """Sessão + materiais do usuário; lista vazia quando não há sessão válida."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return None, []
        return ctx, study_store.list_materials(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)


def _reject(request: Request, notice: str) -> Response:
    """Recusa o envio com 400 e reapresenta a tela com o aviso — nada é gravado."""
    _, materials = _list_for(request)
    return _render_list(request, materials, notice=notice, status=400)


@router.get("/materials")
def list_materials_page(request: Request) -> Response:
    """Lista os materiais do usuário e o formulário de envio."""
    ctx, materials = _list_for(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    return _render_list(request, materials)


def _declares_oversized_body(request: Request, limit: int) -> bool:
    """True quando o `Content-Length` já anuncia corpo acima do teto.

    Compara o corpo INTEIRO (arquivo + overhead do multipart) com o teto do arquivo:
    a diferença é de algumas centenas de bytes e o erro cai para o lado seguro —
    recusar cedo é o ponto. Header ausente/ilegível não decide nada; a cerca real
    continua sendo a leitura limitada logo abaixo.
    """
    raw = request.headers.get("content-length")
    if not raw:
        return False
    try:
        return int(raw) > limit
    except ValueError:
        return False


def _validated_upload(
    request: Request, file: UploadFile | None
) -> tuple[MaterialFormat, bytes, ParsedMaterial] | Response:
    """Valida extensão → tamanho → parse, nessa ordem; devolve a recusa pronta se falhar.

    A ordem é comportamento: um arquivo grande recusado só DEPOIS do parse já custou
    a memória que o limite existe para não gastar.

    O que este teto NÃO faz: barrar o corpo na rede. Quando o handler roda, o Starlette
    já recebeu o multipart inteiro num `SpooledTemporaryFile` — o cap de rede/disco é do
    proxy à frente (Caddy `max_request_body`), não daqui. O ganho aqui é não trazer o
    conteúdo para a MEMÓRIA do processo nem entregá-lo ao parser.
    """
    fmt = _format_of(file)
    if file is None or fmt is None:
        return _reject(request, _BAD_FORMAT)
    limit = _max_bytes()
    too_big = f"Arquivo acima do limite de {limit // (1024 * 1024)} MB."
    if _declares_oversized_body(request, limit):
        return _reject(request, too_big)
    # Lê 1 byte além do teto: o excesso é detectado sem trazer o arquivo todo à memória.
    data = file.file.read(limit + 1)
    if len(data) > limit:
        return _reject(request, too_big)
    try:
        parsed = parse_material(data, fmt)
    except MaterialParseError as exc:
        return _reject(request, str(exc))
    return fmt, data, parsed


@router.post("/materials")
def upload_material(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Recebe um epub/PDF, extrai os capítulos e persiste o Material.

    Ordem deliberada: CSRF → sessão → corpo. Autorizar DEPOIS de ler e parsear seria
    gastar memória a mando de quem nem passou no gate.
    """
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)

    validated = _validated_upload(request, file)
    if isinstance(validated, Response):
        return validated
    fmt, data, parsed = validated

    try:
        directory = _materials_dir()
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)

    original = Path(file.filename or "").name if file is not None else ""
    path = _save_upload(directory, ctx, fmt, data)
    try:
        with client.connect_rw() as db:
            material = study_store.create_material(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                title=parsed.title or original or "Material sem título",
                fmt=fmt,
                original_filename=original,
                file_path=str(path),
                size_bytes=len(data),
                chapters=parsed.chapters,
            )
    except ConfigError:
        path.unlink(missing_ok=True)
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        path.unlink(missing_ok=True)
        _log.warning("study.material.store_failed", fmt=fmt, size_bytes=len(data))
        return PlainTextResponse(_STORE_FAILED, status_code=500)
    except Exception:
        # A limpeza não pode depender do catálogo de exceções conhecidas: qualquer
        # falha após a gravação deixaria um arquivo sem registro no volume.
        path.unlink(missing_ok=True)
        raise
    return RedirectResponse(f"{_MATERIALS_ROUTE}/{material.id.id}", status_code=303)


@router.get("/materials/{key}")
def material_detail(
    request: Request,
    key: str,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
) -> Response:
    """Tela de conferência: metadados do material + capítulos paginados."""
    size = clamp_size(size)
    start = clamp_start(start)
    material_key = key.strip()
    chapters: list[study_store.MaterialChapter] = []
    topic: study_store.Topic | None = None
    total = 0
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
        # A tabela do RecordID é SEMPRE `material`; o path param só escolhe a chave.
        material = (
            study_store.get_material(
                db, material_id=RecordID(_MATERIAL_TABLE, material_key), **scope
            )
            if material_key
            else None
        )
        if material is not None:
            chapters = study_store.list_chapters(
                db, material_id=material.id, limit=size, start=start, **scope
            )
            total = study_store.count_chapters(db, material_id=material.id, **scope)
            # Decide entre "Criar tema" e "Ver tema" na própria tela de conferência.
            topic = study_store.get_topic_for_material(db, material_id=material.id, **scope)
    if material is None:
        return templates.TemplateResponse(
            request, _NOT_FOUND_TEMPLATE, {"raw": key}, status_code=404
        )
    return templates.TemplateResponse(
        request,
        _DETAIL_TEMPLATE,
        {
            "material": material,
            "chapters": chapters,
            "start": start,
            "size": size,
            "total": total,
            "topic": topic,
            "csrf": csrf_token(request),
        },
    )


# --- Tema e plano (KUBO-136) -----------------------------------------------------------
#
# Mesma tríade de escrita das rotas acima: verify_csrf → sessão → connect_rw → 303 (PRG).
# Formulários clássicos, sem JS novo (molde do gate, ADR-0018).

_TOPIC_TEMPLATE = "study/topic.html"
_TOPIC_NOT_FOUND_TEMPLATE = "study/topic_not_found.html"
_TOPIC_TABLE = "topic"
_TOPICS_ROUTE = "/study/topics"

_PLANNER_PERSONA = "planner"
# 4096 (e não o default de 1024): o JSON de um plano com dezenas de lições trunca no
# meio e volta como saída malformada — o fallback mecânico esconderia a causa.
_PLANNER_MAX_TOKENS = 4096
_PLANNER_TIMEOUT = 60.0

# Cadência inicial de uma proposta nova: dias úteis. O dono ajusta na própria tela.
_DEFAULT_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")
_WEEKDAY_VALUES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Página de capítulos na varredura do material (o teto da store é 100).
_CHAPTER_PAGE = 100
# Teto de lições de um plano — o mesmo `max_length` de `PlanProposal.lessons`. Acima
# disso a proposta nem seria construível; recusar aqui dá um motivo legível ao dono
# em vez de um 500 vindo da validação do modelo.
_MAX_PLAN_CHAPTERS = 200

_NO_CHAPTERS = "O material não tem capítulos para virar plano."
_TOO_MANY_CHAPTERS = "O material tem capítulos demais para propor um plano — reduza na conferência."
_NO_PLAN = "Este tema ainda não tem plano proposto."
_PLAN_LOCKED = "Este plano já foi ativado e não aceita mais edição."


class Cadence(BaseModel):
    """Entrada validada dos checkboxes de dias da semana — a fronteira pydantic.

    `Literal` fechado e `min_length=1`: um dia inventado ou nenhum dia marcado é
    recusado na borda, senão um plano ficaria sem data-alvo possível.
    """

    weekdays: list[Weekday] = Field(min_length=1)


class MoveDirection(BaseModel):
    """Entrada validada do botão de mover lição — só para cima ou para baixo."""

    direction: Literal["up", "down"]


def _planner_persona(db: Any, ctx: SessionContext) -> Persona:
    """Persona `planner` do catálogo do tenant; ausente, o default de código.

    O seed não retro-semeia tenants criados antes desta fatia (ADR-0042), e um caminho
    de LEITURA não escreve no catálogo — mudança de catálogo é auditada e pertence ao
    dono. Por isso o default entra em memória, com log, e nunca por upsert.
    """
    personas = load_personas(db, ctx.tenant_id, ctx.user_id)
    found = personas.get(_PLANNER_PERSONA)
    if found is not None:
        return found
    _log.info("study.planner.persona_default", persona=_PLANNER_PERSONA)
    default = next(p for p in DEFAULT_PERSONAS if p["name"] == _PLANNER_PERSONA)
    return Persona.model_validate(default)


def build_planner(db: Any, ctx: SessionContext) -> Planner:
    """Monta o `Planner` com a persona `planner` do catálogo do tenant.

    Costura injetável (molde de `get_finder` em sources.py): os testes de rota trocam
    esta função por um fake, então NENHUM teste toca LiteLLM. Recebe `db` e `ctx` porque
    a persona é do catálogo do tenant (ADR-0042), não de um YAML global.
    """
    persona = _planner_persona(db, ctx)
    executor = ApiExecutor(
        ApiExecutorConfig(
            model=persona.model or "groq/llama-3.3-70b-versatile",
            max_tokens=_PLANNER_MAX_TOKENS,
            timeout=_PLANNER_TIMEOUT,
        ),
        max_attempts=1,
    )
    return Planner(executor=executor, prompt=persona.prompt)


def _today() -> date:
    """Hoje na tz de APRESENTAÇÃO (env `TZ`, default America/Sao_Paulo).

    O container roda em UTC: `date.today()` viraria amanhã a partir das 21h de Brasília
    e a primeira lição do plano cairia um dia à frente do que o dono vê na tela.
    """
    return datetime.now(_display_tz()).date()


def _display_tz() -> ZoneInfo:
    """Timezone de apresentação — mesma regra de `rendering._local` (env `TZ`)."""
    return ZoneInfo(os.environ.get("TZ") or "America/Sao_Paulo")


def _target_datetime(*, weekdays: Sequence[str], lesson_count: int) -> datetime | None:
    """Data-alvo como datetime aware em UTC, ou None quando o plano fica sem lição.

    O domínio raciocina em `date` (sem timezone); o storage é UTC. A conversão é aqui:
    meia-noite do dia-alvo na tz do dono, convertida a UTC — nunca o contrário, senão
    um alvo de sábado viraria domingo no banco.
    """
    if lesson_count < 1:
        return None
    tz = _display_tz()
    day = compute_target_date(start=_today(), weekdays=weekdays, lesson_count=lesson_count)
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def _topic_url(topic_id: RecordID, *, flag: str = "") -> str:
    """URL da tela do tema, com um flag opcional de aviso no PRG.

    Só o FLAG viaja na query (`?mecanico=1`); o texto vive no template — mensagem em
    query string é conteúdo refletido, e conteúdo refletido é XSS esperando acontecer.
    """
    return f"{_TOPICS_ROUTE}/{topic_id.id}" + (f"?{flag}=1" if flag else "")


def _all_chapters(db: Any, *, material_id: RecordID, scope: dict[str, Any]) -> list[Any]:
    """Todos os capítulos do material, paginando o teto da store.

    O plano é sobre o LIVRO INTEIRO: usar uma página só cobriria os 100 primeiros
    capítulos em silêncio. Para quando a página vem curta (fim) ou quando já passou do
    teto de lições — quem chama recusa a partir do tamanho da lista.
    """
    chapters: list[Any] = []
    start = 0
    while len(chapters) <= _MAX_PLAN_CHAPTERS:
        page = study_store.list_chapters(
            db, material_id=material_id, limit=_CHAPTER_PAGE, start=start, **scope
        )
        if not page:
            break
        chapters.extend(page)
        if len(page) < _CHAPTER_PAGE:
            break
        start += len(page)
    return chapters


def _proposal_entries(
    proposal: PlanProposal, chapters: Sequence[Any]
) -> list[study_store.PlanEntryInput]:
    """Traduz a proposta (capítulos por `seq`) nas lições que a store grava (por id)."""
    by_seq = {chapter.seq: chapter.id for chapter in chapters}
    return [
        study_store.PlanEntryInput(
            seq=position,
            title=lesson.title,
            chapter_ids=[by_seq[seq] for seq in lesson.chapter_seqs],
        )
        for position, lesson in enumerate(proposal.lessons, start=1)
    ]


def _topic_of(db: Any, key: str, ctx: SessionContext) -> study_store.Topic | None:
    """Tema do usuário pela chave da URL; None quando não existe ou é de outro."""
    topic_key = key.strip()
    if not topic_key:
        return None
    # A tabela do RecordID é SEMPRE `topic`; o path param só escolhe a chave.
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


@router.post("/materials/{key}/topic")
def create_topic(
    request: Request,
    key: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Cria o tema do material (título herdado do material) e redireciona pro tema."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
    try:
        with client.connect_rw() as db:
            material = study_store.get_material(
                db, material_id=RecordID(_MATERIAL_TABLE, key.strip()), **scope
            )
            if material is None:
                return templates.TemplateResponse(
                    request, _NOT_FOUND_TEMPLATE, {"raw": key}, status_code=404
                )
            topic = study_store.create_topic(
                db, material_id=material.id, title=material.title, **scope
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.topic.store_failed", material=key.strip())
        return _reject(request, "Não foi possível criar o tema deste material.")
    return RedirectResponse(_topic_url(topic.id), status_code=303)


@router.get("/topics/{key}")
def topic_detail(request: Request, key: str) -> Response:
    """Tela do tema: propor plano, revisar a proposta ou ver o plano ativo."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        topic = _topic_of(db, key, ctx)
        if topic is None:
            return _topic_missing(request, key)
        scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
        plan = study_store.get_plan_for_topic(db, topic_id=topic.id, **scope)
        entries = (
            study_store.list_plan_entries(db, plan_id=plan.id, **scope) if plan is not None else []
        )
    return templates.TemplateResponse(
        request,
        _TOPIC_TEMPLATE,
        {
            "topic": topic,
            "plan": plan,
            "entries": entries,
            "weekday_values": _WEEKDAY_VALUES,
            "csrf": csrf_token(request),
            "mechanical": request.query_params.get("mecanico") == "1",
            "activated": request.query_params.get("ativado") == "1",
        },
    )


@router.post("/topics/{key}/plan/propose")
def propose_plan(
    request: Request,
    key: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Propõe o plano pela persona; LLM indisponível cai na proposta mecânica."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
    try:
        with client.connect_rw() as db:
            topic = _topic_of(db, key, ctx)
            if topic is None:
                return _topic_missing(request, key)
            chapters = _all_chapters(db, material_id=topic.material, scope=scope)
            if not chapters:
                return PlainTextResponse(_NO_CHAPTERS, status_code=400)
            if len(chapters) > _MAX_PLAN_CHAPTERS:
                return PlainTextResponse(_TOO_MANY_CHAPTERS, status_code=400)
            return _save_proposal(db, topic=topic, chapters=chapters, ctx=ctx)
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        # Repropor sobre plano já ativado é a única recusa esperada aqui.
        _log.warning("study.plan.propose_refused", topic=key.strip())
        return PlainTextResponse(_PLAN_LOCKED, status_code=409)


def _save_proposal(
    db: Any, *, topic: study_store.Topic, chapters: list[Any], ctx: SessionContext
) -> Response:
    """Roda a persona (ou o fallback), grava a proposta e devolve o 303 do PRG.

    Extraído do handler para manter a complexidade sob o teto (C901) — o handler cuida
    dos portões (CSRF, sessão, material), este pedaço cuida da proposta em si.
    """
    proposal = build_planner(db, ctx).propose(chapters)
    mechanical = proposal is None
    if proposal is None:
        proposal = mechanical_proposal(chapters)
    entries = _proposal_entries(proposal, chapters)
    plan = study_store.save_plan_proposal(
        db,
        topic_id=topic.id,
        weekdays=list(_DEFAULT_WEEKDAYS),
        target_date=_target_datetime(weekdays=_DEFAULT_WEEKDAYS, lesson_count=len(entries)),
        entries=entries,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )
    _log.info(
        "study.plan.proposed",
        plan=str(plan.id),
        lessons=len(entries),
        mechanical=mechanical,
    )
    return RedirectResponse(
        _topic_url(topic.id, flag="mecanico" if mechanical else ""), status_code=303
    )


def _plan_edit(
    request: Request,
    key: str,
    csrf: str,
    apply: Callable[[Any, study_store.StudyPlan, list[Any]], None],
) -> Response:
    """Molde comum das três edições da proposta (remover, mover, cadência).

    Mesma tríade das outras escritas — CSRF → sessão → `connect_rw` → 303 (PRG) — com a
    leitura do plano e das lições feita uma vez: as três precisam contar as lições para
    recalcular a data-alvo. A única variação entre elas é `apply`.
    """
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
    try:
        with client.connect_rw() as db:
            topic = _topic_of(db, key, ctx)
            if topic is None:
                return _topic_missing(request, key)
            plan = study_store.get_plan_for_topic(db, topic_id=topic.id, **scope)
            if plan is None:
                return PlainTextResponse(_NO_PLAN, status_code=404)
            entries = study_store.list_plan_entries(db, plan_id=plan.id, **scope)
            apply(db, plan, entries)
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.plan.not_editable", topic=key.strip())
        return PlainTextResponse(_PLAN_LOCKED, status_code=409)
    return RedirectResponse(_topic_url(topic.id), status_code=303)


@router.post("/topics/{key}/plan/entries/{seq}/remove")
def remove_entry(
    request: Request,
    key: str,
    seq: int,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Remove uma lição da proposta e recalcula a data-alvo."""

    def _apply(db: Any, plan: study_store.StudyPlan, entries: list[Any]) -> None:
        study_store.remove_plan_entry(
            db,
            plan_id=plan.id,
            seq=seq,
            new_target=_target_datetime(weekdays=plan.weekdays, lesson_count=len(entries) - 1),
            tenant_id=plan.tenant_id,
            user_id=plan.user_id,
        )

    return _plan_edit(request, key, csrf, _apply)


@router.post("/topics/{key}/plan/entries/{seq}/move")
def move_entry(
    request: Request,
    key: str,
    seq: int,
    direction: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Move uma lição da proposta para cima ou para baixo."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    try:
        payload = MoveDirection(direction=direction)  # type: ignore[arg-type]
    except ValidationError:
        return PlainTextResponse("Direção inválida.", status_code=400)

    def _apply(db: Any, plan: study_store.StudyPlan, entries: list[Any]) -> None:
        study_store.move_plan_entry(
            db,
            plan_id=plan.id,
            seq=seq,
            direction=payload.direction,
            tenant_id=plan.tenant_id,
            user_id=plan.user_id,
        )

    return _plan_edit(request, key, csrf, _apply)


@router.post("/topics/{key}/plan/cadence")
def set_cadence(
    request: Request,
    key: str,
    weekdays: Annotated[list[str] | None, Form()] = None,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Troca os dias da semana da proposta e recalcula a data-alvo."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    try:
        payload = Cadence(weekdays=weekdays or [])  # type: ignore[arg-type]
    except ValidationError:
        return PlainTextResponse("Escolha ao menos um dia da semana válido.", status_code=400)

    def _apply(db: Any, plan: study_store.StudyPlan, entries: list[Any]) -> None:
        study_store.set_plan_cadence(
            db,
            plan_id=plan.id,
            weekdays=payload.weekdays,
            new_target=_target_datetime(weekdays=payload.weekdays, lesson_count=len(entries)),
            tenant_id=plan.tenant_id,
            user_id=plan.user_id,
        )

    return _plan_edit(request, key, csrf, _apply)


@router.post("/topics/{key}/plan/activate")
def activate_plan(
    request: Request,
    key: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Ativa o plano proposto — a partir daqui a data-alvo está congelada."""
    if not verify_csrf(request, csrf):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
    try:
        with client.connect_rw() as db:
            topic = _topic_of(db, key, ctx)
            if topic is None:
                return _topic_missing(request, key)
            plan = study_store.get_plan_for_topic(db, topic_id=topic.id, **scope)
            if plan is None:
                return PlainTextResponse(_NO_PLAN, status_code=404)
            active = study_store.activate_plan(db, plan_id=plan.id, **scope)
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        _log.warning("study.plan.not_activatable", topic=key.strip())
        return PlainTextResponse(_PLAN_LOCKED, status_code=409)
    _log.info("study.plan.activated", plan=str(active.id))
    return RedirectResponse(_topic_url(topic.id, flag="ativado"), status_code=303)
