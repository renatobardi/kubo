"""Builder puro do digest de e-mail (ADR-0031, ADR-0050, KUBO-196): itens → (assunto, texto, HTML).

Identidade do design system: glifo sakura em tile near-black, card com ring
(box-shadow inset), MSO/Outlook compat, preheader, dark mode, mobile. O shell
HTML vive em `email_template.wrap_email`; este módido monta o body (day_summary
+ entries como <tr>) e passa ao wrapper. Todo conteúdo dinâmico é escapado; o
corpo textual fica cru.

As quatro formas de mensagem (ADR-0050 §VI): normal/recovery enviam o digest;
empty_window/none_passed enviam um aviso curto. O worker nunca fica em silêncio.
"""

from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import urlparse

from kubo.contracts.worker import DigestSelectionView, DigestView
from kubo.distribution.email_template import (
    _BORDER,
    _FINE,
    _FONT,
    _INK,
    _MUTED,
    _SECONDARY,
    wrap_email,
)

_TITLE_CAP = 200
_SUMMARY_CAP = 300
_OPINION_CAP = 300
_ENTITIES_CAP = 8
_NO_TITLE = "(sem título)"


def build_email_digest(
    selection: DigestSelectionView, base_url: str
) -> tuple[str, str, str] | None:
    """Monta assunto + corpo textual + corpo HTML para o digest de e-mail.

    ADR-0050 §VI: as quatro formas de mensagem. Retorna sempre uma tupla —
    o worker nunca fica em silêncio (só-se-novidade revogado). ADR-0052: inclui
    o resumo do dia (bloco de abertura) e o parecer por item (em cada entry)."""
    form = selection.form
    if form in ("empty_window", "none_passed"):
        return _warning_email(selection, base_url)
    return _digest_email(selection, base_url, is_recovery=form == "recovery")


def _digest_email(
    selection: DigestSelectionView, base_url: str, *, is_recovery: bool
) -> tuple[str, str, str]:
    items = selection.items
    total = len(items)
    subject = _subject(total, is_recovery=is_recovery)
    text = _build_text(items, base_url, is_recovery=is_recovery, day_summary=selection.day_summary)
    html_body = _build_html(
        items, base_url, is_recovery=is_recovery, day_summary=selection.day_summary
    )
    return subject, text, html_body


def _warning_email(selection: DigestSelectionView, base_url: str) -> tuple[str, str, str]:
    if selection.form == "none_passed":
        n = selection.total_publications
        pub = "publicação" if n == 1 else "publicações"
        subject = f"Kubo · {n} {pub}, nenhuma passou o corte"
        message = f"Kubo · {n} {pub} no período, nenhuma passou o corte de relevância."
        heading = f"Kubo · {n} {pub}, nenhuma passou o corte"
    else:
        subject = "Kubo · sem novidades no período"
        message = "Kubo · sem novidades no período."
        heading = "Kubo · sem novidades no período"
    text = f"{message}\n\nVer na UI: {base_url}/distilled"
    warning_body = (
        '<tr><td class="kubo-pad" style="padding:16px 40px 0;">\n'
        f'<p class="kubo-muted" style="margin:0 0 8px 0;font-family:{_FONT};'
        f'font-size:14px;line-height:22px;color:{_SECONDARY};">'
        + html.escape(message, quote=False)
        + "</p>\n"
        "</td></tr>\n"
    )
    html_body = wrap_email(
        heading=html.escape(heading, quote=False),
        body_html=warning_body,
        footer_link=html.escape(f"{base_url}/distilled", quote=True),
        preheader=html.escape(heading, quote=False),
    )
    return subject, text, html_body


def _subject(total: int, *, is_recovery: bool = False) -> str:
    plural = "novo" if total == 1 else "novos"
    label = "recuperação · " if is_recovery else ""
    return f"Kubo · {label}{total} {plural} no acervo"


def _build_text(
    views: list[DigestView],
    base_url: str,
    *,
    is_recovery: bool = False,
    day_summary: str | None = None,
) -> str:
    header = _subject(len(views), is_recovery=is_recovery)
    blocks = [header]
    if day_summary:
        blocks.append(day_summary)
    entries = [_text_entry(v, base_url) for v in views]
    blocks.extend(entries)
    blocks.append(f"Ver na UI: {base_url}/distilled")
    return "\n\n".join(blocks)


