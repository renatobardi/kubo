"""Wrapper HTML compartilhado para e-mails do Kubo (design system).

Identidade visual: glifo sakura (❇) em tile near-black, card com ring
(box-shadow inset), MSO/Outlook conditional comments, preheader hidden,
dark mode via classes kubo-* em @media (prefers-color-scheme: dark),
mobile via @media (max-width:600px). Font Arial/Helvetica (email-safe).

Sem SVG, sem <img>, sem imagem externa — o logo é um glifo de texto num
tile colorido, roda em qualquer cliente de e-mail. Todo conteúdo dinâmico
(heading, body_html, footer_link, preheader) DEVE ser escapado pelo caller
antes de chegar aqui — este módulo é o shell, não valida entrada.
"""

from __future__ import annotations

# Cores (hex, alinhadas ao design system — oklch convertido para email-safe).
_INK = "#1c1917"  # foreground (stone-900)
_CARD = "#ffffff"  # card
_BORDER = "#e7e5e4"  # border (stone-200)
_MUTED = "#78716c"  # muted-foreground (stone-500)
_SECONDARY = "#57534e"  # secondary text (stone-600)
_FINE = "#a8a29e"  # fine print (stone-400)
_SAKURA_PINK = "#f4c9d4"  # glifo rosa no tile
_FONT = "Arial,Helvetica,sans-serif"  # email-safe (Inter não renderiza sem webfont)

# Glifo sakura — ❇ (U+2747 FLORAL HEART). Email-safe em qualquer cliente.
_GLYPH = "&#10047;"

_TAGLINE = "The art of getting things done"


def wrap_email(*, heading: str, body_html: str, footer_link: str, preheader: str = "") -> str:
    """Embrulha conteúdo no template HTML com a identidade do design system.

    Args:
        heading: título do e-mail (já escapado pelo caller).
        body_html: conteúdo HTML do corpo (já escapado pelo caller).
        footer_link: URL para o rodapé "Ver na UI" (já escapado pelo caller).
        preheader: texto de preview hidden (já escapado pelo caller). Vazio = sem bloco.

    Returns:
        HTML completo (DOCTYPE, html, head, body) pronto para envio.
    """
    preheader_html = _preheader_block(preheader)
    footer_html = _footer_block(footer_link) if footer_link else ""
    return _TEMPLATE.format(
        preheader=preheader_html,
        heading=heading,
        body=body_html,
        footer=footer_html,
        ink=_INK,
        card=_CARD,
        border=_BORDER,
        muted=_MUTED,
        secondary=_SECONDARY,
        fine=_FINE,
        pink=_SAKURA_PINK,
        font=_FONT,
        glyph=_GLYPH,
        tagline=_TAGLINE,
    )


def _preheader_block(preheader: str) -> str:
    """Bloco hidden de preview text — some em clientes que não suportam, inofensivo."""
    if not preheader:
        return ""
    # Espaços zero-width após o texto garantem que clientes de email não puxem
    # texto do corpo como preview (técnica padrão de preheader).
    return (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        f'font-size:1px;line-height:1px;">{preheader}'
        "&nbsp;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;"
        "&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;"
        "&#8203;&#8203;&#8203;&#8203;</div>\n"
    )


def _footer_block(footer_link: str) -> str:
    """Rodapé do card com link 'Ver na UI'."""
    return (
        '<tr><td class="kubo-pad" align="center" style="padding:4px 40px 32px;">\n'
        f'<a href="{footer_link}" class="kubo-fg" style="font-family:{_FONT};'
        f"font-size:13px;font-weight:bold;color:{_INK};"
        f'text-decoration:none;">Ver na UI &rarr;</a>\n'
        "</td></tr>\n"
    )


