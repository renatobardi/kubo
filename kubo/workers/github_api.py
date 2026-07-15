"""Operações REST no repositório sandbox: abrir e fechar PR (ADR-0019 §VI/§IX).

E3 — estrutura NUNCA vem do texto do agente: a URL e o número do PR vêm da RESPOSTA da API
do GitHub; o agente contribui só prosa (o `body`). E8/D38 — SEM função de merge: o Kubo
não tem capacidade de merge (anti-bypass por construção). O PAT viaja no header
`Authorization` (nunca na URL); erro sanitiza (redação belt-and-suspenders) como o Telegram.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from kubo.errors import ForgeError

_TIMEOUT = httpx.Timeout(30.0)
_API_VERSION = "2022-11-28"
_REDACTED = "<pat-redacted>"


class PrRef(BaseModel):
    """Referência estrutural do PR — vinda da API (E3), nunca do agente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    number: int


def open_pull_request(
    *,
    base_url: str,
    token: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    transport: httpx.BaseTransport | None = None,
) -> PrRef:
    """Abre um PR de `head`→`base`; devolve a `PrRef` da resposta da API (E3).

    A `PrRef` sai do JSON da resposta (`html_url`/`number`) — NUNCA de dados que passamos
    ou do texto do agente. Resposta sem esses campos vira `ForgeError` (sem vazar o PAT)."""
    resp = _send(
        "POST",
        f"{base_url}/repos/{owner}/{repo}/pulls",
        token=token,
        json_body={"title": title, "head": head, "base": base, "body": body},
        transport=transport,
    )
    try:
        data = resp.json()
        return PrRef(url=data["html_url"], number=data["number"])
    except (ValueError, KeyError, TypeError, ValidationError):
        raise ForgeError("resposta da API do GitHub sem html_url/number esperados") from None


def close_pull_request(
    *,
    base_url: str,
    token: str,
    owner: str,
    repo: str,
    number: int,
    reason: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Comenta o `reason` e fecha o PR via API (D38 reject).

    O `reason` (input do dono) vai como VALOR do campo `body` no JSON — sem interpolação em
    string, sem template (E8). Comenta ANTES de fechar: se o comentário falhar, o PR não
    fecha sem o motivo registrado."""
    _send(
        "POST",
        f"{base_url}/repos/{owner}/{repo}/issues/{number}/comments",
        token=token,
        json_body={"body": reason},
        transport=transport,
    )
    _send(
        "PATCH",
        f"{base_url}/repos/{owner}/{repo}/pulls/{number}",
        token=token,
        json_body={"state": "closed"},
        transport=transport,
    )


def _send(
    method: str,
    url: str,
    *,
    token: str,
    json_body: dict[str, object],
    transport: httpx.BaseTransport | None,
) -> httpx.Response:
    """Faz uma request autenticada; erro httpx vira `ForgeError` com o PAT redigido.

    `transport` injetável para teste (httpx.MockTransport); None = transporte real."""
    try:
        with httpx.Client(timeout=_TIMEOUT, transport=transport) as client:
            resp = client.request(method, url, headers=_headers(token), json=json_body)
            resp.raise_for_status()
            return resp
    except httpx.HTTPError as exc:
        raise ForgeError(_redact(_describe(exc), token)) from None


def _headers(token: str) -> dict[str, str]:
    """Headers da API do GitHub — PAT no `Authorization`, nunca na URL."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }


def _describe(exc: httpx.HTTPError) -> str:
    """Falha SEM corpo cru: só status ou tipo da exceção (o corpo poderia carregar o PAT)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub respondeu HTTP {exc.response.status_code}"
    return f"falha de rede na API do GitHub ({type(exc).__name__})"


def _redact(text: str, token: str) -> str:
    """Remove qualquer ocorrência do PAT do texto — 2ª cerca (belt-and-suspenders)."""
    return text.replace(token, _REDACTED) if token else text
