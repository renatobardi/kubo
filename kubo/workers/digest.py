"""Worker `telegram-digest` — envia o digest diário de UM destino Telegram.

ADR-0029: o digest vira sweep de destinos; cada canal ganha seu próprio worker,
single-destino. O endereço (PII) chega pelo CONSTRUTOR, nunca pela config ou
pelo payload; o sender é injetável para teste.
"""

from __future__ import annotations

from collections.abc import Callable

from kubo.contracts.models import WorkerManifest
from kubo.contracts.worker import DigestView, RunContext
from kubo.distribution.digest import build_telegram_digest
from kubo.distribution.telegram import send_telegram
from kubo.errors import SenderError
from kubo.store.destinations import Destination
from kubo.workers._digest_common import DigestConfig, _DigestWorker

# Sender de um canal: recebe segredo/endereço/texto e ENVIA (ou levanta SenderError).
# Assinatura por-keyword para casar com `send_telegram`; injetável para teste.
Sender = Callable[..., None]


class TelegramDigestWorker(_DigestWorker):
    """Envia o digest dos destilados novos para UM destino Telegram.

    `destination` e `base_url` são injetados na construção (o scheduler resolve
    do banco + env). `sender` é injetável para teste; padrão usa o sender real.
    """

    _channel = "telegram"
    _error_kind = "telegram_send"
    _config_class = DigestConfig

    manifest = WorkerManifest(
        name="telegram-digest",
        version="1",
        integrations=["telegram"],
        config=DigestConfig,
    )

    def __init__(
        self,
        *,
        destination: Destination,
        base_url: str,
        sender: Sender | None = None,
    ) -> None:
        super().__init__(destination=destination, base_url=base_url)
        self._sender: Sender = sender or send_telegram

    def _deliver(self, ctx: RunContext, views: list[DigestView]) -> None:
        """Monta a mensagem do Telegram e envia; levanta SenderError se não puder
        entregar."""
        if self._destination.channel != "telegram":
            raise SenderError(
                f"canal {self._destination.channel!r} não suportado por TelegramDigestWorker"
            )
        token = _integration_secret(ctx, "telegram")
        text = build_telegram_digest(views, self._base_url)
        self._sender(token=token, chat_id=self._destination.address, text=text)


def _integration_secret(ctx: RunContext, name: str) -> str:
    """Lê o segredo resolvido de uma integração do ctx; ausente → SenderError."""
    integration = ctx.integrations.get(name)
    secret = getattr(integration, "secret", None)
    if not secret:
        raise SenderError(f"integração {name!r} sem segredo resolvido no ctx")
    return str(secret)
