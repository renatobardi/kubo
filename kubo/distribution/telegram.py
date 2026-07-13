"""Sender do Telegram Bot API (ADR-0015 §IV, E4) — envia UMA mensagem HTML.

Segurança central da fatia: o Bot API põe o token na URL (`/bot<TOKEN>/sendMessage`)
e as exceções httpx embutem a URL — o token vazaria para `run.error`/log, e o
truncamento de 500 chars do ADR-0009 não salva. Este módulo NUNCA deixa a URL
(logo o token) entrar numa mensagem de erro: descreve a falha por status/tipo, e
ainda passa qualquer texto por uma redação belt-and-suspenders. Análogo do
`repr=False` do segredo — não é disciplina, é construção.

Fallback barato (E4): se o Bot API rejeitar o HTML num edge case, o worker pode
reenviar sem `parse_mode` (texto puro) — decisão do worker, não deste sender.
"""

from __future__ import annotations

import httpx

from kubo.errors import SenderError

_TELEGRAM_API = "https://api.telegram.org"
_TIMEOUT = httpx.Timeout(15.0)
_REDACTED = "<token-redacted>"


def send_telegram(
    *,
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = "HTML",
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Envia `text` ao `chat_id` via Bot API. Levanta `SenderError` (com o token
    SEMPRE redigido) em qualquer falha — nunca deixa a exceção httpx crua escapar.

    `transport` é injetável para teste (httpx.MockTransport); em produção fica None
    (transporte real). `parse_mode=None` envia texto puro (fallback de HTML rejeitado)."""
    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    try:
        with httpx.Client(timeout=_TIMEOUT, transport=transport) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            _ensure_ok(resp)
    except httpx.HTTPError as exc:
        # Descrição por status/tipo — NÃO usa str(exc) (que embute a URL+token). A
        # redação é a 2ª cerca. `from None` corta o __cause__ (cujo traceback também
        # embutiria a URL) — o token não pode existir em nenhuma forma do erro.
        raise SenderError(_redact(_describe(exc), token)) from None


def _ensure_ok(resp: httpx.Response) -> None:
    """Telegram às vezes responde 200 com `{"ok": false, ...}` — trata como falha.
    A `description` do Telegram não carrega o token (o token só está na URL)."""
    body = resp.json()
    if not (isinstance(body, dict) and body.get("ok") is True):
        desc = body.get("description") if isinstance(body, dict) else None
        raise SenderError(f"Telegram recusou o envio: {desc or 'resposta ok=false'}")


def _describe(exc: httpx.HTTPError) -> str:
    """Mensagem de falha SEM a URL (logo sem o token): só status ou tipo da exceção."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Telegram respondeu HTTP {exc.response.status_code}"
    return f"falha de rede no envio Telegram ({type(exc).__name__})"


def _redact(text: str, token: str) -> str:
    """Remove qualquer ocorrência do token do texto — 2ª cerca contra vazamento."""
    return text.replace(token, _REDACTED) if token else text
