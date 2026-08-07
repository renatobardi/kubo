"""Builder puro do digest de e-mail (ADR-0031, ADR-0050, KUBO-196): itens → (assunto, texto, HTML).

Identidade Direção B v2 (canônica): preto mono, stone quente, Inter, sakura SVG inline.
Sem imagem externa, anexo embutido ou arquivo hospedado — a sakura é SVG inline e a
marca é texto. Dark mode via <style> no <head> com prefers-color-scheme. Mobile via
max-width:600px. Todo conteúdo dinâmico é escapado; o corpo textual fica cru.

As quatro formas de mensagem (ADR-0050 §VI): normal/recovery enviam o digest;
empty_window/none_passed enviam um aviso curto. O worker nunca fica em silêncio.
"""

from __future__ import annotations

import html
from datetime import datetime
from urllib.parse import urlparse

from kubo.contracts.worker import DigestSelectionView, DigestView

_TITLE_CAP = 200
_SUMMARY_CAP = 300
_OPINION_CAP = 300
_ENTITIES_CAP = 8
_NO_TITLE = "(sem título)"

# Direção B v2 — preto mono, stone quente (oklch aproximado em hex para email).
# Cores dark são hardcoded no <style> media query (CSS não pode usar vars Python).
_INK = "#1c1917"  # foreground (stone-900)
_BG = "#f5f5f4"  # background (stone-100)
_CARD = "#ffffff"  # card
_BORDER = "#e7e5e4"  # border (stone-200)
_MUTED = "#78716c"  # muted-foreground (stone-500)


