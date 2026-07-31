"""Telas de Tema e Plano (ADR-0043, KUBO-136): propor, revisar e ativar.

Unit com a store e o planner mockados — a persistência é do
`tests/store/test_study_plan.py` (integração). Aqui ficam a tríade de escrita
(CSRF → sessão → PRG 303), a COSTURA do planner (`build_planner` trocado por um fake:
nenhum teste toca LiteLLM) e o que cada tela mostra por status do plano.

A data-alvo concreta NÃO é afirmada aqui: ela depende de "hoje" e é comportamento de
`tests/study/test_planning.py`. Um teste de rota que fixasse a data viraria bomba-relógio.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from starlette.testclient import TestClient
from surrealdb import RecordID

from kubo.store.study import (
    Material,
    MaterialChapter,
    PlanEntry,
    PlanEntryInput,
    StudyPlan,
    Topic,
)
from kubo.study.planner import PlanLesson, PlanProposal

_TENANT = RecordID("tenant", "breakglass")
_USER = RecordID("user", "breakglass-owner")
_MATERIAL_ID = RecordID("material", "abc123")
_TOPIC_ID = RecordID("topic", "t1")
_PLAN_ID = RecordID("study_plan", "p1")
_WEEK = ["mon", "tue", "wed", "thu", "fri"]

_TOPIC_URL = f"/study/topics/{_TOPIC_ID.id}"


def _material() -> Material:
    return Material(
        id=_MATERIAL_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        title="Manual de Kubo",
        fmt="epub",
        original_filename="manual.epub",
        file_path="/data/materials/t/u/abc123.epub",
        size_bytes=1024,
        chapter_count=3,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def _chapters(count: int = 3) -> list[MaterialChapter]:
    return [
        MaterialChapter(
            id=RecordID("material_chapter", f"c{i}"),
            material=_MATERIAL_ID,
            seq=i,
            title=f"Capítulo {i}",
            part=None,
            content=f"Conteúdo {i}.",
        )
        for i in range(1, count + 1)
    ]


def _topic() -> Topic:
    return Topic(
        id=_TOPIC_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        material=_MATERIAL_ID,
        title="Manual de Kubo",
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def _plan(status: str = "proposed", *, activated: bool = False) -> StudyPlan:
    return StudyPlan(
        id=_PLAN_ID,
        tenant_id=_TENANT,
        user_id=_USER,
        topic=_TOPIC_ID,
        status=status,
        weekdays=_WEEK,
        target_date=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        activated_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc) if activated else None,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def _entries() -> list[PlanEntry]:
    return [
        PlanEntry(
            id=RecordID("plan_entry", f"e{i}"),
            study_plan=_PLAN_ID,
            seq=i,
            title=f"Aula {i}",
            chapters=[RecordID("material_chapter", f"c{i}")],
        )
        for i in (1, 2, 3)
    ]


class _FakePlanner:
    """Fake do `Planner`: devolve a proposta canned (ou None para simular LLM caído)."""

    def __init__(self, proposal: PlanProposal | None) -> None:
        self._proposal = proposal
        self.calls: list[int] = []

    def propose(self, chapters: Any) -> PlanProposal | None:
        self.calls.append(len(list(chapters)))
        return self._proposal


@pytest.fixture(autouse=True)
def stub_plan_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leituras vazias por padrão + escrita sem banco real (o `connect_rw` do ADR-0018).

    Sem isto a rota de escrita explodiria em ConfigError (não há senha rw no unit) e o
    teste falharia por 503 em vez de pelo comportamento que ele afirma.
    """
    from tests.api.conftest import _fake_connect

    monkeypatch.setattr("kubo.api.routes.study.client.connect_rw", _fake_connect)
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_materials", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_material", lambda db, **kw: _material()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_chapters", lambda db, **kw: _chapters()
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.count_chapters", lambda db, **kw: 3)
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: _topic())
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_for_material", lambda db, **kw: None
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.create_topic", lambda db, **kw: _topic())
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: None
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_plan_entries", lambda db, **kw: [])
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.save_plan_proposal", lambda db, **kw: _plan()
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.remove_plan_entry", lambda db, **kw: None
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.move_plan_entry", lambda db, **kw: None)
    monkeypatch.setattr("kubo.api.routes.study.study_store.set_plan_cadence", lambda db, **kw: None)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.activate_plan",
        lambda db, **kw: _plan("active", activated=True),
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.build_planner", lambda db, ctx: _FakePlanner(_proposal())
    )
    # Leituras de Lição (KUBO-137): a tela do tema com plano ATIVO mostra a lição do dia
    # e o histórico. Vazias por padrão — o comportamento delas é de `test_lesson.py`.
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_lesson_for_day", lambda db, **kw: None
    )
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_lessons", lambda db, **kw: [])
    monkeypatch.setattr("kubo.api.routes.study.study_store.count_lessons", lambda db, **kw: 0)
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_log_for_lesson", lambda db, **kw: None
    )
    # Régua de progresso (KUBO-138): a tela do tema com plano ativado deriva o progresso
    # das conclusões. Vazia por padrão — o comportamento dela é de `test_plan_lifecycle.py`.
    monkeypatch.setattr("kubo.api.routes.study.study_store.completion_dates", lambda db, **kw: [])


