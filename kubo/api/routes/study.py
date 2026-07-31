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
from datetime import date, datetime, time, timedelta, timezone
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
from kubo.runtime.personas import resolve_persona
from kubo.store import client
from kubo.store import study as study_store
from kubo.study.parsing import MaterialFormat, ParsedMaterial, parse_material
from kubo.study.planner import Planner, PlanProposal, mechanical_proposal
from kubo.study.planning import compute_target_date, next_study_day
from kubo.study.progress import PlanProgress, compute_progress, count_study_days

_log = structlog.get_logger(__name__)
router = APIRouter()

_LIST_TEMPLATE = "study/list.html"
_DETAIL_TEMPLATE = "study/detail.html"
_NOT_FOUND_TEMPLATE = "study/not_found.html"
_NEW_TEMPLATE = "study/new.html"
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
# Teto de uma chave de URL em CAMPO DE LOG: uma chave real (hex de 32) cabe com folga.
_MAX_LOG_KEY = 64


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


def _log_key(raw: str) -> str:
    """Chave da URL pronta para virar CAMPO DE LOG: aparada e truncada.

    O path param é entrada de fora e vai cru para o log; uma chave de 100 kB inflaria a
    linha (e o disco) sem acrescentar nada — o que identifica um record cabe de sobra em
    `_MAX_LOG_KEY`. Truncar, e não recusar: o log é diagnóstico, não validação.
    """
    return raw.strip()[:_MAX_LOG_KEY]


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


@router.get("/new")
def new_study_page(request: Request) -> Response:
    """Guia de entrada do módulo: os 3 passos do estudo, com o envio de material.

    Tela de LEITURA: o formulário posta na rota de escrita que já existe
    (`POST /study/materials`) — nenhum caminho de escrita novo nasce aqui.
    """
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    return templates.TemplateResponse(request, _NEW_TEMPLATE, {"csrf": csrf_token(request)})


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
_TOPICS_LIST_TEMPLATE = "study/topics.html"
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
# Estado do plano que já produz lição (a store é a dona do vocabulário).
_ACTIVE_STATUS = "active"
# Estado anterior à ativação: sem régua de progresso, e sem pausar/retomar (KUBO-138).
_PROPOSED_STATUS = "proposed"
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


def build_planner(db: Any, ctx: SessionContext) -> Planner:
    """Monta o `Planner` com a persona `planner` do catálogo do tenant.

    Costura injetável (molde de `get_finder` em sources.py): os testes de rota trocam
    esta função por um fake, então NENHUM teste toca LiteLLM. Recebe `db` e `ctx` porque
    a persona é do catálogo do tenant (ADR-0042), não de um YAML global.
    """
    persona = resolve_persona(db, ctx.tenant_id, ctx.user_id, _PLANNER_PERSONA)
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
        _log.warning("study.topic.store_failed", material=_log_key(key))
        return _reject(request, "Não foi possível criar o tema deste material.")
    return RedirectResponse(_topic_url(topic.id), status_code=303)


def _midnight_utc(day: date) -> datetime:
    """Meia-noite do dia na tz do dono, em UTC — a chave de `scheduled_for` no banco."""
    return datetime.combine(day, time.min, tzinfo=_display_tz()).astimezone(timezone.utc)


def _plan_progress(
    db: Any,
    *,
    plan: study_store.StudyPlan | None,
    entries: list[study_store.PlanEntry],
    scope: dict[str, Any],
) -> PlanProgress | None:
    """A régua do plano hoje, ou None quando ainda não há régua.

    A régua nasce na ATIVAÇÃO: plano ainda proposto (ou tema sem plano) não tem progresso
    a mostrar, e derivar atraso do nada inventaria cobrança para quem está curando o
    plano. Nada aqui lê "meta batida" (ADR-0043): as conclusões saem dos registros de
    estudo e viram o dia LOCAL do dono — comparar dias em UTC faria a lição das 21h
    contar para o dia seguinte.
    """
    if plan is None or plan.status == _PROPOSED_STATUS or not entries:
        return None
    tz = _display_tz()
    completions = study_store.completion_dates(db, plan_id=plan.id, **scope)
    return compute_progress(
        activated_on=(plan.activated_at or plan.created_at).astimezone(tz).date(),
        today=_today(),
        weekdays=plan.weekdays,
        total=len(entries),
        completions=[moment.astimezone(tz).date() for moment in completions],
        paused_days=plan.paused_days,
    )


