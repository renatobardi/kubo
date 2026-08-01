"""Indicador de progresso global em toda ação de escrita da UI (KUBO-143).

Enviar um Material de ~39 MB (upload + parse de 71 capítulos + escrita no banco) ou
pedir "Propor plano" (chamada ao Opus 5, dezenas de segundos) deixava a tela parada e
muda: o dono não sabia se tinha clicado, se travou, ou se devia clicar de novo — e
clicar de novo disparava um segundo envio.

O indicador é GLOBAL e delegado (um listener no `base.html`), não uma macro repetida
em cada form: os 38 botões de escrita têm formatos diferentes (ícone puro, texto,
classes próprias) e uma macro exigiria reescrever todos, com o agravante de que o
próximo form escrito por alguém distraído nasceria sem indicador. O que estes testes
protegem é justamente a condição para o listener funcionar em toda parte.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_TEMPLATES = Path(__file__).resolve().parents[2] / "kubo" / "api" / "templates"

# Um form por página é ação de LEITURA (busca/filtro), não de escrita: barrá-los
# junto não protegeria nada e só empurraria ruído visual pro usuário.
_FORM_RE = re.compile(r"<form\b([^>]*method=\"post\"[^>]*)>(.*?)</form>", re.DOTALL | re.IGNORECASE)
_ID_RE = re.compile(r"id=\"([^\"]+)\"")


def _writing_forms() -> list[tuple[str, str, str, str]]:
    """(file, attrs, body, whole) de cada form method=post."""
    found: list[tuple[str, str, str, str]] = []
    for path in sorted(_TEMPLATES.rglob("*.html")):
        whole = path.read_text(encoding="utf-8")
        for attrs, body in _FORM_RE.findall(whole):
            found.append((str(path.relative_to(_TEMPLATES)), attrs, body, whole))
    return found


def _has_submit(attrs: str, body: str, whole: str) -> bool:
    """O form dispara `submit` por um controle de verdade?

    Duas formas legítimas no Kubo hoje: um `<button>` dentro do form (submit é o
    default do elemento, com ou sem `type`) ou um botão FORA dele apontando por
    `form="<id>"` — usado nos diálogos de gate, onde o botão fica na barra de ação.
    Ambas disparam o evento `submit`, que é onde o indicador global se prende."""
    if "<button" in body:
        return True
    ident = _ID_RE.search(attrs)
    return bool(ident) and f'form="{ident.group(1)}"' in whole


def test_writing_forms_exist_to_protect() -> None:
    """Sanidade do próprio varredor: se ele parar de achar forms, os testes abaixo
    passariam vazios e o gate viraria enfeite."""
    assert len(_writing_forms()) >= 30


def test_writing_forms_have_a_real_submit() -> None:
    """Cada form de escrita dispara `submit` por um controle de verdade.

    Um form cuja ação sai por link ou por JS avulso escaparia do indicador em
    silêncio: a tela voltaria a ficar parada e muda exatamente no caso que este
    ticket existe para corrigir. Este é o teste que faz a correção durar."""
    without_submit = [
        file for file, attrs, body, whole in _writing_forms() if not _has_submit(attrs, body, whole)
    ]

    assert not without_submit, (
        f"forms de escrita sem submit (ficam sem indicador): {without_submit}"
    )


def test_base_includes_global_indicator(authed_client: TestClient) -> None:
    """Qualquer página autenticada traz o indicador — ele vive no `base.html`."""
    html = authed_client.get("/sources").text

    assert "data-busy" in html, "o marcador do indicador global não chegou na página"
    assert "aria-busy" in html, "o indicador precisa expor aria-busy"
    assert "aria-live" in html, "o indicador precisa expor aria-live"


def test_script_guards_against_double_submit(client: TestClient) -> None:
    """O script evita o segundo envio enquanto o primeiro está em curso.

    Sem isso o dono clica de novo achando que não pegou — e o Kubo processa um segundo
    upload de 39 MB, ou uma segunda chamada paga ao Opus 5."""
    html = client.get("/login").text

    assert "dataset.submitting" in html, "o form precisa marcar que já foi enviado"
    assert "ev.preventDefault()" in html, "o segundo submit precisa ser cancelado"
    assert "target.disabled = true" in html, "o controle desabilita após o submit"


def test_login_also_has_indicator(client: TestClient) -> None:
    """A tela de login não é autenticada, mas o scrypt leva ~1s e o form é POST."""
    html = client.get("/login").text

    assert "data-busy" in html


def test_htmx_buttons_are_covered_by_busy_indicator(client: TestClient) -> None:
    """Botões soltos com `hx-post` (ex.: testar feed, gerar convite) não disparam
    `submit` de form. O listener global precisa escutar os eventos deles e encadear
    para os helpers de aplicar/limpar o busy state."""
    html = client.get("/login").text

    # Listeners HTMX existem e encadeiam para os helpers — não basta o nome do
    # evento estar no HTML, ele precisa chamar quem aplica/limpa o estado.
    assert "htmx:beforeRequest" in html, "requisições HTMX não têm listener de início"
    assert "htmx:afterRequest" in html, "requisições HTMX não têm listener de fim"
    assert "htmx:sendError" in html, "erro de envio HTMX precisa limpar o busy"
    assert "_kuboApplyBusy" in html, "beforeRequest precisa chamar _kuboApplyBusy"
    assert "_kuboClearBusy" in html, "afterRequest/sendError precisa chamar _kuboClearBusy"
    # Anti-duplo-envio: beforeRequest cancela se já está busy.
    assert re.search(
        r"htmx:beforeRequest.*?\.dataset\.busy.*?ev\.preventDefault", html, re.DOTALL
    ), "beforeRequest precisa cancelar requisição se o botão já está busy"


def test_firebase_login_buttons_have_busy_state(client: TestClient) -> None:
    """Login Google/GitHub é `fetch` manual e pode levar. Os botões devem marcar
    e desmarcar `data-busy` durante a chamada, inclusive em falha HTTP."""
    html = client.get("/login").text

    assert "btn-login-google" in html, "o botão do Google precisa existir no teste"
    assert "btn-login-github" in html, "o botão do GitHub precisa existir no teste"
    # Rótulos wrapped em <span> para o CSS [data-busy] > * esconder o texto.
    assert "<span>Entrar com Google</span>" in html, "rótulo do Google precisa estar em <span>"
    assert "<span>Entrar com GitHub</span>" in html, "rótulo do GitHub precisa estar em <span>"
    # setBusy é chamado antes do try; clearBusy no catch (cobre popup, fetch e HTTP).
    assert "setBusy(btn)" in html, "signIn precisa chamar setBusy antes da chamada"
    assert "clearBusy(btn)" in html, "signIn precisa chamar clearBusy no catch"
    # Falha HTTP (resp.ok false) precisa propagar para o catch limpar o busy.
    assert re.search(r"resp\.ok.*?throw", html, re.DOTALL), (
        "sendIdToken precisa lançar em !resp.ok para o catch de signIn limpar o busy"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
