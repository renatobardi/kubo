"""Rotas de Estudos (ADR-0043, KUBO-135): ingestão e conferência de Material.

Rotas SÍNCRONAS (store bloqueante), leitura no molde de `entities.py` e escrita no
molde ADR-0018 de `settings.py`. Material é dado pessoal: toda rota resolve a sessão
e opera com `tenant_id`/`user_id` do contexto.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from starlette.responses import PlainTextResponse, Response

from kubo.api.session import resolve_session
from kubo.store import client
from kubo.store import study as study_store  # noqa: F401 — colaborador do GREEN
from kubo.study.parsing import parse_material  # noqa: F401 — colaborador do GREEN

router = APIRouter()

_NOT_IMPLEMENTED = "Estudos: ingestão de Material ainda não implementada."
_DENIED = "Acesso negado."


@router.get("/materials")
def list_materials_page(
    request: Request,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
) -> Response:
    """Lista os materiais do usuário e o formulário de envio."""
    with client.connect() as db:
        if resolve_session(request, db) is None:
            return PlainTextResponse(_DENIED, status_code=403)
    return PlainTextResponse(_NOT_IMPLEMENTED, status_code=501)


@router.post("/materials")
def upload_material(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    """Recebe um epub/PDF, extrai os capítulos e persiste o Material."""
    with client.connect() as db:
        if resolve_session(request, db) is None:
            return PlainTextResponse(_DENIED, status_code=403)
    return PlainTextResponse(_NOT_IMPLEMENTED, status_code=501)


@router.get("/materials/{key}")
def material_detail(
    request: Request,
    key: str,
    start: Annotated[int, Query()] = 0,
    size: Annotated[int, Query()] = 50,
) -> Response:
    """Tela de conferência: metadados do material + capítulos paginados."""
    with client.connect() as db:
        if resolve_session(request, db) is None:
            return PlainTextResponse(_DENIED, status_code=403)
    return PlainTextResponse(_NOT_IMPLEMENTED, status_code=501)
