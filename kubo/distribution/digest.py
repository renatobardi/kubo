"""Builder PURO do digest (ADR-0015 §IV, E4) — destilados → mensagem Telegram HTML.

Puro e SEPARADO do Jinja da UI (não reusar template: acoplaria os canais). HTML
parse mode do Telegram (MarkdownV2 rejeitado: 18 chars de escape sensíveis a
contexto, footgun). Escaping = `html.escape` (stdlib), mesma disciplina do XSS da
0009: só `<b>` e `<a href>` do NOSSO template são markup; TODO conteúdo dinâmico
(título, summary, nomes de entidade — hostis por padrão, invariante de consumo do
ADR-0013) é escapado. Único href = link para a UI (`base_url` + id) — `item.url`
coletada nunca vira hyperlink (nem existe no DigestView).

Limite 4096: UMA mensagem, digest agrupado, truncamento HONESTO ("+N destilados —
ver na UI") SÓ em fronteira de entry — cortar dentro de `<b>`/`<a>` = HTML inválido
= 400 = digest perdido = watermark não avança = bola de neve.
"""

from __future__ import annotations

import html

from kubo.contracts.worker import DigestView

# Teto de caracteres de uma mensagem no Bot API (parse_mode=HTML). Uma mensagem só.
TELEGRAM_LIMIT = 4096
# Cercas por-entry: mantêm cada entrada pequena o bastante para SEMPRE caber uma +
# rodapé em 4096 (garante que o truncamento em fronteira é sempre possível), e o
# digest escaneável. Não são segurança — são legibilidade + garantia de truncamento.
_TITLE_CAP = 200
_SUMMARY_CAP = 300
_ENTITIES_CAP = 8
_NO_TITLE = "(sem título)"


def build_telegram_digest(views: list[DigestView], base_url: str) -> str:
    """Monta a mensagem HTML do digest (uma só, ≤ 4096), truncando em fronteira de
    entry com rodapé "+N destilados — ver na UI" quando não cabe tudo.

    Vazio → string vazia (o worker sequer envia; ADR-0015 §V só-se-novidade)."""
    total = len(views)
    if total == 0:
        return ""
    header = _header(total)
    rendered = [_render_entry(v, base_url) for v in views]
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


def _header(total: int) -> str:
    """Cabeçalho do digest — o único markup com conteúdo estático (o número é nosso)."""
    plural = "novo" if total == 1 else "novos"
    return f"<b>Kubo · {total} {plural} no acervo</b>"


def _render_entry(view: DigestView, base_url: str) -> str:
    """Renderiza UMA entrada: título (hyperlink para a UI, em negrito), resumo curto e
    entidades. Todo conteúdo dinâmico é escapado; só `<b>`/`<a>` do template são markup.
    O título É o link (title-as-hyperlink) — sem linha separada de "abrir"."""
    title = _escape(_cap(view.title or _NO_TITLE, _TITLE_CAP))
    summary = _escape(_cap(view.summary, _SUMMARY_CAP))
    link = _link(view.id, base_url)
    lines = [f'<a href="{link}"><b>{title}</b></a>', summary]
    if view.entities:
        names = ", ".join(_escape(e) for e in view.entities[:_ENTITIES_CAP])
        lines.append(f"Entidades: {names}")
    return "\n".join(lines)


def _assemble(header: str, entries: list[str], *, omitted: int) -> str:
    """Junta cabeçalho + entradas + rodapé de truncamento (só se `omitted > 0`)."""
    blocks = [header, *entries]
    if omitted > 0:
        blocks.append(f"+{omitted} destilados — ver na UI")
    return "\n\n".join(blocks)


def _link(view_id: str, base_url: str) -> str:
    """Link para o detalhe na UI: `base_url` + a KEY do id (sem o prefixo `distilled:`).
    Escapado com quote=True (é valor de atributo) — defesa mesmo o id sendo nosso hex."""
    _, _, key = view_id.partition(":")
    return html.escape(f"{base_url}/distilled/{key}", quote=True)


def _escape(text: str) -> str:
    """Escapa conteúdo dinâmico para HTML (quote=False: é texto de nó, não atributo)."""
    return html.escape(text, quote=False)


def _cap(text: str, cap: int) -> str:
    """Trunca texto longo com reticências — cerca de volume por-entry (não segurança)."""
    text = text.strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"
