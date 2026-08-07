"""Builder puro do digest de e-mail (ADR-0031, ADR-0050): itens → (assunto, texto, HTML).

HTML inline com identidade visual mínima (Inter, stone, preto mono, link âmbar).
Todo conteúdo dinâmico é escapado para o HTML; o corpo textual fica cru.

As quatro formas de mensagem (ADR-0050 §VI): normal/recovery enviam o digest;
empty_window/none_passed enviam um aviso curto. O worker nunca fica em silêncio.
"""

from __future__ import annotations

import html
from urllib.parse import urlparse

from kubo.contracts.worker import DigestSelectionView, DigestView

_TITLE_CAP = 200
_SUMMARY_CAP = 300
_ENTITIES_CAP = 8
_NO_TITLE = "(sem título)"


def build_email_digest(
    selection: DigestSelectionView, base_url: str
) -> tuple[str, str, str] | None:
    """Monta assunto + corpo textual + corpo HTML para o digest de e-mail.

    ADR-0050 §VI: as quatro formas de mensagem. Retorna sempre uma tupla —
    o worker nunca fica em silêncio (só-se-novidade revogado)."""
    form = selection.form
    if form in ("empty_window", "none_passed"):
        return _warning_email(selection, base_url)
    return _digest_email(selection.items, base_url, is_recovery=form == "recovery")


def _digest_email(
    items: list[DigestView], base_url: str, *, is_recovery: bool
) -> tuple[str, str, str]:
    total = len(items)
    subject = _subject(total, is_recovery=is_recovery)
    text = _build_text(items, base_url, is_recovery=is_recovery)
    html_body = _build_html(items, base_url, is_recovery=is_recovery)
    return subject, text, html_body


def _warning_email(selection: DigestSelectionView, base_url: str) -> tuple[str, str, str]:
    if selection.form == "none_passed":
        n = selection.total_publications
        pub = "publicação" if n == 1 else "publicações"
        subject = f"Kubo · {n} {pub}, nenhuma passou o corte"
        text = (
            f"Kubo · {n} {pub} no período, nenhuma passou o corte de relevância.\n\n"
            f"Ver na UI: {base_url}/distilled"
        )
    else:
        subject = "Kubo · sem novidades no período"
        text = f"Kubo · sem novidades no período.\n\nVer na UI: {base_url}/distilled"
    html_body = _HTML_TEMPLATE.format(
        total=0,
        plural="novos",
        entries=(
            '<p style="margin:0 0 8px 0;font-size:14px;">' + html.escape(text, quote=False) + "</p>"
        ),
        footer_link=html.escape(f"{base_url}/distilled", quote=True),
    )
    return subject, text, html_body


def _subject(total: int, *, is_recovery: bool = False) -> str:
    plural = "novo" if total == 1 else "novos"
    label = "recuperação · " if is_recovery else ""
    return f"Kubo · {label}{total} {plural} no acervo"


def _build_text(views: list[DigestView], base_url: str, *, is_recovery: bool = False) -> str:
    header = _subject(len(views), is_recovery=is_recovery)
    entries = [_text_entry(v, base_url) for v in views]
    footer = f"Ver na UI: {base_url}/distilled"
    return "\n\n".join([header, *entries, footer])


def _text_entry(view: DigestView, base_url: str) -> str:
    title = _cap(view.title or _NO_TITLE, _TITLE_CAP)
    summary = _cap(view.summary, _SUMMARY_CAP)
    link = _link(view, base_url)
    lines = [title, link, "", summary]
    if view.entities:
        names = ", ".join(view.entities[:_ENTITIES_CAP])
        lines.append(f"\nEntidades: {names}")
    return "\n".join(lines)


def _build_html(views: list[DigestView], base_url: str, *, is_recovery: bool = False) -> str:
    total = len(views)
    plural = "novo" if total == 1 else "novos"
    label = "recuperação · " if is_recovery else ""
    entries = [_html_entry(v, base_url) for v in views]
    entries_html = "\n".join(entries)
    return _HTML_TEMPLATE.format(
        total=f"{label}{total}",
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
    link = html.escape(_link(view, base_url), quote=True)
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


def _link(view: DigestView, base_url: str) -> str:
    """Link para a fonte (`view.url`) se for HTTP(S), senão para a UI (`base_url` + id).
    URL hostil (javascript:, data:) cai no fallback da UI — conteúdo coletado é hostil."""
    if view.url and _is_safe_url(view.url):
        return view.url
    _, _, key = view.id.partition(":")
    return f"{base_url}/item/{key}"


def _is_safe_url(url: str) -> bool:
    """True se a URL tem scheme HTTP(S) — fecha a porta a javascript:/data: no href."""
    parsed = urlparse(url.strip())
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _cap(text: str, cap: int) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"
