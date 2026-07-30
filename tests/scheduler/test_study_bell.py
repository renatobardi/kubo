"""Sino do dia (ADR-0043, KUBO-138): o aviso da manhã e a cutucada de atraso.

Unit puro — sem SurrealDB, sem BlockingScheduler e SEM TELEGRAM: o alvo é a FUNÇÃO
`send_daily_study_bell`, no molde de `test_study_job.py` (fakes de db e store). O
notificador entra pela costura `notifier`, então nenhum teste toca a rede.

O relógio é EXPLÍCITO (`now` por parâmetro) e a timezone vem do env: o "dia de estudo"
é o dia LOCAL do dono, não o do container (UTC). Sem os dois, o teste viraria
bomba-relógio.

O que estes testes protegem: silêncio é comportamento. Notificar sem lição, notificar
plano pausado ou mandar sino E cutucada no mesmo dia treina o dono a ignorar o bot — e
aí o módulo inteiro perde a função.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from surrealdb import RecordID

from kubo.store.study import Lesson, PlanEntry, StudyPlan

_TENANT = RecordID("tenant", "t1")
_USER = RecordID("user", "u1")
_TZ = "America/Sao_Paulo"
_BASE_URL = "https://kubo.example"
_WEEK = ["mon", "tue", "wed", "thu", "fri"]

# Sexta-feira, 07:00 em São Paulo — o sino toca de manhã (a véspera é que roda à noite).
_NOW = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
# Sábado 08/08: fora da cadência seg-sex.
_WEEKEND = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)
# Meia-noite local de hoje — a chave de `scheduled_for` da lição do dia.
_TODAY = datetime.combine(datetime(2026, 8, 7).date(), time.min, tzinfo=ZoneInfo(_TZ))
# Plano ativado na segunda daquela semana: sex já é o 5º dia de estudo.
_ACTIVATED = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _plan(key: str = "p1", *, status: str = "active", paused_days: int = 0) -> StudyPlan:
    return StudyPlan(
        id=RecordID("study_plan", key),
        tenant_id=_TENANT,
        user_id=_USER,
        topic=RecordID("topic", key),
        status=status,
        weekdays=_WEEK,
        target_date=datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc),
        activated_at=_ACTIVATED,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        paused_at=None,
        paused_days=paused_days,
    )


def _entries(count: int = 10, plan_key: str = "p1") -> list[PlanEntry]:
    """As lições PLANEJADAS — o `total` da régua sai daqui, não das lições geradas."""
    return [
        PlanEntry(
            id=RecordID("plan_entry", f"{plan_key}-e{i}"),
            study_plan=RecordID("study_plan", plan_key),
            seq=i,
            title=f"Aula {i}",
            chapters=[RecordID("material_chapter", f"c{i}")],
        )
        for i in range(1, count + 1)
    ]


def _lesson(plan_key: str = "p1", *, day: datetime = _TODAY) -> Lesson:
    return Lesson(
        id=RecordID("lesson", f"{plan_key}-hoje"),
        tenant_id=_TENANT,
        user_id=_USER,
        study_plan=RecordID("study_plan", plan_key),
        plan_entry=RecordID("plan_entry", f"{plan_key}-e1"),
        scheduled_for=day,
        concept="O conceito.",
        scenario="O cenário.",
        application="A aplicação.",
        recap=None,
        quiz=[],
        created_at=_NOW,
    )


def _completions(count: int) -> list[datetime]:
    """`count` conclusões, uma por dia de estudo desde a ativação (seg 03/08 em diante)."""
    days = [3, 4, 5, 6, 7]
    return [
        datetime.combine(datetime(2026, 8, day).date(), time.min, tzinfo=ZoneInfo(_TZ))
        for day in days[:count]
    ]


class _Spy:
    """Espiã de função de store: registra os kwargs e devolve o resultado combinado."""

    def __init__(self, result: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result

    def __call__(self, db: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if callable(self.result):
            return self.result(kwargs)
        return self.result


class _FakeNotifier:
    """Fake do envio: guarda o texto de cada mensagem (ou explode, se `fails`)."""

    def __init__(self, *, fails: bool = False) -> None:
        self.messages: list[str] = []
        self._fails = fails

    def __call__(self, text: str) -> None:
        self.messages.append(text)
        if self._fails:
            raise RuntimeError("telegram fora do ar")


@pytest.fixture(autouse=True)
def bell_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timezone do dono + base dos links: sem elas o job falharia por config, não por
    comportamento — e o RED apontaria para o lugar errado."""
    monkeypatch.setenv("TZ", _TZ)
    monkeypatch.setenv("KUBO_BASE_URL", _BASE_URL)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Spy]:
    """Store inteira em espiãs: um plano ativo em dia, com lição de hoje gerada.

    Cada teste desloca UMA peça da cena (a lição some, o atraso cresce, o plano acaba)
    — o que sobra é o comportamento que ele afirma.
    """
    spies = {
        "list_active_plans": _Spy([_plan()]),
        "get_lesson_for_day": _Spy(_lesson()),
        "list_plan_entries": _Spy(_entries()),
        "count_completed_lessons": _Spy(5),
        "completion_dates": _Spy(_completions(5)),
        "complete_plan": _Spy(),
        "get_topic": _Spy(
            type("T", (), {"id": RecordID("topic", "p1"), "title": "Manual de Kubo"})()
        ),
    }
    for name, spy in spies.items():
        monkeypatch.setattr(f"kubo.store.study.{name}", spy)
    return spies


