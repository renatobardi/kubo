"""Sender Telegram (ADR-0015 §IV, E4): Bot API via httpx, parse_mode=HTML, e a
REDAÇÃO do token — o achado de segurança central da fatia.

O Bot API põe o token na URL; exceções httpx embutem a URL e o truncamento de 500
chars do ADR-0009 NÃO salva. O sender captura e sanitiza ANTES de construir o
SenderError — o token JAMAIS aparece em erro/log (teste obrigatório). Transport
mockado (httpx.MockTransport, stdlib do httpx) — nenhum teste toca a rede real.
"""

from __future__ import annotations

import httpx
import pytest

from kubo.distribution.telegram import send_telegram
from kubo.errors import SenderError

_TOKEN = "123456:AA-secret-bot-token"
_CHAT = "42"


def _transport(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


def test_success_posts_html_message() -> None:
    """Envio ok: POST em sendMessage com parse_mode=HTML, chat_id e texto."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    send_telegram(token=_TOKEN, chat_id=_CHAT, text="<b>oi</b>", transport=_transport(handler))
    assert "sendMessage" in str(seen["url"])
    assert '"parse_mode":"HTML"' in str(seen["body"])
    assert '"chat_id":"42"' in str(seen["body"])


def test_http_error_does_not_leak_token() -> None:
    """CANÁRIO de segurança: um 401 (cuja URL embute o token) vira SenderError SEM o token."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    transport = _transport(handler)
    with pytest.raises(SenderError) as exc:
        send_telegram(token=_TOKEN, chat_id=_CHAT, text="x", transport=transport)
    assert _TOKEN not in str(exc.value)
    assert _TOKEN not in repr(exc.value)


def test_telegram_ok_false_is_error_without_token() -> None:
    """200 com ok:false (Telegram sinaliza erro assim às vezes) também falha — sem token."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    transport = _transport(handler)
    with pytest.raises(SenderError) as exc:
        send_telegram(token=_TOKEN, chat_id=_CHAT, text="x", transport=transport)
    assert _TOKEN not in str(exc.value)


def test_network_error_does_not_leak_token() -> None:
    """Falha de rede (a exceção httpx embute a URL com o token) → SenderError sem token."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    transport = _transport(handler)
    with pytest.raises(SenderError) as exc:
        send_telegram(token=_TOKEN, chat_id=_CHAT, text="x", transport=transport)
    assert _TOKEN not in str(exc.value)
