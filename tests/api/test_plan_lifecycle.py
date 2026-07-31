"""Pausar/retomar e o painel de progresso (ADR-0043, KUBO-138).

Unit com a store mockada — a persistência é de `tests/store/test_plan_lifecycle.py`
(integração). Aqui ficam a tríade de escrita (CSRF → sessão → PRG 303), as recusas por
estado errado e o que o painel do tema MOSTRA.

Fixture de stub PRÓPRIA (molde de `stub_plan_store` em test_study_plan.py): a cena
nasce com um plano ATIVO, e cada teste desloca uma peça.

Nenhum número derivado do RELÓGIO é afirmado aqui (o precedente de test_study_plan.py):
esperado, atraso, streak e data projetada dependem de "hoje" e são comportamento de
`tests/study/test_progress.py`. O que a rota deve provar é o que vem dos stubs —
concluídas e planejadas — e o SHAPE do que ela manda para a store.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.errors import StoreError
from kubo.store.study import PlanEntry, StudyPlan, Topic

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_MATERIAL_ID = RecordID("material", "abc123")
_TOPIC_ID = RecordID("topic", "t1")
_PLAN_ID = RecordID("study_plan", "p1")
_WEEK = ["mon", "tue", "wed", "thu", "fri"]

_TOPIC_URL = f"/study/topics/{_TOPIC_ID.id}"
_PAUSE_URL = f"{_TOPIC_URL}/plan/pause"
_RESUME_URL = f"{_TOPIC_URL}/plan/resume"

_TOTAL = 10
_DONE = 4


def _topic() -> Topic:
    return Topic(
        id=_TOPIC_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        material=_MATERIAL_ID,
        title="Manual de Kubo",
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def _plan(status: str = "active", *, paused_days: int = 0) -> StudyPlan:
    return StudyPlan(
        id=_PLAN_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        status=status,
        weekdays=_WEEK,
        target_date=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc),
        activated_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        paused_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc) if status == "paused" else None,
        paused_days=paused_days,
    )


def _entries(count: int = _TOTAL) -> list[PlanEntry]:
    return [
        PlanEntry(
            id=RecordID("plan_entry", f"e{i}"),
            study_plan=_PLAN_ID,
            seq=i,
            title=f"Aula {i}",
            chapters=[RecordID("material_chapter", f"c{i}")],
        )
        for i in range(1, count + 1)
    ]


def _completions(count: int) -> list[datetime]:
    """`count` registros de estudo, um por dia desde a ativação.

    O painel deriva o concluído DESTAS datas (é delas que saem streak e ritmo), então
    stubar uma contagem que não bate com elas montaria uma cena impossível — "4 de 10"
    ao lado de um atraso calculado sobre zero conclusões.
    """
    return [datetime(2026, 8, 3 + i, 12, 0, tzinfo=timezone.utc) for i in range(count)]


@pytest.fixture(autouse=True)
def stub_lifecycle_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cena base: tema com plano ATIVO, 10 lições planejadas, 4 estudadas.

    O `connect_rw` falso vem junto (ADR-0018): sem ele a rota de escrita explodiria em
    ConfigError e o teste falharia por 503 em vez do comportamento que afirma.
    """
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: _plan()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_plan_entries", lambda db, **kw: _entries()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.count_completed_lessons", lambda db, **kw: _DONE
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.completion_dates", lambda db, **kw: _completions(_DONE)
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.pause_plan", lambda db, **kw: _plan())
    monkeypatch.setattr("kubo.api.routes.study.study_store.resume_plan", lambda db, **kw: _plan())
    # Leituras de Lição da tela do tema — vazias: o comportamento delas é de test_lesson.py.
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_lesson_for_day", lambda db, **kw: None
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_lessons", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.count_lessons", lambda db, **kw: 0)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_log_for_lesson", lambda db, **kw: None
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


# --- Painel de progresso ---------------------------------------------------------------


def test_topic_shows_completed_over_planned(authed_client: TestClient) -> None:
    """O painel mostra o progresso REAL contra o plano aprovado: 4 de 10.

    A asserção é na string COMPOSTA de propósito: procurar "4" solto no HTML passaria
    por qualquer número da página e o teste não mediria nada.
    """
    html = authed_client.get(_TOPIC_URL).text

    assert f"{_DONE} de {_TOTAL}" in html


def test_topic_progress_comes_from_the_study_logs_not_the_generated_lessons(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concluído é o que foi ESTUDADO: a rota pergunta pelo plano certo.

    Contar lição gerada (a véspera gera sozinha) mostraria progresso que o dono não fez.
    O numerador sai das MESMAS datas que alimentam streak e ritmo — dois leitores
    diferentes para o mesmo número renderiam um painel que se contradiz.
    """
    calls = _spy(monkeypatch, "completion_dates", _completions(7))

    html = authed_client.get(_TOPIC_URL).text

    assert f"7 de {_TOTAL}" in html
    assert calls[0]["plan_id"] == _PLAN_ID
    assert calls[0]["tenant_id"] == _TENANT
    assert calls[0]["user_id"] == _USER


def test_paused_plan_shows_the_badge_and_the_resume_action(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plano pausado se anuncia como pausado e oferece Retomar (não Pausar).

    Sem o aviso, o dono acharia que o plano parou de gerar lição por defeito.
    """
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: _plan("paused")
    )

    html = authed_client.get(_TOPIC_URL).text

    assert "ausado" in html  # "Pausado"/"pausado" — o badge de estado
    assert _RESUME_URL in html
    assert _PAUSE_URL not in html


