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
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from surrealdb import RecordID

from kubo.api.csrf import csrf_token, verify_csrf
from kubo.api.pagination import clamp_size, clamp_start
from kubo.api.rendering import templates
from kubo.api.session import SessionContext, resolve_session
from kubo.errors import ConfigError, MaterialParseError, StoreError
from kubo.store import client
from kubo.store import study as study_store
from kubo.study.parsing import MaterialFormat, ParsedMaterial, parse_material

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
        },
    )
