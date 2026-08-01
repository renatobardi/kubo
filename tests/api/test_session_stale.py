"""Sessão presente mas irresolvível não pode trancar o usuário (KUBO-140).

O cookie existe e o papel é válido, então o `RequireLoginMiddleware` deixa passar;
mas o usuário/membership não existe mais no banco (sessão anterior ao multi-tenant,
banco reprovisionado, identidade resetada) e `resolve_session` devolve None. Antes
deste comportamento, TODA página respondia `403 Acesso negado.` em texto puro — e
como `/logout` é POST-only e o middleware só redireciona quem NÃO tem sessão, não
sobrava nem tela de login nem botão de sair. Único escape era limpar cookie na mão
(aconteceu 2x com o dono no incidente de 2026-07-30, em DEV e PRD).

O que se prova aqui é a saída: limpar a sessão e mandar pro login. O 403 continua
certo para recurso de OUTRO tenant com sessão válida — isso é o
`tests/api/test_study_membership.py`, não este arquivo.
"""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

# Páginas tenant-scoped de módulos diferentes: o comportamento é do resolvedor de
# sessão, não de uma rota específica. Se alguém "consertar" só a que apareceu no
# incidente, as outras aqui denunciam.
_PAGES = ("/distilled", "/study/topics", "/sources")


@pytest.fixture
def orphan_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """O usuário da sessão sumiu do banco — cookie válido apontando pro vazio.

    A conftest troca `resolve_session` por um dublê em cada módulo de rota, para que
    os testes de tela não precisem montar identidade. Aqui o alvo do teste é
    justamente o resolvedor de verdade, então ele é devolvido ao lugar ANTES de o
    usuário ser apagado — senão o que se mediria era o dublê, não o comportamento."""
    from kubo.api.session import resolve_session as real_resolve_session

    for mod in ("distilled", "sources", "study"):
        monkeypatch.setattr(f"kubo.api.routes.{mod}.resolve_session", real_resolve_session)
    monkeypatch.setattr(
        "kubo.store.tenancy.get_user_by_firebase_uid", lambda db, firebase_uid: None
    )


@pytest.mark.parametrize("path", _PAGES)
def test_orphan_session_redirects_to_login(
    authed_client: TestClient, orphan_session: None, path: str
) -> None:
    """Sessão irresolvível → 303 para /login, nunca 403 de texto puro."""
    resp = authed_client.get(path, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_orphan_session_clears_cookie(authed_client: TestClient, orphan_session: None) -> None:
    """O redirect vem com a sessão LIMPA.

    Sem isso o usuário volta pro /login carregando a mesma sessão podre e roda em
    círculo — que é a versão elegante do mesmo tranco."""
    resp = authed_client.get("/distilled", follow_redirects=False)

    assert resp.status_code == 303
    assert "set-cookie" in resp.headers, "o redirect precisa reemitir/limpar o cookie de sessão"
    set_cookie = resp.headers["set-cookie"]
    assert "kubo_session=null" in set_cookie, "o redirect precisa limpar o cookie de sessão"
    assert "expires=Thu, 01 Jan 1970" in set_cookie, "o cookie limpo deve ter expiração no passado"


def test_valid_session_stays_intact(authed_client: TestClient) -> None:
    """Contraprova: com o usuário existindo, a página responde normalmente.

    Sem esta, um bug que redirecionasse TODO mundo pro login passaria nos testes
    acima com louvor."""
    resp = authed_client.get("/distilled", follow_redirects=False)

    assert resp.status_code == 200


def test_write_route_also_redirects(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Não é só GET: POST com sessão órfã também sai pelo login.

    Uma correção que só tratasse páginas de leitura deixaria o usuário vendo a tela
    de login e tomando 403 ao tentar agir.

    A sessão só apodrece DEPOIS de pegar o CSRF: é a ordem da vida real — o dono
    carregou a página com sessão boa, o banco foi reprovisionado enquanto ele lia, e
    o envio pega a sessão já sem lastro. Apodrecer antes só testaria o CSRF."""
    html = authed_client.get("/sources").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Fontes"

    from kubo.api.session import resolve_session as real_resolve_session

    monkeypatch.setattr("kubo.api.routes.sources.resolve_session", real_resolve_session)
    monkeypatch.setattr(
        "kubo.store.tenancy.get_user_by_firebase_uid", lambda db, firebase_uid: None
    )

    resp = authed_client.post(
        "/sources",
        data={"csrf": m.group(1), "kind": "rss", "url": "https://exemplo.test/feed.xml"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_login_explains_why_user_returned(client: TestClient) -> None:
    """A tela de login diz que a sessão expirou, em vez de aparecer do nada.

    Sem o aviso, o usuário que estava logado é cuspido no login sem explicação e
    conclui que o Kubo quebrou — o mesmo susto do 403, com roupa melhor."""
    html = client.get("/login?expired=1").text

    assert "sess" in html.lower()
    assert "expir" in html.lower()


def test_normal_login_shows_no_notice(client: TestClient) -> None:
    """Contraprova: quem chega ao login pelo caminho normal não vê aviso nenhum."""
    html = client.get("/login").text

    assert "expir" not in html.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