def _text_entry(view: DigestView, base_url: str) -> str:
    title = _cap(view.title or _NO_TITLE, _TITLE_CAP)
    summary = _cap(view.summary, _SUMMARY_CAP)
    link = _link(view, base_url)
    date_str = _format_published_at(view.published_at)
    lines = [title, date_str, link, "", summary]
    if view.opinion:
        lines.append(f"\nParecer: {_cap(view.opinion, _OPINION_CAP)}")
    if view.entities:
        names = ", ".join(view.entities[:_ENTITIES_CAP])
        lines.append(f"\nEntidades: {names}")
    return "\n".join(lines)


def _build_html(
    views: list[DigestView],
    base_url: str,
    *,
    is_recovery: bool = False,
    day_summary: str | None = None,
) -> str:
    total = len(views)
    plural = "novo" if total == 1 else "novos"
    label = "recuperação · " if is_recovery else ""
    heading = f"Kubo · {label}{total} {plural} no acervo"
    parts: list[str] = []
    if day_summary:
        parts.append(_day_summary_tr(html.escape(day_summary, quote=False)))
    for v in views:
        parts.append(_html_entry(v, base_url))
        parts.append(_DIVIDER_TR)
    body_html = "\n".join(parts)
    return wrap_email(
        heading=html.escape(heading, quote=False),
        body_html=body_html,
        footer_link=html.escape(f"{base_url}/distilled", quote=True),
        preheader=html.escape(heading, quote=False),
    )


def _day_summary_tr(summary: str) -> str:
    """Resumo do dia como <tr> — bloco de abertura antes das entries (ADR-0052 §II)."""
    return (
        '<tr><td class="kubo-pad" style="padding:16px 40px 0;">\n'
        f'<div class="kubo-muted" style="font-family:{_FONT};'
        f'font-size:14px;line-height:22px;color:{_SECONDARY};font-style:italic;">{summary}</div>\n'
        "</td></tr>\n" + _DIVIDER_TR
    )


_DIVIDER_TR = (
    '<tr><td class="kubo-pad" style="padding:0 40px;">'
    f'<div class="kubo-border" style="border-top:1px solid {_BORDER};'
    'font-size:0;line-height:0;">&nbsp;</div></td></tr>\n'
)


def _format_published_at(dt: datetime) -> str:
    """Formata data/hora de publicação em PT-BR: '13 jul 2026 · 00:00' (UTC)."""
    months = [
        "jan",
        "fev",
        "mar",
        "abr",
        "mai",
        "jun",
        "jul",
        "ago",
        "set",
        "out",
        "nov",
        "dez",
    ]
    return f"{dt.day:02d} {months[dt.month - 1]} {dt.year} · {dt.hour:02d}:{dt.minute:02d}"


def _html_entry(view: DigestView, base_url: str) -> str:
    title = html.escape(_cap(view.title or _NO_TITLE, _TITLE_CAP), quote=False)
    summary = html.escape(_cap(view.summary, _SUMMARY_CAP), quote=False)
    link = html.escape(_link(view, base_url), quote=True)
    date_str = html.escape(_format_published_at(view.published_at), quote=False)
    opinion_html = ""
    if view.opinion:
        opinion = html.escape(_cap(view.opinion, _OPINION_CAP), quote=False)
        opinion_html = (
            f'<div class="kubo-muted" style="margin-top:8px;font-family:{_FONT};'
            f"font-size:13px;line-height:20px;color:{_SECONDARY};"
            f'font-style:italic;">Parecer: {opinion}</div>\n'
        )
    entities_html = ""
    if view.entities:
        names = ", ".join(html.escape(e, quote=False) for e in view.entities[:_ENTITIES_CAP])
        entities_html = (
            f'<div class="kubo-muted" style="margin-top:8px;font-family:{_FONT};'
            f'font-size:12px;color:{_MUTED};">Entidades: {names}</div>\n'
        )
    return _ENTRY_TR.format(
        link=link,
        title=title,
        date=date_str,
        summary=summary,
        opinion=opinion_html,
        entities=entities_html,
        font=_FONT,
        ink=_INK,
        secondary=_SECONDARY,
        fine=_FINE,
    )


_ENTRY_TR = (
    '<tr><td class="kubo-pad" style="padding:16px 40px 0;">\n'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
    '<tr><td style="padding-bottom:20px;">\n'
    '<a href="{link}" class="kubo-fg" style="font-family:{font};'
    'font-size:15px;font-weight:bold;color:{ink};text-decoration:none;">{title}</a>\n'
    '<div class="kubo-muted" style="margin-top:4px;font-family:{font};'
    'font-size:13px;line-height:20px;color:{secondary};">{summary}</div>\n'
    '<div style="margin-top:6px;font-family:{font};'
    'font-size:12px;color:{fine};">{date}</div>\n'
    "{opinion}"
    "{entities}"
    "</td></tr></table>\n"
    "</td></tr>\n"
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