def _plan_lessons(db: Any, plan: study_store.StudyPlan, scope: dict[str, Any]) -> dict[str, Any]:
    """Lição do dia (ou a da próxima data de estudo) + histórico do plano ATIVO.

    Enquanto o plano está `proposed` não há lição nenhuma para ler — por isso a leitura
    inteira fica atrás do estado do plano, e não espalhada em ifs no template.
    """
    today = _today()
    current = study_store.get_lesson_for_day(
        db, plan_id=plan.id, scheduled_for=_midnight_utc(today), **scope
    )
    upcoming = None
    if current is None:
        upcoming = study_store.get_lesson_for_day(
            db,
            plan_id=plan.id,
            scheduled_for=_midnight_utc(next_study_day(after=today, weekdays=plan.weekdays)),
            **scope,
        )
    return {"today_lesson": current, "next_lesson": upcoming}


@router.get("/topics")
def list_topics_page(request: Request) -> Response:
    """Lista os temas do usuário com o estado do plano de cada um."""
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        scope = {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
        # Uma leitura de plano por tema: o estado do plano é O que o dono procura nesta
        # lista, e a alternativa seria uma consulta agregada nova só para a tela — mesmo
        # trade-off já assumido para `get_log_for_lesson` em `topic_detail`. A lista de
        # temas de um dono é curta por natureza (um tema = um livro que ele está lendo).
        rows = [
            {"topic": topic, "plan": study_store.get_plan_for_topic(db, topic_id=topic.id, **scope)}
            for topic in study_store.list_topics(db, **scope)
        ]
    return templates.TemplateResponse(request, _TOPICS_LIST_TEMPLATE, {"rows": rows})


@router.get("/topics/{key}")
def topic_detail(
    request: Request,
    key: str,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 20,
) -> Response:
    """Tela do tema: propor plano, revisar a proposta ou ver o plano ativo.

    Com plano ativo mostra também a lição de hoje (ou a da próxima data) e o histórico
    paginado das lições já geradas.
    """
    size = clamp_size(size)
    start = clamp_start(start)
    lessons: list[study_store.Lesson] = []
    completed: set[str] = set()
    total = 0
    current: dict[str, Any] = {"today_lesson": None, "next_lesson": None}
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
        if plan is not None and plan.status == _ACTIVE_STATUS:
            current = _plan_lessons(db, plan, scope)
            lessons = study_store.list_lessons(
                db, plan_id=plan.id, limit=size, start=start, **scope
            )
            total = study_store.count_lessons(db, plan_id=plan.id, **scope)
            # Uma leitura de registro por lição DA PÁGINA (no máximo `size`, clampado):
            # o estado concluída/pendente é o que o dono procura na lista, e a alternativa
            # seria uma consulta agregada nova só para a tela.
            completed = {
                str(lesson.id)
                for lesson in lessons
                if study_store.get_log_for_lesson(db, lesson_id=lesson.id, **scope) is not None
            }
        progress = _plan_progress(db, plan=plan, entries=entries, scope=scope)
    return templates.TemplateResponse(
        request,
        _TOPIC_TEMPLATE,
        {
            "topic": topic,
            "plan": plan,
            "progress": progress,
            "entries": entries,
            "lessons": lessons,
            "completed": completed,
            "start": start,
            "size": size,
            "total": total,
            "weekday_values": _WEEKDAY_VALUES,
            "csrf": csrf_token(request),
            "mechanical": request.query_params.get("mecanico") == "1",
            "activated": request.query_params.get("ativado") == "1",
            **current,
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
        # Leitura + persona na conexão de LEITURA: a chamada ao LLM leva até 60s, e
        # segurá-los dentro do `connect_rw` prenderia a conexão privilegiada de escrita
        # (ADR-0018) por todo esse tempo, à mercê da latência de um provedor externo. A
        # escrita abre depois, e dura o que a store demora.
        with client.connect() as ro:
            topic = _topic_of(ro, key, ctx)
            if topic is None:
                return _topic_missing(request, key)
            chapters = _all_chapters(ro, material_id=topic.material, scope=scope)
            if not chapters:
                return PlainTextResponse(_NO_CHAPTERS, status_code=400)
            if len(chapters) > _MAX_PLAN_CHAPTERS:
                return PlainTextResponse(_TOO_MANY_CHAPTERS, status_code=400)
            proposal = build_planner(ro, ctx).propose(chapters)
        return _save_proposal(topic=topic, chapters=chapters, proposal=proposal, ctx=ctx)
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        # Repropor sobre plano já ativado é a única recusa esperada aqui.
        _log.warning("study.plan.propose_refused", topic=_log_key(key))
        return PlainTextResponse(_PLAN_LOCKED, status_code=409)


def _save_proposal(
    *,
    topic: study_store.Topic,
    chapters: list[Any],
    proposal: PlanProposal | None,
    ctx: SessionContext,
) -> Response:
    """Grava a proposta (ou o fallback mecânico) e devolve o 303 do PRG.

    Recebe a proposta PRONTA: a persona já rodou na conexão de leitura, e a de escrita
    abre aqui, só para o tempo da store. Extraído do handler para manter a complexidade
    sob o teto (C901) — o handler cuida dos portões, este pedaço da gravação.
    """
    mechanical = proposal is None
    if proposal is None:
        proposal = mechanical_proposal(chapters)
    entries = _proposal_entries(proposal, chapters)
    with client.connect_rw() as db:
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
    *,
    refused: str = _PLAN_LOCKED,
) -> Response:
    """Molde comum das escritas sobre o plano (remover, mover, cadência, pausar, retomar).

    Mesma tríade das outras escritas — CSRF → sessão → `connect_rw` → 303 (PRG) — com a
    leitura do plano e das lições feita uma vez: todas precisam contar as lições para
    recalcular a data-alvo. A variação entre elas é `apply` e o texto de `refused`, que
    o 409 devolve: "não aceita mais edição" e "não está no estado necessário" são
    recusas diferentes, e mostrar a errada mandaria o dono procurar o problema no lugar
    errado.
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
        _log.warning("study.plan.not_editable", topic=_log_key(key))
        return PlainTextResponse(refused, status_code=409)
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
        _log.warning("study.plan.not_activatable", topic=_log_key(key))
        return PlainTextResponse(_PLAN_LOCKED, status_code=409)
    _log.info("study.plan.activated", plan=str(active.id))
    return RedirectResponse(_topic_url(topic.id, flag="ativado"), status_code=303)


# --- Lição do dia (KUBO-137) -----------------------------------------------------------
#
# Mesma tríade de escrita das rotas acima: verify_csrf → sessão → connect_rw → 303 (PRG).

_LESSONS_ROUTE = "/study/lessons"
_LESSON_TABLE = "lesson"
_LESSON_TEMPLATE = "study/lesson.html"
_LESSON_NOT_FOUND_TEMPLATE = "study/lesson_not_found.html"

_BAD_ANSWERS = "Responda todas as questões do quiz para concluir a lição."
_BAD_REACTION = "Reação inválida — escolha fácil, ok ou difícil."
_ALREADY_DONE = "Esta lição já foi concluída."

# Reações aceitas (espelho do ASSERT de `study_log.reaction` na migração 0023). Vazio é
# "não reagiu", não erro: a reação é opcional.
_REACTIONS = ("facil", "ok", "dificil")


def _paragraphs(text: str | None) -> list[str]:
    """Quebra um bloco da lição em parágrafos, uma linha não-vazia por parágrafo.

    A quebra é AQUI e não no template porque o template não pode marcar `\n` como HTML
    sem `|safe` — e `|safe` em texto vindo do LLM seria XSS com passe livre. O template
    itera parágrafos já escapados.
    """
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _lesson_of(db: Any, key: str, ctx: SessionContext) -> study_store.Lesson | None:
    """Lição do usuário pela chave da URL; None quando não existe ou é de outro."""
    lesson_key = key.strip()
    if not lesson_key:
        return None
    # A tabela do RecordID é SEMPRE `lesson`; o path param só escolhe a chave.
    return study_store.get_lesson(
        db,
        lesson_id=RecordID(_LESSON_TABLE, lesson_key),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
    )


def _lesson_missing(request: Request, key: str) -> Response:
    """404 da tela da lição — lição de outro usuário é INEXISTENTE, não 'negada'."""
    return templates.TemplateResponse(
        request, _LESSON_NOT_FOUND_TEMPLATE, {"raw": key}, status_code=404
    )


def _questions(
    lesson: study_store.Lesson, log: study_store.StudyLog | None
) -> list[dict[str, Any]]:
    """Questões do quiz prontas para a tela, com a resposta marcada quando há registro.

    A correção é montada aqui (não no template) para que a explicação só exista na
    estrutura quando a lição já foi concluída — antes disso, o gabarito não pode nem
    chegar ao HTML.
    """
    questions: list[dict[str, Any]] = []
    for index, item in enumerate(lesson.quiz):
        chosen = log.answers[index] if log is not None and index < len(log.answers) else None
        answer_index = item.get("answer_index")
        questions.append(
            {
                "index": index,
                "question": str(item.get("question", "")),
                "options": list(item.get("options") or []),
                "chosen": chosen,
                "answer_index": answer_index,
                "correct": chosen == answer_index,
                "explanation": str(item.get("explanation", "")) if log is not None else "",
            }
        )
    return questions


@router.get("/lessons/{key}")
def lesson_detail(request: Request, key: str) -> Response:
    """A lição do dia: recapitulação, conceito, cenário, aplicação e o quiz.

    Sem registro de estudo, o quiz é um formulário; com registro, vira correção
    (acerto/erro por questão + explicação), a reação e quando o dono concluiu.
    """
    with client.connect() as db:
        ctx = resolve_session(request, db)
        if ctx is None:
            return PlainTextResponse(_DENIED, status_code=403)
        lesson = _lesson_of(db, key, ctx)
        if lesson is None:
            return _lesson_missing(request, key)
        log = study_store.get_log_for_lesson(
            db, lesson_id=lesson.id, tenant_id=ctx.tenant_id, user_id=ctx.user_id
        )
    return templates.TemplateResponse(
        request,
        _LESSON_TEMPLATE,
        {
            "lesson": lesson,
            "log": log,
            "blocks": {
                "recap": _paragraphs(lesson.recap),
                "concept": _paragraphs(lesson.concept),
                "scenario": _paragraphs(lesson.scenario),
                "application": _paragraphs(lesson.application),
            },
            "questions": _questions(lesson, log),
            "reactions": _REACTIONS,
            "csrf": csrf_token(request),
        },
    )


def _answers_from_form(form: Any, lesson: study_store.Lesson) -> list[int] | None:
    """Uma resposta por questão do quiz, ou None quando o envio não casa com ele.

    Questão em branco é o caso perigoso: aceitá-la deslocaria as respostas seguintes e
    gravaria uma correção silenciosamente errada. Por isso a exigência é de contagem
    EXATA, e cada índice tem que existir entre as alternativas daquela questão.
    """
    answers: list[int] = []
    for index, item in enumerate(lesson.quiz):
        raw = form.get(f"answer_{index}")
        if not isinstance(raw, str):
            return None
        try:
            answer = int(raw)
        except ValueError:
            return None
        options = list(item.get("options") or [])
        if not 0 <= answer < len(options):
            return None
        answers.append(answer)
    return answers


@router.post("/lessons/{key}/complete")
async def complete_lesson(request: Request, key: str) -> Response:
    """Corrige as respostas contra o quiz da lição e grava o registro de estudo.

    ÚNICA rota `async` do módulo (o resto é síncrono, store bloqueante): os campos do
    quiz têm nome dinâmico (`answer_0`, `answer_1`, ...), e só `await request.form()`
    lê um formulário cujo shape depende do dado. As chamadas de store seguem
    bloqueantes — aceitável no regime de um usuário por vez desta fase.
    """
    form = await request.form()
    if not verify_csrf(request, str(form.get("csrf") or "")):
        return PlainTextResponse(_CSRF_INVALID, status_code=403)
    reaction = str(form.get("reaction") or "").strip()
    if reaction and reaction not in _REACTIONS:
        # Recusado ANTES da store: o ASSERT da migração devolveria um erro de banco,
        # e o dono veria 500 no lugar do motivo.
        return PlainTextResponse(_BAD_REACTION, status_code=400)
    # Sessão pela conexão de LEITURA, antes da de escrita — mesmo idioma das outras
    # escritas do módulo (ADR-0018): autorizar não é trabalho do usuário de escrita.
    ctx = _session_of(request)
    if ctx is None:
        return PlainTextResponse(_DENIED, status_code=403)
    try:
        with client.connect_rw() as db:
            lesson = _lesson_of(db, key, ctx)
            if lesson is None:
                return _lesson_missing(request, key)
            answers = _answers_from_form(form, lesson)
            if answers is None:
                return PlainTextResponse(_BAD_ANSWERS, status_code=400)
            study_store.complete_lesson(
                db,
                lesson_id=lesson.id,
                answers=answers,
                # A nota é da ROTA, contra o quiz CONGELADO da lição: aceitar um
                # `correct_count` do formulário poria o desempenho — que alimenta a
                # recapitulação — nas mãos de quem responde.
                correct_count=sum(
                    1
                    for answer, item in zip(answers, lesson.quiz, strict=False)
                    if answer == item.get("answer_index")
                ),
                reaction=reaction or None,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
            )
    except ConfigError:
        _log.warning("study.write_unavailable")
        return PlainTextResponse(_WRITE_UNAVAILABLE, status_code=503)
    except StoreError:
        # A recusa esperada é a segunda conclusão (UNIQUE por lição).
        _log.warning("study.lesson.complete_refused", lesson=_log_key(key))
        return PlainTextResponse(_ALREADY_DONE, status_code=409)
    return RedirectResponse(f"{_LESSONS_ROUTE}/{lesson.id.id}", status_code=303)


# --- Pausar e retomar o plano (KUBO-138) -----------------------------------------------
#
# Mesma tríade de escrita das rotas acima: verify_csrf → sessão → connect_rw → 303 (PRG).

_PLAN_STATE_REFUSED = "O plano não está no estado necessário para esta ação."


def _frozen_study_days(plan: study_store.StudyPlan) -> int:
    """Dias de estudo congelados por ESTA pausa — o que o retomar soma ao acumulador.

    Conta de `paused_at` até ONTEM: hoje ainda dá para estudar, então incluí-lo
    perdoaria um dia que o dono não perdeu. Sem `paused_at` (plano gravado antes da
    migração 0024) não há intervalo a congelar.
    """
    if plan.paused_at is None:
        return 0
    paused_on = plan.paused_at.astimezone(_display_tz()).date()
    return count_study_days(
        start=paused_on, end=_today() - timedelta(days=1), weekdays=plan.weekdays
    )


@router.post("/topics/{key}/plan/pause")
def pause_plan(
    request: Request,
    key: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Pausa o plano ativo: sem geração na véspera, sem sino, e o atraso congela."""

    def _apply(db: Any, plan: study_store.StudyPlan, entries: list[Any]) -> None:
        study_store.pause_plan(db, plan_id=plan.id, tenant_id=plan.tenant_id, user_id=plan.user_id)

    return _plan_edit(request, key, csrf, _apply, refused=_PLAN_STATE_REFUSED)


@router.post("/topics/{key}/plan/resume")
def resume_plan(
    request: Request,
    key: str,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Retoma o plano pausado, com a data-alvo recalculada a partir do que resta."""

    def _apply(db: Any, plan: study_store.StudyPlan, entries: list[Any]) -> None:
        scope = {"tenant_id": plan.tenant_id, "user_id": plan.user_id}
        done = study_store.count_completed_lessons(db, plan_id=plan.id, **scope)
        study_store.resume_plan(
            db,
            plan_id=plan.id,
            paused_days_add=_frozen_study_days(plan),
            # O alvo é recalculado a partir do que RESTA, contado de hoje: herdar o alvo
            # congelado devolveria uma data que passou enquanto o plano estava parado.
            new_target=_target_datetime(weekdays=plan.weekdays, lesson_count=len(entries) - done),
            **scope,
        )

    return _plan_edit(request, key, csrf, _apply, refused=_PLAN_STATE_REFUSED)