def _proposal() -> PlanProposal:
    """Proposta coerente: 3 capítulos em 2 lições."""
    return PlanProposal(
        lessons=[
            PlanLesson(title="Fundamentos", chapter_seqs=[1, 2]),
            PlanLesson(title="Prática", chapter_seqs=[3]),
        ]
    )


def _spy(monkeypatch: pytest.MonkeyPatch, name: str, result: Any = None) -> list[dict[str, Any]]:
    """Substitui uma função da store por uma espiã que registra os kwargs recebidos."""
    calls: list[dict[str, Any]] = []

    def _record(db: Any, **kw: Any) -> Any:
        calls.append(kw)
        return result

    monkeypatch.setattr(f"kubo.api.routes.study.study_store.{name}", _record)
    return calls


def _csrf(authed_client: TestClient) -> str:
    """Token CSRF da sessão, lido do form da lista de Materiais."""
    html = authed_client.get("/study/materials").text
    m = re.search(r'name="csrf" value="([0-9a-f]+)"', html)
    assert m, "csrf ausente no form de Estudos"
    return m.group(1)


def _show_plan(
    monkeypatch: pytest.MonkeyPatch, plan: StudyPlan, entries: list[PlanEntry] | None = None
) -> None:
    """Deixa a tela do tema com um plano e suas lições."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_plan_for_topic", lambda db, **kw: plan
    )
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_plan_entries",
        lambda db, **kw: _entries() if entries is None else entries,
    )


def test_create_topic_redirects_to_the_topic_page(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criar tema herda o título do material e leva o dono pro tema (PRG)."""
    calls = _spy(monkeypatch, "create_topic", _topic())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"/study/materials/{_MATERIAL_ID.id}/topic",
        data={"csrf": csrf},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith(_TOPIC_URL)
    assert len(calls) == 1
    assert calls[0]["title"] == "Manual de Kubo"


def test_create_topic_for_unknown_material_is_404(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Material inexistente (ou de outro usuário) não vira tema."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_material", lambda db, **kw: None)
    calls = _spy(monkeypatch, "create_topic", _topic())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        "/study/materials/nao-existe/topic", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 404
    assert calls == []


@pytest.mark.parametrize(
    ("path", "data"),
    [
        (f"/study/materials/{_MATERIAL_ID.id}/topic", {}),
        (f"{_TOPIC_URL}/plan/propose", {}),
        (f"{_TOPIC_URL}/plan/entries/2/remove", {}),
        (f"{_TOPIC_URL}/plan/entries/2/move", {"direction": "up"}),
        (f"{_TOPIC_URL}/plan/cadence", {"weekdays": ["mon"]}),
        (f"{_TOPIC_URL}/plan/activate", {}),
    ],
)
def test_writes_require_csrf(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, data: dict[str, Any]
) -> None:
    """Toda escrita sem CSRF é 403 e NÃO chega à store."""
    _show_plan(monkeypatch, _plan())
    saved = _spy(monkeypatch, "save_plan_proposal", _plan())
    created = _spy(monkeypatch, "create_topic", _topic())
    removed = _spy(monkeypatch, "remove_plan_entry")
    moved = _spy(monkeypatch, "move_plan_entry")
    cadence = _spy(monkeypatch, "set_plan_cadence")
    activated = _spy(monkeypatch, "activate_plan", _plan("active", activated=True))

    resp = authed_client.post(path, data=data, follow_redirects=False)

    assert resp.status_code == 403
    assert saved == created == removed == moved == cadence == activated == []