def _run(notifier: _FakeNotifier, *, now: datetime = _NOW) -> list[str]:
    from kubo.scheduler.study import send_daily_study_bell

    return send_daily_study_bell(
        object(),
        tenant_id=_TENANT,
        user_id=_USER,
        now=now,
        notifier=notifier,
    )


def test_bell_announces_todays_lesson_with_a_link(store: dict[str, _Spy]) -> None:
    """Dia de estudo com lição gerada: uma mensagem, com o tema e o link da lição.

    O link é o ponto do sino — avisar sem dizer ONDE estudar empurra o dono a procurar
    a lição no menu.
    """
    notifier = _FakeNotifier()

    sent = _run(notifier)

    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert "Manual de Kubo" in message
    assert f"{_BASE_URL}/study/lessons/p1-hoje" in message
    # O retorno é o que SAIU de fato — sem isto, um job que devolve sempre `[]`
    # satisfaria todos os testes de silêncio deste arquivo.
    assert sent == notifier.messages


def test_no_lesson_for_today_means_silence(store: dict[str, _Spy]) -> None:
    """Sem lição gerada não há o que anunciar — nada é enviado "por via das dúvidas"."""
    store["get_lesson_for_day"].result = None
    store["count_completed_lessons"].result = 2
    store["completion_dates"].result = _completions(2)
    notifier = _FakeNotifier()

    assert _run(notifier) == []
    assert notifier.messages == []


def test_outside_a_study_day_means_silence(store: dict[str, _Spy]) -> None:
    """Sábado com cadência seg-sex: o sino não toca em dia que não é de estudo."""
    notifier = _FakeNotifier()

    assert _run(notifier, now=_WEEKEND) == []
    assert notifier.messages == []


def test_paused_plan_is_never_announced(store: dict[str, _Spy]) -> None:
    """Plano pausado é silêncio total: nem sino, nem cutucada.

    Pausar é o pedido explícito de parar de ser cobrado; notificar mesmo assim seria
    ignorar o único controle que o dono tem sobre o ritmo. O sino pergunta por planos
    ATIVOS (de onde o pausado já saiu) — uma varredura própria e mais larga aqui
    reabriria a cobrança pelas costas da pausa.
    """
    store["list_active_plans"].result = []  # pausado não é ativo: some da lista
    notifier = _FakeNotifier()

    assert _run(notifier) == []
    assert notifier.messages == []
    assert len(store["list_active_plans"].calls) == 1
    assert store["get_lesson_for_day"].calls == []


