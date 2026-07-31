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
        "Obrigado por fazer parte do Kubo. Estou construindo essa ferramenta "
        "para ajudar a acompanhar o que importa e compartilhar conhecimento "
        "de forma mais leve.\n\n"
        "Se algo parecer estranho, lento ou confuso, responde aqui. "
        "Toda ajuda é bem-vinda para melhorar.\n\n"
        "Com carinho,\nBardi"
    )


def welcome_email(name: str) -> tuple[str, str, str]:
    """Assunto + corpo texto + corpo HTML para e-mail de boas-vindas."""
    subject = "Bem-vindo ao Kubo"
    text = (
        f"Olá, {name}!\n\n"
        "Obrigado por fazer parte do Kubo. Estou construindo essa ferramenta "
        "para ajudar a acompanhar o que importa e compartilhar conhecimento "
        "de forma mais leve.\n\n"
        "Se algo parecer estranho, lento ou confuso, me avisa. "
        "Toda ajuda é bem-vinda para melhorar.\n\n"
        "Com carinho,\nBardi"
    )
    html_body = (
        f"<p>Olá, {html.escape(name)}!</p>"
        "<p>Obrigado por fazer parte do Kubo. Estou construindo essa ferramenta "
        "para ajudar a acompanhar o que importa e compartilhar conhecimento "
        "de forma mais leve.</p>"
        "<p>Se algo parecer estranho, lento ou confuso, me avisa. "
        "Toda ajuda é bem-vinda para melhorar.</p>"
        "<p>Com carinho,<br>Renato</p>"
    )
    return subject, text, html_body