def test_propose_saves_the_lessons_the_planner_returned(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposta da persona vira as lições gravadas, com os capítulos resolvidos por seq."""
    planner = _FakePlanner(_proposal())
    monkeypatch.setattr("kubo.api.routes.study.build_planner", lambda db, ctx: planner)
    calls = _spy(monkeypatch, "save_plan_proposal", _plan())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/propose", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert planner.calls == [3]
    assert len(calls) == 1
    entries: list[PlanEntryInput] = calls[0]["entries"]
    assert [(e.seq, e.title) for e in entries] == [(1, "Fundamentos"), (2, "Prática")]
    assert [list(e.chapter_ids) for e in entries] == [
        [RecordID("material_chapter", "c1"), RecordID("material_chapter", "c2")],
        [RecordID("material_chapter", "c3")],
    ]
    assert calls[0]["weekdays"] == _WEEK
    assert isinstance(calls[0]["target_date"], datetime)


def test_propose_falls_back_to_mechanical_plan_when_llm_is_down(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM indisponível não trava a tela: 1 capítulo = 1 lição, com aviso ao dono.

    O aviso é comportamento, não enfeite — sem ele o dono aprovaria um plano mecânico
    achando que foi a persona que o desenhou."""
    monkeypatch.setattr("kubo.api.routes.study.build_planner", lambda db, ctx: _FakePlanner(None))
    calls = _spy(monkeypatch, "save_plan_proposal", _plan())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/propose", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 303
    entries: list[PlanEntryInput] = calls[0]["entries"]
    assert [(e.seq, e.title) for e in entries] == [
        (1, "Capítulo 1"),
        (2, "Capítulo 2"),
        (3, "Capítulo 3"),
    ]
    _show_plan(monkeypatch, _plan())
    page = authed_client.get(resp.headers["location"])
    assert "mecânica" in page.text


def test_propose_on_material_without_chapters_does_not_save(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Material sem capítulo não tem plano possível — nada é gravado."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.list_chapters", lambda db, **kw: [])
    calls = _spy(monkeypatch, "save_plan_proposal", _plan())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/propose", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 400
    assert calls == []


def test_propose_refuses_a_material_with_too_many_chapters(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Material grande demais é recusado ANTES da persona, com motivo acionável.

    `PlanProposal.lessons` tem teto de 200: acima disso nem a proposta mecânica é
    construível. Sem esta cerca a tela devolveria 500 (erro de validação do modelo) e o
    dono não saberia que o caminho é reduzir o material na conferência.
    """
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.list_chapters", lambda db, **kw: _chapters(201)
    )
    planner = _FakePlanner(_proposal())
    monkeypatch.setattr("kubo.api.routes.study.build_planner", lambda db, ctx: planner)
    calls = _spy(monkeypatch, "save_plan_proposal", _plan())
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/propose", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 400
    assert "capítulos demais" in resp.text
    assert planner.calls == []
    assert calls == []


def test_remove_entry_delegates_to_the_store_and_redirects(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remover lição chega à store com o `seq` da URL e uma data-alvo recalculada."""
    _show_plan(monkeypatch, _plan())
    calls = _spy(monkeypatch, "remove_plan_entry")
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/entries/2/remove", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["seq"] == 2
    assert calls[0]["plan_id"] == _PLAN_ID
    assert isinstance(calls[0]["new_target"], datetime)


def test_move_entry_passes_the_direction(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direção vem do form e é repassada como está."""
    _show_plan(monkeypatch, _plan())
    calls = _spy(monkeypatch, "move_plan_entry")
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/entries/3/move",
        data={"csrf": csrf, "direction": "up"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert calls[0]["seq"] == 3
    assert calls[0]["direction"] == "up"


def test_move_entry_rejects_unknown_direction(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direção fora de up/down é recusada na borda — não chega à store."""
    _show_plan(monkeypatch, _plan())
    calls = _spy(monkeypatch, "move_plan_entry")
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/entries/3/move",
        data={"csrf": csrf, "direction": "sideways"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert calls == []


def test_cadence_saves_the_checked_weekdays(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Os checkboxes marcados viram a cadência do plano, com data-alvo nova."""
    _show_plan(monkeypatch, _plan())
    calls = _spy(monkeypatch, "set_plan_cadence")
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/cadence",
        data={"csrf": csrf, "weekdays": ["sat", "sun"]},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert calls[0]["weekdays"] == ["sat", "sun"]
    assert isinstance(calls[0]["new_target"], datetime)


def test_cadence_rejects_empty_and_invalid_weekdays(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cadência vazia ou com dia inventado é recusada na borda — plano não fica sem dia."""
    _show_plan(monkeypatch, _plan())
    calls = _spy(monkeypatch, "set_plan_cadence")
    csrf = _csrf(authed_client)

    empty = authed_client.post(
        f"{_TOPIC_URL}/plan/cadence", data={"csrf": csrf}, follow_redirects=False
    )
    bogus = authed_client.post(
        f"{_TOPIC_URL}/plan/cadence",
        data={"csrf": csrf, "weekdays": ["funday"]},
        follow_redirects=False,
    )

    assert empty.status_code == 400
    assert bogus.status_code == 400
    assert calls == []


def test_activate_plan_redirects_with_success(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ativar delega à store e volta pro tema (PRG)."""
    _show_plan(monkeypatch, _plan())
    calls = _spy(monkeypatch, "activate_plan", _plan("active", activated=True))
    csrf = _csrf(authed_client)

    resp = authed_client.post(
        f"{_TOPIC_URL}/plan/activate", data={"csrf": csrf}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert calls[0]["plan_id"] == _PLAN_ID
    assert _TOPIC_URL in resp.headers["location"]


def test_topic_page_without_plan_offers_to_propose(authed_client: TestClient) -> None:
    """Tema sem plano mostra o material e o botão de propor."""
    html = authed_client.get(_TOPIC_URL).text

    assert "Manual de Kubo" in html
    assert "Propor plano" in html


def test_topic_page_with_proposal_is_editable(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proposta traz as lições, a cadência editável e o botão de ativar."""
    _show_plan(monkeypatch, _plan())

    html = authed_client.get(_TOPIC_URL).text

    assert "Aula 1" in html
    assert "Aula 3" in html
    assert f"{_TOPIC_URL}/plan/entries/1/remove" in html
    assert f"{_TOPIC_URL}/plan/entries/2/move" in html
    assert f"{_TOPIC_URL}/plan/cadence" in html
    assert "Ativar plano" in html


def test_topic_page_with_active_plan_is_read_only(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plano ativo vira resumo: sem botão de ativar e sem edição de lição."""
    _show_plan(monkeypatch, _plan("active", activated=True))

    html = authed_client.get(_TOPIC_URL).text

    assert "Aula 1" in html
    assert "Ativar plano" not in html
    assert f"{_TOPIC_URL}/plan/entries/1/remove" not in html
    assert f"{_TOPIC_URL}/plan/cadence" not in html


def test_topic_page_for_unknown_topic_is_404(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tema inexistente (ou de outro usuário) é 404, não 500."""
    monkeypatch.setattr("kubo.api.routes.study.study_store.get_topic", lambda db, **kw: None)

    assert authed_client.get("/study/topics/nao-existe").status_code == 404


def test_topic_page_requires_auth(client: TestClient) -> None:
    """Sem sessão, a tela do tema redireciona pro login."""
    assert client.get(_TOPIC_URL, follow_redirects=False).status_code == 303


def test_material_detail_links_to_existing_topic(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No detalhe do material: com tema, link pro tema; sem tema, o botão de criar."""
    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_for_material", lambda db, **kw: _topic()
    )
    with_topic = authed_client.get(f"/study/materials/{_MATERIAL_ID.id}").text

    monkeypatch.setattr(
        "kubo.api.routes.study.study_store.get_topic_for_material", lambda db, **kw: None
    )
    without_topic = authed_client.get(f"/study/materials/{_MATERIAL_ID.id}").text

    assert _TOPIC_URL in with_topic
    assert f"/study/materials/{_MATERIAL_ID.id}/topic" in without_topic


def test_planner_tem_folga_de_max_tokens_para_thinking() -> None:
    """O teto de tokens do planner comporta thinking + JSON da proposta (KUBO-139).

    Nos modelos Claude atuais o thinking adaptativo é ligado por padrão e consome do
    MESMO `max_tokens` da resposta: o teto antigo truncaria o JSON no meio e a proposta
    voltaria como saída malformada (falha silenciosa, a tela cai no plano mecânico).
    """
    from kubo.api.routes.study import _PLANNER_MAX_TOKENS

    assert _PLANNER_MAX_TOKENS >= 8192
