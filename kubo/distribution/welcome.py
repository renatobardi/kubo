"""Mensagens de boas-vindas manuais para destinos (Kubo).

Um 'welcome' é um envio pontual, ativado por botão na UI, para validar o canal
sem parecer uma mensagem de teste genérica.
"""

from __future__ import annotations

import html

from kubo.distribution.email_template import _FONT, _INK, _SECONDARY, wrap_email


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
    safe_name = html.escape(name, quote=False)
    body_html = (
        '<tr><td class="kubo-pad" style="padding:16px 40px 0;">\n'
        f'<p class="kubo-fg" style="margin:0 0 12px 0;font-family:{_FONT};'
        f'font-size:15px;line-height:22px;color:{_INK};">Olá, {safe_name}!</p>\n'
        f'<p class="kubo-muted" style="margin:0 0 12px 0;font-family:{_FONT};'
        f'font-size:14px;line-height:22px;color:{_SECONDARY};">'
        "Canal do Kubo ativo. Daqui pra frente é por aqui que as entregas chegam.</p>\n"
        f'<p class="kubo-muted" style="margin:0 0 12px 0;font-family:{_FONT};'
        f'font-size:14px;line-height:22px;color:{_SECONDARY};">'
        "Se algo parecer errado, responde — leio tudo.</p>\n"
        f'<p class="kubo-fg" style="margin:0 0 8px 0;font-family:{_FONT};'
        f'font-size:14px;line-height:22px;color:{_INK};">— Bardi</p>\n'
        "</td></tr>\n"
    )
    html_body = wrap_email(
        heading="Bem-vindo ao Kubo",
        body_html=body_html,
        footer_link="",
        preheader="Canal do Kubo ativo",
    )
    return subject, text, html_body
