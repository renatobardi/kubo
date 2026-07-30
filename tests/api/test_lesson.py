"""Tela da Lição (ADR-0043, KUBO-137): ler, responder o quiz e registrar o estudo.

Unit com a store mockada — a persistência é do `tests/store/test_lesson.py`
(integração). Aqui ficam a tríade de escrita (CSRF → sessão → PRG 303), a CORREÇÃO do
quiz (feita na rota, contra o quiz congelado da lição) e o que a tela mostra antes e
depois de o dono concluir.

Fixture de stub PRÓPRIA (molde de `stub_plan_store` em test_study_plan.py): as leituras
de Lição nascem vazias e cada teste monta a cena que afirma.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store.study import Lesson, StudyLog

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_PLAN_ID = RecordID("study_plan", "p1")
_ENTRY_ID = RecordID("plan_entry", "e1")
_LESSON_ID = RecordID("lesson", "l1")

_LESSON_URL = f"/study/lessons/{_LESSON_ID.id}"
_COMPLETE_URL = f"{_LESSON_URL}/complete"

_DAY = datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc)


def _quiz() -> list[dict[str, Any]]:
    """Quiz congelado da lição: a resposta certa da Q1 é o índice 0; a da Q2, o 1."""
    return [
        {
            "question": "O que é backpressure?",
            "options": ["Freio do consumidor", "Fila infinita", "Retry cego"],
            "answer_index": 0,
            "explanation": "O consumidor lento freia o produtor.",
        },
        {
            "question": "Quando particionar uma fila?",
            "options": ["Nunca", "Quando a ordem é por chave", "Sempre"],
            "answer_index": 1,
            "explanation": "Partição preserva ordem por chave.",
        },
    ]


def _lesson(*, recap: str | None = None) -> Lesson:
    return Lesson(
        id=_LESSON_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        study_plan=_PLAN_ID,
        plan_entry=_ENTRY_ID,
        scheduled_for=_DAY,
        concept="Filas desacoplam produtor e consumidor.",
        scenario="Um pico de tráfego numa madrugada de deploy.",
        application="Meça o lag antes de aumentar a concorrência.",
        recap=recap,
        quiz=_quiz(),
        created_at=datetime(2026, 8, 5, 21, 0, tzinfo=timezone.utc),
    )


def _log(*, correct_count: int = 1, reaction: str | None = "ok") -> StudyLog:
    return StudyLog(
        id=RecordID("study_log", "s1"),
        lesson=_LESSON_ID,
        answers=[0, 0],
        correct_count=correct_count,
        reaction=reaction,
        completed_at=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture(autouse=True)
def stub_lesson_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escrita sem banco real (o `connect_rw` do ADR-0018) + leituras da Lição.

    Sem isto a rota de escrita explodiria em ConfigError (não há senha rw no unit) e o
    teste falharia por 503 em vez de pelo comportamento que ele afirma.
    """
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_lesson", lambda db, **kw: _lesson())
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_log_for_lesson", lambda db, **kw: None
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.complete_lesson", lambda db, **kw: _log()
    )


def _spy(monkeypatch: pytest.MonkeyPatch, name: str, result: Any = None) -> list[dict[str, Any]]:
    """Substitui uma função da store por uma espiã que registra os kwargs recebidos."""
    calls: list[dict[str, Any]] = []

    def _record(db: Any, **kw: Any) -> Any:
        calls.append(kw)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(f"kubo.api.routes.study.study_store.{name}", _record)
    return calls


def _csrf(authed_client: TestClient) -> str:
    """Token CSRF da sessão, lido do form da lista de Materiais."""
    html = authed_client.get("/study/materials").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


def _answers(first: str = "0", second: str = "1") -> dict[str, str]:
    """Um campo por questão do quiz — o índice da alternativa marcada no rádio."""
    return {"answer_0": first, "answer_1": second}


# --- Leitura ---------------------------------------------------------------------------


def test_lesson_page_shows_the_blocks_and_the_quiz_form(authed_client: TestClient) -> None:
    """Lição pendente: os blocos + o quiz como formulário (um rádio por alternativa)."""
    html = authed_client.get(_LESSON_URL).text

    assert "Filas desacoplam produtor e consumidor." in html
    assert "Um pico de tráfego numa madrugada de deploy." in html
    assert "Meça o lag antes de aumentar a concorrência." in html
    assert "O que é backpressure?" in html
    assert 'name="answer_0"' in html
    assert 'name="answer_1"' in html
    assert _COMPLETE_URL in html


def test_pending_lesson_does_not_leak_the_answers(authed_client: TestClient) -> None:
    """Antes de responder, a explicação NÃO aparece — senão o quiz não mede nada."""
    html = authed_client.get(_LESSON_URL).text

    assert "Freio do consumidor" in html  # a alternativa aparece...
    assert "O consumidor lento freia o produtor." not in html  # ...a explicação, não


