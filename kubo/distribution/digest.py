"""Builder PURO do digest (ADR-0015 §IV, ADR-0050) — itens → mensagem Telegram HTML.

Puro e SEPARADO do Jinja da UI (não reusar template: acoplaria os canais). HTML
parse mode do Telegram (MarkdownV2 rejeitado: 18 chars de escape sensíveis a
contexto, footgun). Escaping = `html.escape` (stdlib), mesma disciplina do XSS da
0009: só `<b>` e `<a href>` do NOSSO template são markup; TODO conteúdo dinâmico
(título, summary, nomes de entidade — hostis por padrão, invariante de consumo do
ADR-0013) é escapado. Único href = link para a fonte (`view.url`) se disponível,
senão link para a UI (`base_url` + id).

As quatro formas de mensagem (ADR-0050 §VI):
- normal: digest com os itens selecionados.
- recovery: digest identificado como recuperação (cobre múltiplos dias).
- empty_window: aviso curto — nada publicado na janela.
- none_passed: aviso curto — N publicações, nenhuma passou o corte.

Limite 4096: UMA mensagem, digest agrupado, truncamento HONESTO ("+N itens — ver
na UI") SÓ em fronteira de entry — cortar dentro de `<b>`/`<a>` = HTML inválido
= 400 = digest perdido = watermark não avança = bola de neve.
"""

from __future__ import annotations

import html
from urllib.parse import urlparse

from kubo.contracts.worker import DigestSelectionView, DigestView

# Teto de caracteres de uma mensagem no Bot API (parse_mode=HTML). Uma mensagem só.
TELEGRAM_LIMIT = 4096
# Cercas por-entry: mantêm cada entrada pequena o bastante para SEMPRE caber uma +
# rodapé em 4096 (garante que o truncamento em fronteira é sempre possível), e o
# digest escaneável. Não são segurança — são legibilidade + garantia de truncamento.
_TITLE_CAP = 200
_SUMMARY_CAP = 300
_ENTITIES_CAP = 8
_NO_TITLE = "(sem título)"


def build_telegram_digest(selection: DigestSelectionView, base_url: str) -> str:
    """Monta a mensagem HTML do digest (uma só, ≤ 4096) ou o aviso, conforme a forma.

    ADR-0050 §VI: as quatro formas de mensagem. O worker nunca fica em silêncio —
    o só-se-novidade (ADR-0015 §V) foi revogado."""
    form = selection.form
    if form in ("empty_window", "none_passed"):
        return _warning_message(selection)
    return _digest_message(selection.items, base_url, is_recovery=form == "recovery")


def _digest_message(items: list[DigestView], base_url: str, *, is_recovery: bool) -> str:
    """Monta a mensagem HTML do digest com os itens, truncando em fronteira de entry
    com rodapé "+N itens — ver na UI" quando não cabe tudo."""
    total = len(items)
    if total == 0:
        return ""
    header = _header(total, is_recovery=is_recovery)
    rendered = [_render_entry(v, base_url) for v in items]
    full = _assemble(header, rendered, omitted=0)
    if len(full) <= TELEGRAM_LIMIT:
        return full
    # Trunca: inclui entradas inteiras enquanto a mensagem + rodapé couber.
    included = 0
    while included < total:
        candidate = _assemble(header, rendered[: included + 1], omitted=total - included - 1)
        if len(candidate) > TELEGRAM_LIMIT:
            break
        included += 1
    return _assemble(header, rendered[:included], omitted=total - included)


def _warning_message(selection: DigestSelectionView) -> str:
    """Monta a mensagem de aviso para as formas 2 (empty_window) e 3 (none_passed)."""
    if selection.form == "none_passed":
        n = selection.total_publications
        pub = "publicação" if n == 1 else "publicações"
        return f"<b>Kubo · {n} {pub} no período, nenhuma passou o corte</b>"
    return "<b>Kubo · sem novidades no período</b>"


def _header(total: int, *, is_recovery: bool = False) -> str:
    """Cabeçalho do digest — o único markup com conteúdo estático (o número é nosso)."""
    plural = "novo" if total == 1 else "novos"
    label = "recuperação · " if is_recovery else ""
    return f"<b>Kubo · {label}{total} {plural} no acervo</b>"


def _render_entry(view: DigestView, base_url: str) -> str:
    """Renderiza UMA entrada: título (hyperlink para a fonte ou UI, em negrito), resumo
    curto e entidades. Todo conteúdo dinâmico é escapado; só `<b>`/`<a>` do template
    são markup. O título É o link (title-as-hyperlink) — sem linha separada de "abrir"."""
    title = _escape(_cap(view.title or _NO_TITLE, _TITLE_CAP))
    summary = _escape(_cap(view.summary, _SUMMARY_CAP))
    link = _link(view, base_url)
    lines = [f'<a href="{link}"><b>{title}</b></a>', summary]
    if view.entities:
        names = ", ".join(_escape(e) for e in view.entities[:_ENTITIES_CAP])
        lines.append(f"Entidades: {names}")
    return "\n".join(lines)


def _assemble(header: str, entries: list[str], *, omitted: int) -> str:
    """Junta cabeçalho + entradas + rodapé de truncamento (só se `omitted > 0`)."""
    blocks = [header, *entries]
    if omitted > 0:
        blocks.append(f"+{omitted} itens — ver na UI")
    return "\n\n".join(blocks)


def _link(view: DigestView, base_url: str) -> str:
    """Link para a fonte (`view.url`) se for HTTP(S), senão para a UI (`base_url` + id).
    Escapado com quote=True (é valor de atributo) — defesa mesmo o id sendo nosso hex.
    URL hostil (javascript:, data:) cai no fallback da UI — conteúdo coletado é hostil."""
    if view.url and _is_safe_url(view.url):
        return html.escape(view.url, quote=True)
    _, _, key = view.id.partition(":")
    return html.escape(f"{base_url}/item/{key}", quote=True)


def _is_safe_url(url: str) -> bool:
    """True se a URL tem scheme HTTP(S) — fecha a porta a javascript:/data: no href."""
    parsed = urlparse(url.strip())
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _escape(text: str) -> str:
    """Escapa conteúdo dinâmico para HTML (quote=False: é texto de nó, não atributo)."""
    return html.escape(text, quote=False)


def _cap(text: str, cap: int) -> str:
    """Trunca texto longo com reticências — cerca de volume por-entry (não segurança)."""
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"
