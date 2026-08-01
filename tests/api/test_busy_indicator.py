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

    assert "data-submitting" in html, "o form precisa marcar que já foi enviado"
    assert "ev.preventDefault()" in html, "o segundo submit precisa ser cancelado"
    assert "target.disabled = true" in html, "o controle desabilita após o submit"


def test_login_also_has_indicator(client: TestClient) -> None:
    """A tela de login não é autenticada, mas o scrypt leva ~1s e o form é POST."""
    html = client.get("/login").text

    assert "data-busy" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