def test_lesson_page_shows_the_recap_when_there_is_one(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recapitulação do que o dono errou abre a lição."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_lesson",
        lambda db, **kw: _lesson(recap="Você errou backpressure ontem."),
    )

    html = authed_client.get(_LESSON_URL).text

    assert "Você errou backpressure ontem." in html


def test_completed_lesson_shows_the_correction_and_the_reaction(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Com registro, a tela vira correção: explicação por questão, reação e a conclusão."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_log_for_lesson", lambda db, **kw: _log()
    )

    html = authed_client.get(_LESSON_URL).text

    assert "O consumidor lento freia o produtor." in html
    assert "Partição preserva ordem por chave." in html
    assert 'name="answer_0"' not in html


def test_unknown_lesson_is_404(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lição inexistente (ou de outro usuário) é 404, não 500."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_lesson", lambda db, **kw: None)

    assert authed_client.get("/study/lessons/nao-existe").status_code == 404


def test_lesson_page_requires_auth(client: TestClient) -> None:
    """Sem sessão, a tela da lição redireciona pro login."""
    assert client.get(_LESSON_URL, follow_redirects=False).status_code == 303


# --- Conclusão -------------------------------------------------------------------------


def test_complete_corrects_against_the_frozen_quiz_and_records(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correção é da ROTA, contra o quiz da lição: o cliente manda respostas, não notas.

    Confiar num `correct_count` vindo do formulário deixaria o desempenho — que alimenta
    a recapitulação — nas mãos de quem responde.
    """
    calls = _spy(monkeypatch, "complete_lesson", _log())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        _COMPLETE_URL,
        data={"csrf": csrf, "reaction": "dificil", **_answers("0", "0")},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith(_LESSON_URL)
    assert len(calls) == 1
    assert list(calls[0]["answers"]) == [0, 0]
    assert calls[0]["correct_count"] == 1  # acertou a Q1 (índice 0), errou a Q2 (era 1)
    assert calls[0]["reaction"] == "dificil"
    assert calls[0]["lesson_id"] == _LESSON_ID


def test_complete_without_reaction_still_records(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reação é opcional — não marcar nada não impede concluir a lição."""
    calls = _spy(monkeypatch, "complete_lesson", _log(reaction=None))
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        _COMPLETE_URL, data={"csrf": csrf, **_answers()}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert calls[0]["reaction"] is None


def test_all_correct_answers_count_every_question(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gabarito inteiro: `correct_count` é o total de questões do quiz."""
    calls = _spy(monkeypatch, "complete_lesson", _log(correct_count=2))
    csrf = _csrf(authed_client)

    authed_client.post(
        _COMPLETE_URL, data={"csrf": csrf, **_answers("0", "1")}, follow_redirects=False
    )

    assert calls[0]["correct_count"] == 2


@pytest.mark.parametrize(
    ("data", "motivo"),
    [
        ({"answer_0": "9", "answer_1": "1"}, "índice fora das alternativas"),
        ({"answer_0": "-1", "answer_1": "1"}, "índice negativo"),
        ({"answer_0": "sim", "answer_1": "1"}, "índice não numérico"),
        ({"answer_0": "0"}, "questão sem resposta desloca a correção"),
    ],
)
def test_invalid_answers_are_refused_before_the_store(
    authed_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, str],
    motivo: str,
) -> None:
    """Resposta que não casa com o quiz é recusada na borda — nada é gravado.

    Uma questão em branco é o caso perigoso: aceitá-la deslocaria todas as respostas
    seguintes e gravaria uma correção silenciosamente errada.
    """
    calls = _spy(monkeypatch, "complete_lesson", _log())
    csrf = _csrf(authed_client)

    resp = authed_client.post(_COMPLETE_URL, data={"csrf": csrf, **data}, follow_redirects=False)

    assert resp.status_code == 400, motivo
    assert calls == []


def test_second_completion_is_refused_with_409(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concluir a mesma lição de novo é recusado com motivo legível, não 500."""
    _spy(monkeypatch, "complete_lesson", StoreError("lição já concluída"))
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        _COMPLETE_URL, data={"csrf": csrf, **_answers()}, follow_redirects=False
    )

    assert resp.status_code == 409


def test_complete_requires_csrf(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem CSRF é 403 e NÃO chega à store."""
    calls = _spy(monkeypatch, "complete_lesson", _log())

    resp = authed_client.post(_COMPLETE_URL, data=_answers(), follow_redirects=False)

    assert resp.status_code == 403
    assert calls == []


def test_complete_on_unknown_lesson_is_404(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem lição não há quiz para corrigir — 404 antes de qualquer escrita."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_lesson", lambda db, **kw: None)
    calls = _spy(monkeypatch, "complete_lesson", _log())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/study/lessons/nao-existe/complete",
        data={"csrf": csrf, **_answers()},
        follow_redirects=False,
    )

    assert resp.status_code == 404
    assert calls == []
