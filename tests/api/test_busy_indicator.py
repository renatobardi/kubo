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


def _forms_de_escrita() -> list[tuple[str, str, str, str]]:
    """(arquivo, atributos, corpo, texto completo do arquivo) de cada form method=post."""
    achados: list[tuple[str, str, str, str]] = []
    for path in sorted(_TEMPLATES.rglob("*.html")):
        texto = path.read_text(encoding="utf-8")
        for attrs, corpo in _FORM_RE.findall(texto):
            achados.append((str(path.relative_to(_TEMPLATES)), attrs, corpo, texto))
    return achados


def _tem_submit(attrs: str, corpo: str, arquivo_inteiro: str) -> bool:
    """O form dispara `submit` por um controle de verdade?

    Duas formas legítimas no Kubo hoje: um `<button>` dentro do form (submit é o
    default do elemento, com ou sem `type`) ou um botão FORA dele apontando por
    `form="<id>"` — usado nos diálogos de gate, onde o botão fica na barra de ação.
    Ambas disparam o evento `submit`, que é onde o indicador global se prende."""
    if "<button" in corpo:
        return True
    ident = _ID_RE.search(attrs)
    return bool(ident) and f'form="{ident.group(1)}"' in arquivo_inteiro


def test_existem_forms_de_escrita_para_proteger() -> None:
    """Sanidade do próprio varredor: se ele parar de achar forms, os testes abaixo
    passariam vazios e o gate viraria enfeite."""
    assert len(_forms_de_escrita()) >= 30


def test_todo_form_de_escrita_tem_um_submit_de_verdade() -> None:
    """Cada form de escrita dispara `submit` por um controle de verdade.

    Um form cuja ação sai por link ou por JS avulso escaparia do indicador em
    silêncio: a tela voltaria a ficar parada e muda exatamente no caso que este
    ticket existe para corrigir. Este é o teste que faz a correção durar."""
    sem_submit = [
        arquivo
        for arquivo, attrs, corpo, inteiro in _forms_de_escrita()
        if not _tem_submit(attrs, corpo, inteiro)
    ]

    assert not sem_submit, f"forms de escrita sem submit (ficam sem indicador): {sem_submit}"


def test_base_carrega_o_indicador_global(authed_client: TestClient) -> None:
    """Qualquer página autenticada traz o indicador — ele vive no `base.html`."""
    html = authed_client.get("/sources").text

    assert "data-busy" in html, "o marcador do indicador global não chegou na página"


def test_indicador_desabilita_o_botao_para_impedir_envio_duplo(authed_client: TestClient) -> None:
    """O script precisa desabilitar o controle após o envio.

    Sem isso o dono clica de novo achando que não pegou — e o Kubo processa um segundo
    upload de 39 MB, ou uma segunda chamada paga ao Opus 5."""
    html = authed_client.get("/sources").text

    assert "disabled" in html and "submit" in html


def test_login_tambem_tem_indicador(client: TestClient) -> None:
    """A tela de login não é autenticada, mas o scrypt leva ~1s e o form é POST."""
    html = client.get("/login").text

    assert "data-busy" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
