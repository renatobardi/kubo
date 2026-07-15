"""Operações REST no repositório sandbox: abrir e fechar PR (ADR-0019 §VI/§IX).

E3 — estrutura NUNCA vem do texto do agente: a URL e o número do PR vêm da RESPOSTA da API
do GitHub; o agente contribui só prosa (o `body`). E8/D38 — SEM função de merge: o Kubo
não tem capacidade de merge (anti-bypass por construção). O PAT viaja no header
`Authorization` (nunca na URL); erro sanitiza (redação belt-and-suspenders) como o Telegram.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict

from kubo.errors import ForgeError

_TIMEOUT = httpx.Timeout(30.0)
_API_VERSION = "2022-11-28"
_REDACTED = "<pat-redacted>"


class PrRef(BaseModel):
    """Referência estrutural do PR — vinda da API (E3), nunca do agente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    number: int


class PrStatus(BaseModel):
    """Estado de MERGE de um PR lido da API (ADR-0021 §2, E10/E12) — só leitura, nunca mescla.

    `merged` é a verdade do rito (aprovar ≠ merge — D38); `merge_commit_sha` é a âncora de
    auditoria. Num PR ABERTO a API preenche `merge_commit_sha` com um test-merge NÃO confiável —
    o chamador só usa o SHA quando `merged` é true."""

    # strict=True (achado CodeRabbit, crítico): `bool()` coagiria uma string truthy ("false")
    # a True — `merged` é o campo que decide o rito inteiro (E10/E12); um valor não-canônico
    # deve falhar a validação (→ ForgeError), nunca ser adivinhado.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    merged: bool
    merge_commit_sha: str | None = None


def get_pull_request(
    *,
    base_url: str,
    token: str,
    owner: str,
    repo: str,
    number: int,
    transport: httpx.BaseTransport | None = None,
) -> PrStatus:
    """Lê o estado de merge de um PR (E10/E12) com o token READ-ONLY — jamais mescla/comenta/fecha.

    Resposta sem `merged` vira `ForgeError` (sem vazar o token). O campo `merge_commit_sha` só é
    significativo quando `merged` é true (num PR aberto é um test-merge — o consumidor ignora)."""
    resp = _send(
        "GET",
        f"{base_url}/repos/{owner}/{repo}/pulls/{number}",
        token=token,
        json_body=None,
        transport=transport,
    )
    try:
        data = resp.json()
        return PrStatus(merged=data["merged"], merge_commit_sha=data.get("merge_commit_sha"))
    except (ValueError, KeyError, TypeError):
        raise ForgeError("resposta da API do GitHub sem 'merged' esperado") from None


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
    except (ValueError, KeyError, TypeError):
        # ValidationError (do PrRef) é subclasse de ValueError — já coberta, sem listar.
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
    json_body: dict[str, object] | None,
    transport: httpx.BaseTransport | None,
) -> httpx.Response:
    """Faz uma request autenticada; erro httpx vira `ForgeError` com o PAT redigido.

    `json_body=None` (GET de leitura, E12) não envia corpo. `transport` injetável para teste
    (httpx.MockTransport); None = transporte real."""
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
