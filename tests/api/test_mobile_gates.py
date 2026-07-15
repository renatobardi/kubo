"""Gates no celular (0019 marco 19.3, 'nunca cortável'): o board mostra o título real
do flow (não o rótulo genérico 'Fluxos') e um chevron-voltar no header mobile; o
GateSheet vira full-screen em mobile (sem bottom-sheet arrastável, C2 do plano)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tests.api.conftest import _mobile_header_block
from tests.api.test_flows import _BOARD, _GATE


def test_board_mobile_header_shows_flow_question_not_generic_label(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """O header mobile do board mostra a pergunta do flow (mobile_title), não o rótulo
    genérico 'Fluxos' que o crumb resolveria por default."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _GATE)
    html = authed_client.get("/flows/f1").text
    header = _mobile_header_block(html)
    assert "O que dizem sobre memória?" in header
    assert ">Fluxos<" not in header


def test_board_mobile_header_has_back_chevron_to_flows_list(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """O header mobile do board tem chevron-voltar pra /flows (mobile_back_href)."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _GATE)
    html = authed_client.get("/flows/f1").text
    header = _mobile_header_block(html)
    assert 'href="/flows"' in header


def test_board_inline_back_link_hidden_on_mobile(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """O link '← Fluxos' já existente no conteúdo do board (desktop) some em mobile —
    o chevron do shell (acima) já cobre esse papel; sem duplicar."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _GATE)
    html = authed_client.get("/flows/f1").text
    inline_tag_start = html.find('href="/flows" class="mt-1')
    assert inline_tag_start != -1, "link inline '← Fluxos' não encontrado no board"
    inline_tag = html[inline_tag_start : html.find(">", inline_tag_start)]
    assert "hidden" in inline_tag and "md:inline-flex" in inline_tag


def test_gatesheet_is_full_screen_on_mobile(
    monkeypatch: pytest.MonkeyPatch, authed_client: TestClient
) -> None:
    """O GateSheet (dialog nativo) é full-screen em mobile (w-full/h-dvh, default) e só
    vira o painel de 440px docado à direita a partir de md (C2: sem bottom-sheet
    arrastável, não é gesto novo — só a moldura muda por breakpoint)."""
    monkeypatch.setattr("kubo.api.routes.flows.flow_board", lambda db, f: _BOARD)
    monkeypatch.setattr("kubo.api.routes.flows.read_gate_context", lambda db, t: _GATE)
    html = authed_client.get("/flows/f1").text
    dialog_start = html.find("<dialog")
    dialog_tag = html[dialog_start : html.find(">", dialog_start)]
    assert "w-full" in dialog_tag
    assert "h-dvh" in dialog_tag
    assert "md:w-[440px]" in dialog_tag
    assert "md:ml-auto" in dialog_tag


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