# Template com .format() — todas as {chaves} são campos; CSS braces escapados como {{ }}.
_TEMPLATE = (
    "<!DOCTYPE html>\n"
    '<html lang="pt-BR" xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office">\n'
    "<head>\n"
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<meta http-equiv="X-UA-Compatible" content="IE=edge">\n'
    '<meta name="color-scheme" content="light dark">\n'
    '<meta name="supported-color-schemes" content="light dark">\n'
    "<title>Kubo</title>\n"
    "<!--[if mso]>\n"
    "<noscript><xml><o:OfficeDocumentSettings>"
    "<o:PixelsPerInch>96</o:PixelsPerInch>"
    "</o:OfficeDocumentSettings></xml></noscript>\n"
    "<style>table{{border-collapse:collapse}}</style>\n"
    "<![endif]-->\n"
    "<style>\n"
    "body,table,td{{-ms-text-size-adjust:100%;-webkit-text-size-adjust:100%}}\n"
    "img{{border:0;line-height:100%;outline:none;text-decoration:none}}\n"
    "a{{text-decoration:none}}\n"
    "@media (max-width:600px){{\n"
    ".kubo-wrap{{width:100% !important}}\n"
    ".kubo-pad{{padding-left:20px !important;padding-right:20px !important}}\n"
    "}}\n"
    "@media (prefers-color-scheme: dark){{\n"
    ".kubo-card{{background-color:#292524 !important;"
    "box-shadow:inset 0 0 0 1px rgba(255,255,255,0.1) !important}}\n"
    ".kubo-fg{{color:#fafaf9 !important}}\n"
    ".kubo-muted{{color:#a8a29e !important}}\n"
    ".kubo-border{{border-color:rgba(255,255,255,0.1) !important}}\n"
    "}}\n"
    "</style>\n"
    "</head>\n"
    '<body style="margin:0;padding:0;">\n'
    "{preheader}"
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'border="0">\n'
    '<tr><td align="center" style="padding:32px 16px;">\n'
    '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
    'border="0" class="kubo-wrap" style="width:600px;max-width:600px;">\n'
    "\n"
    # --- Logo lockup: glifo sakura em tile near-black + wordmark ---
    '<tr><td align="center" style="padding:0 0 28px;">\n'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>\n'
    '<td style="padding-right:10px;" valign="middle">\n'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
    'width="28" height="28"><tr>\n'
    '<td width="28" height="28" align="center" valign="middle" '
    'style="width:28px;height:28px;background-color:{ink};border-radius:8px;'
    "font-family:Georgia,'Times New Roman',serif;font-size:15px;"
    'color:{pink};">{glyph}</td>\n'
    "</tr></table>\n"
    "</td>\n"
    '<td valign="middle" class="kubo-fg" style="font-family:{font};'
    "font-size:17px;font-weight:bold;letter-spacing:-0.3px;"
    'color:{ink};">Kubo</td>\n'
    "</tr></table>\n"
    "</td></tr>\n"
    "\n"
    # --- Card principal (ring via box-shadow inset, não border) ---
    '<tr><td class="kubo-card" style="background-color:{card};border-radius:16px;'
    'box-shadow:inset 0 0 0 1px {border};">\n'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">\n'
    "\n"
    # --- Heading ---
    '<tr><td class="kubo-pad" style="padding:28px 40px 8px;">\n'
    '<h1 class="kubo-fg" style="margin:0;font-family:{font};'
    "font-size:22px;font-weight:bold;letter-spacing:-0.4px;color:{ink};"
    '">{heading}</h1>\n'
    "</td></tr>\n"
    "\n"
    # --- Divider antes do body (kubo-border para dark mode) ---
    '<tr><td class="kubo-pad" style="padding:16px 40px 0;">'
    '<div class="kubo-border" style="border-top:1px solid {border};font-size:0;'
    'line-height:0;">&nbsp;</div></td></tr>\n'
    "\n"
    # --- Body (inserido pelo caller) ---
    "{body}\n"
    "\n"
    # --- Footer (link Ver na UI) ---
    "{footer}"
    "\n"
    "</table>\n"
    "</td></tr>\n"
    "\n"
    # --- Tagline abaixo do card ---
    '<tr><td align="center" style="padding:20px 24px 0;">\n'
    '<div class="kubo-muted" style="font-family:{font};'
    'font-size:12px;color:{fine};">{tagline}</div>\n'
    "</td></tr>\n"
    "\n"
    "</table>\n"
    "</td></tr>\n"
    "</table>\n"
    "</body>\n"
    "</html>"
)