def test_two_lessons_behind_replaces_the_bell_with_a_nudge(store: dict[str, _Spy]) -> None:
    """Atraso de 2+ lições vira cutucada COM a data projetada — e substitui o sino.

    UMA mensagem por plano por dia: mandar sino e cutucada seria o dobro de ruído pelo
    mesmo assunto, e o dono aprenderia a ignorar os dois.
    """
    store["count_completed_lessons"].result = 2
    store["completion_dates"].result = _completions(2)
    notifier = _FakeNotifier()

    _run(notifier)

    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert "3" in message  # 5 esperadas - 2 concluídas
    assert "04/09" in message or "2026-09-04" in message  # a data projetada pelo ritmo real


def test_one_lesson_behind_still_rings_the_normal_bell(store: dict[str, _Spy]) -> None:
    """Uma lição de atraso é vida normal: o sino do dia, sem cobrança."""
    store["count_completed_lessons"].result = 4
    store["completion_dates"].result = _completions(4)
    notifier = _FakeNotifier()

    _run(notifier)

    assert len(notifier.messages) == 1
    assert f"{_BASE_URL}/study/lessons/p1-hoje" in notifier.messages[0]


def test_finished_plan_is_completed_and_congratulated(store: dict[str, _Spy]) -> None:
    """Todas as lições estudadas: o plano vira `completed` e o dono recebe os parabéns.

    A cena é a REAL: no dia seguinte à última lição a véspera não gerou nada
    (`get_lesson_for_day` é None) e todas as planejadas já têm registro. Se a checagem
    de completude ficasse atrás do guard de "existe lição hoje", o plano nunca fecharia.
    """
    store["get_lesson_for_day"].result = None
    store["list_plan_entries"].result = _entries(5)
    store["count_completed_lessons"].result = 5
    store["completion_dates"].result = _completions(5)
    notifier = _FakeNotifier()

    _run(notifier)

    assert len(store["complete_plan"].calls) == 1
    assert store["complete_plan"].calls[0]["plan_id"] == _plan().id
    assert len(notifier.messages) == 1
    assert "Manual de Kubo" in notifier.messages[0]


def test_unfinished_plan_is_never_completed(store: dict[str, _Spy]) -> None:
    """Plano em andamento não fecha: o sino do dia não pode encerrar o que falta estudar."""
    notifier = _FakeNotifier()

    _run(notifier)

    assert store["complete_plan"].calls == []


def test_notifier_failure_does_not_raise(store: dict[str, _Spy]) -> None:
    """Telegram fora do ar não derruba o job — o log registra e o dia segue.

    Explodir aqui mataria o cron e o dono perderia os avisos dos dias seguintes por
    causa de uma indisponibilidade de minutos.
    """
    notifier = _FakeNotifier(fails=True)

    assert _run(notifier) == []


def test_a_failing_notification_does_not_block_the_other_plans(
    store: dict[str, _Spy],
) -> None:
    """Um plano com envio falho não condena os demais: o job segue para o próximo."""
    store["list_active_plans"].result = [_plan("p1"), _plan("p2")]
    store["get_lesson_for_day"].result = lambda kw: _lesson(str(kw["plan_id"].id))
    notifier = _FakeNotifier(fails=True)

    _run(notifier)

    assert len(notifier.messages) == 2


def test_one_message_per_active_plan(store: dict[str, _Spy]) -> None:
    """Dois planos ativos, duas mensagens — uma por plano, nenhuma a mais."""
    store["list_active_plans"].result = [_plan("p1"), _plan("p2")]
    store["get_lesson_for_day"].result = lambda kw: _lesson(str(kw["plan_id"].id))
    notifier = _FakeNotifier()

    _run(notifier)

    assert len(notifier.messages) == 2
    assert f"{_BASE_URL}/study/lessons/p2-hoje" in notifier.messages[1]