def build_email_digest(
    selection: DigestSelectionView, base_url: str
) -> tuple[str, str, str] | None:
    """Monta assunto + corpo textual + corpo HTML para o digest de e-mail.

    ADR-0050 §VI: as quatro formas de mensagem. Retorna sempre uma tupla —
    o worker nunca fica em silêncio (só-se-novidade revogado). ADR-0052: inclui
    o resumo do dia (bloco de abertura) e o parecer por item (em cada entry).
    KUBO-196: identidade Direção B v2 — sakura SVG inline, preto mono, dark mode."""
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
        text = (
            f"Kubo · {n} {pub} no período, nenhuma passou o corte de relevância.\n\n"
            f"Ver na UI: {base_url}/distilled"
        )
    else:
        subject = "Kubo · sem novidades no período"
        text = f"Kubo · sem novidades no período.\n\nVer na UI: {base_url}/distilled"
    warning_html = (
        '<p style="margin:0 0 8px 0;font-size:14px;">' + html.escape(text, quote=False) + "</p>"
    )
    html_body = _wrap_email(
        heading="Kubo · sem novidades",
        day_summary="",
        entries=warning_html,
        footer_link=html.escape(f"{base_url}/distilled", quote=True),
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
    entries = [_html_entry(v, base_url) for v in views]
    entries_html = "\n".join(entries)
    day_summary_html = ""
    if day_summary:
        day_summary_html = (
            '<p style="margin:0 0 20px 0;font-size:14px;line-height:1.5;'
            f'color:{_MUTED};font-style:italic;">'
            + html.escape(day_summary, quote=False)
            + "</p>\n"
        )
    return _wrap_email(
        heading=html.escape(heading, quote=False),
        day_summary=day_summary_html,
        entries=entries_html,
        footer_link=html.escape(f"{base_url}/distilled", quote=True),
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


# --- Sakura SVG (5 pétalas, traço mono — Direção B v2) -----------------------

_SAKURA_SVG = (
    '<svg width="28" height="28" viewBox="0 0 100 100" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" '
    'style="display:inline-block;vertical-align:middle;">\n'
)
_PETAL = (
    '<path d="M50,50 C38,43 33,27 39,15 C42,8 47,10 50,17 '
    'C53,10 58,8 61,15 C67,27 62,43 50,50 Z" '
    'fill="#f4c9d4" stroke="#1c1917" stroke-width="6" '
    'stroke-linejoin="round" transform="rotate({angle} 50 50)"/>\n'
)
_STAMEN_LINE = (
    '<line x1="50" y1="50" x2="50" y2="34" stroke="#1c1917" '
    'stroke-width="3.3" stroke-linecap="round" '
    'transform="rotate({angle} 50 50)"/>\n'
)
_STAMEN_DOT = '<circle cx="50" cy="33" r="3" fill="#1c1917" transform="rotate({angle} 50 50)"/>\n'
_SAKURA_SVG += "".join(_PETAL.format(angle=a) for a in (0, 72, 144, 216, 288))
_SAKURA_SVG += "".join(_STAMEN_LINE.format(angle=a) for a in (36, 108, 180, 252, 324))
_SAKURA_SVG += "".join(_STAMEN_DOT.format(angle=a) for a in (36, 108, 180, 252, 324))
_SAKURA_SVG += '<circle cx="50" cy="50" r="5.4" fill="#1c1917"/>\n</svg>'


def _wrap_email(*, heading: str, day_summary: str, entries: str, footer_link: str) -> str:
    """Embrulha conteúdo no template HTML com identidade Direção B v2."""
    return _HTML_TEMPLATE.format(
        heading=heading,
        day_summary=day_summary,
        entries=entries,
        footer_link=footer_link,
        sakura=_SAKURA_SVG,
        tagline="The art of getting things done",
        ink=_INK,
        bg=_BG,
        card=_CARD,
        border=_BORDER,
        muted=_MUTED,
    )


_HTML_TEMPLATE = (
    "<!DOCTYPE html>\n"
    '<html lang="pt-BR">\n'
    "<head>\n"
    '  <meta charset="UTF-8">\n'
    '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    "  <title>Kubo digest</title>\n"
    "  <style>\n"
    "    @media (prefers-color-scheme: dark) {{\n"
    "      .email-body {{ background-color:#1c1917 !important; }}\n"
    "      .email-card {{ background-color:#292524 !important; "
    "border-color:rgba(255,255,255,0.10) !important; }}\n"
    "      .email-ink {{ color:#fafaf9 !important; }}\n"
    "      .email-muted {{ color:#a8a29e !important; }}\n"
    "      .email-border {{ border-color:rgba(255,255,255,0.10) !important; }}\n"
    "      .email-link {{ color:#fafaf9 !important; }}\n"
    "      .sakura-petal {{ fill:none !important; stroke:#f4c9d4 !important; }}\n"
    "      .sakura-ink {{ stroke:#f4c9d4 !important; fill:#f4c9d4 !important; }}\n"
    "    }}\n"
    "    @media (max-width: 600px) {{\n"
    "      .email-card {{ border-radius:0 !important; "
    "max-width:100% !important; }}\n"
    "      .email-pad {{ padding:16px !important; }}\n"
    "      .email-h1 {{ font-size:16px !important; }}\n"
    "      .email-h2 {{ font-size:15px !important; }}\n"
    "    }}\n"
    "  </style>\n"
    "</head>\n"
    '<body class="email-body" style="margin:0;padding:0;background-color:{bg};'
    'font-family:Inter,Arial,sans-serif;color:{ink};">\n'
    '  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
    'border="0">\n'
    "    <tr>\n"
    '      <td align="center" style="padding:24px 12px;">\n'
    '        <table role="presentation" width="100%" '
    'cellspacing="0" cellpadding="0" border="0" '
    'class="email-card" style="max-width:600px;background:{card};'
    'border-radius:14px;border:1px solid {border};">\n'
    "          <tr>\n"
    '            <td class="email-pad" style="padding:24px;">\n'
    '              <div style="margin:0 0 20px 0;">{sakura}'
    '<span style="font-size:18px;font-weight:600;color:{ink};'
    'vertical-align:middle;margin-left:8px;">Kubo</span><br>'
    '<span style="font-size:12px;color:{muted};'
    'vertical-align:middle;">{tagline}</span></div>\n'
    '              <h1 class="email-h1 email-ink" style="margin:0 0 20px 0;'
    'font-size:18px;font-weight:600;color:{ink};">{heading}</h1>\n'
    "              {day_summary}"
    "              {entries}\n"
    '              <p class="email-muted" style="margin:24px 0 0 0;'
    'font-size:12px;color:{muted};">'
    '<a href="{footer_link}" class="email-link" '
    'style="color:{ink};text-decoration:none;">Ver na UI</a></p>\n'
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
    date_str = html.escape(_format_published_at(view.published_at), quote=False)
    opinion_block = ""
    if view.opinion:
        opinion = html.escape(_cap(view.opinion, _OPINION_CAP), quote=False)
        opinion_block = (
            f'<p style="margin:8px 0 0 0;font-size:13px;line-height:1.5;'
            f'color:{_MUTED};font-style:italic;">Parecer: {opinion}</p>'
        )
    entities_block = ""
    if view.entities:
        names = ", ".join(html.escape(e, quote=False) for e in view.entities[:_ENTITIES_CAP])
        entities_block = (
            f'<p style="margin:8px 0 0 0;font-size:12px;color:{_MUTED};">Entidades: {names}</p>'
        )
    return _ENTRY_TEMPLATE.format(
        link=link,
        title=title,
        date=date_str,
        summary=summary,
        opinion=opinion_block,
        entities=entities_block,
        ink=_INK,
        muted=_MUTED,
        border=_BORDER,
    )


_ENTRY_TEMPLATE = (
    '<div class="email-border" style="margin-bottom:20px;padding-bottom:20px;'
    'border-bottom:1px solid {border};">\n'
    '  <h2 class="email-h2" style="margin:0 0 4px 0;font-size:16px;font-weight:600;">'
    '<a href="{link}" class="email-link" style="color:{ink};text-decoration:none;">'
    "{title}</a></h2>\n"
    '  <p class="email-muted" style="margin:0 0 8px 0;font-size:12px;color:{muted};'
    '">{date}</p>\n'
    '  <p style="margin:0 0 8px 0;font-size:14px;line-height:1.5;">{summary}</p>\n'
    "  {opinion}\n"
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
