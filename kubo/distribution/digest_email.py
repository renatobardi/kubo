"""Builder puro do digest de e-mail (ADR-0031): destilados → (assunto, texto, HTML).

HTML inline com identidade visual mínima (Inter, stone, preto mono, link âmbar).
Todo conteúdo dinâmico é escapado para o HTML; o corpo textual fica cru. Sem
novidade o builder devolve `None` — o worker não envia e-mail vazio.
"""

from __future__ import annotations

import html

from kubo.contracts.worker import DigestView

_TITLE_CAP = 200
_SUMMARY_CAP = 300
_ENTITIES_CAP = 8
_NO_TITLE = "(sem título)"


def build_email_digest(views: list[DigestView], base_url: str) -> tuple[str, str, str] | None:
    """Monta assunto + corpo textual + corpo HTML para o digest de e-mail.

    Retorna `None` quando não há novidades (só-se-novidade, ADR-0015 §V)."""
    total = len(views)
    if total == 0:
        return None
    subject = _subject(total)
    text = _build_text(views, base_url)
    html_body = _build_html(views, base_url)
    return subject, text, html_body


def _subject(total: int) -> str:
    plural = "novo" if total == 1 else "novos"
    return f"Kubo · {total} {plural} no acervo"


def _build_text(views: list[DigestView], base_url: str) -> str:
    header = f"Kubo · {len(views)} {'novo' if len(views) == 1 else 'novos'} no acervo"
    entries = [_text_entry(v, base_url) for v in views]
    footer = f"Ver na UI: {base_url}/distilled"
    return "\n\n".join([header, *entries, footer])


def _text_entry(view: DigestView, base_url: str) -> str:
    title = _cap(view.title or _NO_TITLE, _TITLE_CAP)
    summary = _cap(view.summary, _SUMMARY_CAP)
    link = _link(view.id, base_url)
    lines = [title, link, "", summary]
    if view.entities:
        names = ", ".join(view.entities[:_ENTITIES_CAP])
        lines.append(f"\nEntidades: {names}")
    return "\n".join(lines)


def _build_html(views: list[DigestView], base_url: str) -> str:
    total = len(views)
    plural = "novo" if total == 1 else "novos"
    entries = [_html_entry(v, base_url) for v in views]
    entries_html = "\n".join(entries)
    return _HTML_TEMPLATE.format(
        total=total,
        plural=plural,
        entries=entries_html,
        footer_link=html.escape(f"{base_url}/distilled", quote=True),
    )


_HTML_TEMPLATE = (
    "<!DOCTYPE html>\n"
    '<html lang="pt-BR">\n'
    "<head>\n"
    '  <meta charset="UTF-8">\n'
    '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    "  <title>Kubo digest</title>\n"
    "</head>\n"
    '<body style="margin:0;padding:0;background-color:#f8f7f5;'
    'font-family:Inter,Arial,sans-serif;color:#2a2a2a;">\n'
    '  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
    'border="0">\n'
    "    <tr>\n"
    '      <td align="center" style="padding:24px 12px;">\n'
    '        <table role="presentation" width="100%" '
    'cellspacing="0" cellpadding="0" border="0" '
    'style="max-width:600px;background:#ffffff;border-radius:12px;'
    'border:1px solid #e5e4e2;">\n'
    "          <tr>\n"
    '            <td style="padding:24px;">\n'
    '              <h1 style="margin:0 0 20px 0;font-size:18px;'
    'font-weight:600;color:#1a1a1a;">Kubo · {total} {plural} no acervo</h1>\n'
    "              {entries}\n"
    '              <p style="margin:24px 0 0 0;font-size:12px;color:#6b6b6b;">'
    '<a href="{footer_link}" style="color:#b06327;text-decoration:none;">'
    "Ver na UI</a></p>\n"
    "            </td>\n"
    "          </tr>\n"
    "        </table>\n"
    "      </td>\n"
    "    </tr>\n"
    "  </table>\n"
    "</body>\n"
    "</html>"
)


def _html_entry(view: DigestView, base_url: str) -> str:
    title = html.escape(_cap(view.title or _NO_TITLE, _TITLE_CAP), quote=False)
    summary = html.escape(_cap(view.summary, _SUMMARY_CAP), quote=False)
    link = html.escape(_link(view.id, base_url), quote=True)
    entities_block = ""
    if view.entities:
        names = ", ".join(html.escape(e, quote=False) for e in view.entities[:_ENTITIES_CAP])
        entities_block = (
            f'<p style="margin:8px 0 0 0;font-size:12px;color:#6b6b6b;">Entidades: {names}</p>'
        )
    return _ENTRY_TEMPLATE.format(
        link=link,
        title=title,
        summary=summary,
        entities=entities_block,
    )


_ENTRY_TEMPLATE = (
    '<div style="margin-bottom:20px;padding-bottom:20px;'
    'border-bottom:1px solid #e5e4e2;">\n'
    '  <h2 style="margin:0 0 8px 0;font-size:16px;font-weight:600;">'
    '<a href="{link}" style="color:#b06327;text-decoration:none;">{title}</a></h2>\n'
    '  <p style="margin:0 0 8px 0;font-size:14px;line-height:1.5;">{summary}</p>\n'
    "  {entities}\n"
    "</div>"
)


def _link(view_id: str, base_url: str) -> str:
    _, _, key = view_id.partition(":")
    return f"{base_url}/distilled/{key}"


def _cap(text: str, cap: int) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"