def test_active_plan_offers_the_pause_action(authed_client: TestClient) -> None:
    """Plano ativo oferece Pausar — o único controle do dono sobre o ritmo."""
    html = authed_client.get(_TOPIC_URL).text

    assert _PAUSE_URL in html
    assert _RESUME_URL not in html


def test_progress_panel_exists_only_after_activation(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A régua nasce na ATIVAÇÃO: o plano ativo tem painel, o proposto não.

    Um plano proposto não tem `activated_at`, e derivar progresso a partir do nada
    inventaria atraso para quem ainda está curando o plano. As duas metades no mesmo
    teste de propósito: "não aparece" sozinho passaria mesmo sem o painel existir.
    """
    active = authed_client.get(_TOPIC_URL).text
    assert f"{_DONE} de {_TOTAL}" in active
    assert _PAUSE_URL in active

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: _plan("proposed")
    )
    calls = _spy(monkeypatch, "completion_dates", [])

    proposed = authed_client.get(_TOPIC_URL).text

    assert f"{_DONE} de {_TOTAL}" not in proposed
    assert _PAUSE_URL not in proposed
    assert calls == []


# --- Pausar ----------------------------------------------------------------------------


def test_pause_calls_the_store_and_redirects(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pausar grava e volta para o tema (PRG): recarregar não pausa de novo."""
    calls = _spy(monkeypatch, "pause_plan", _plan("paused"))
    csrf = _csrf(authed_client)

    resp = authed_client.post(_PAUSE_URL, data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith(_TOPIC_URL)
    assert len(calls) == 1
    assert calls[0]["plan_id"] == _PLAN_ID
    assert calls[0]["tenant_id"] == _TENANT
    assert calls[0]["user_id"] == _USER


def test_pause_requires_csrf(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem CSRF é 403 e NÃO chega à store — pausar é escrita."""
    calls = _spy(monkeypatch, "pause_plan", _plan("paused"))

    resp = authed_client.post(_PAUSE_URL, follow_redirects=False)

    assert resp.status_code == 403
    assert calls == []


def test_pause_on_a_topic_without_plan_is_404(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem plano não há o que pausar — 404 antes de qualquer escrita."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: None
    )
    calls = _spy(monkeypatch, "pause_plan", _plan("paused"))
    csrf = _csrf(authed_client)

    resp = authed_client.post(_PAUSE_URL, data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 404
    assert calls == []


def test_pause_on_an_unknown_topic_is_404(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema de outro usuário é INEXISTENTE, não "negado"."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)
    csrf = _csrf(authed_client)

    resp = authed_client.post(_PAUSE_URL, data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 404


def test_pause_refused_by_state_is_409(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pausar o que não está ativo é recusa legível (409), não 500."""
    _spy(monkeypatch, "pause_plan", StoreError("plano não está ativo"))
    csrf = _csrf(authed_client)

    resp = authed_client.post(_PAUSE_URL, data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 409


# --- Retomar ---------------------------------------------------------------------------


def test_resume_sends_the_frozen_days_and_a_new_target(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retomar soma os dias congelados e grava uma data-alvo recalculada.

    O VALOR de `paused_days_add` depende de hoje (é de `test_progress.py`); o que a rota
    prova aqui é o SHAPE: os dias vão junto e não são negativos, e o alvo é recalculado
    em vez de herdar o alvo antigo, que já passou enquanto o plano estava parado.
    """
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: _plan("paused")
    )
    calls = _spy(monkeypatch, "resume_plan", _plan())
    csrf = _csrf(authed_client)

    resp = authed_client.post(_RESUME_URL, data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["plan_id"] == _PLAN_ID
    assert calls[0]["paused_days_add"] >= 0
    assert calls[0]["new_target"] is not None


def test_resume_requires_csrf(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem CSRF é 403 e NÃO chega à store."""
    calls = _spy(monkeypatch, "resume_plan", _plan())

    resp = authed_client.post(_RESUME_URL, follow_redirects=False)

    assert resp.status_code == 403
    assert calls == []


def test_resume_refused_by_state_is_409(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retomar um plano que nunca parou é recusa legível (409), não 500."""
    _spy(monkeypatch, "resume_plan", StoreError("plano não está pausado"))
    csrf = _csrf(authed_client)

    resp = authed_client.post(_RESUME_URL, data={"csrf": csrf}, follow_redirects=False)

    assert resp.status_code == 409
