"""Mensagens de boas-vindas manuais para destinos (Kubo).

Um 'welcome' é um envio pontual, ativado por botão na UI, para validar o canal
sem parecer uma mensagem de teste genérica.
"""

from __future__ import annotations

import html


def welcome_telegram_text(name: str) -> str:
    """Mensagem de boas-vindas em HTML para o Telegram."""
    return (
        f"<b>Olá, {html.escape(name)}!</b>\n\n"
        "Canal do Kubo ativo. Daqui pra frente é por aqui que as entregas chegam.\n\n"
        "Se algo parecer errado, responde — leio tudo.\n\n"
        "— Bardi"
    )


def welcome_email(name: str) -> tuple[str, str, str]:
    """Assunto + corpo texto + corpo HTML para e-mail de boas-vindas."""
    subject = "Bem-vindo ao Kubo"
    text = (
        f"Olá, {name}!\n\n"
        "Canal do Kubo ativo. Daqui pra frente é por aqui que as entregas chegam.\n\n"
        "Se algo parecer errado, responde — leio tudo.\n\n"
        "— Bardi"
    )
    html_body = (
        f"<p>Olá, {html.escape(name)}!</p>"
        "<p>Canal do Kubo ativo. Daqui pra frente é por aqui que as entregas chegam.</p>"
        "<p>Se algo parecer errado, responde — leio tudo.</p>"
        "<p>— Bardi</p>"
    )
    return subject, text, html_body
