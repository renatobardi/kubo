"""Testes do `github_api` (ADR-0019 §VI/§IX, E3/E8) — HTTP mockado por httpx.MockTransport.

Sem rede: o `transport` injetável (mesma disciplina do sender do Telegram) roteia por
método+caminho. Prova E3 (URL/número do PR vêm da RESPOSTA da API, nunca do agente), a
redação do PAT em erro, e E8/D38 (nenhuma função de merge — anti-bypass por capacidade).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kubo.errors import ForgeError
from kubo.workers import github_api
from kubo.workers.github_api import PrRef, close_pull_request, open_pull_request

_BASE = "https://api.github.com"
_TOKEN = "fake-forge-pat-do-not-leak"  # não é um PAT real (evita o gate detect-secrets)


def _record(requests: list[httpx.Request], responses: dict[tuple[str, str], httpx.Response]) -> Any:
    """MockTransport que grava cada request e responde por (método, caminho)."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        resp = responses.get((request.method, request.url.path))
        if resp is None:  # pragma: no cover — falha de setup do teste, não do código
            raise AssertionError(f"sem resposta mock para {request.method} {request.url.path}")
        return resp

    return httpx.MockTransport(handler)


def test_open_pull_request_returns_url_and_number_from_response() -> None:
    reqs: list[httpx.Request] = []
    transport = _record(
        reqs,
        {
            ("POST", "/repos/owner/kubo-forge/pulls"): httpx.Response(
                201, json={"html_url": "https://github.com/owner/kubo-forge/pull/42", "number": 42}
            )
        },
    )
    pr = open_pull_request(
        base_url=_BASE,
        token=_TOKEN,
        owner="owner",
        repo="kubo-forge",
        head="kubo/flow-abc",
        base="main",
        title="ignored by E3",
        body="agent prose",
        transport=transport,
    )
    assert isinstance(pr, PrRef)
    # E3: estrutura vem da RESPOSTA da API, não do que passamos
    assert pr.url == "https://github.com/owner/kubo-forge/pull/42"
    assert pr.number == 42
    # auth no header (nunca na URL)
    assert reqs[0].headers["authorization"] == f"Bearer {_TOKEN}"
    sent = json.loads(reqs[0].content)
    assert sent["head"] == "kubo/flow-abc"
    assert sent["base"] == "main"


def test_open_pull_request_error_status_raises_forge_error_without_token() -> None:
    transport = _record(
        [],
        {
            ("POST", "/repos/owner/kubo-forge/pulls"): httpx.Response(
                422, json={"message": "Validation Failed"}
            )
        },
    )
    with pytest.raises(ForgeError) as excinfo:
        open_pull_request(
            base_url=_BASE,
            token=_TOKEN,
            owner="owner",
            repo="kubo-forge",
            head="h",
            base="main",
            title="t",
            body="b",
            transport=transport,
        )
    assert _TOKEN not in str(excinfo.value)


def test_close_pull_request_comments_reason_then_closes() -> None:
    reqs: list[httpx.Request] = []
    transport = _record(
        reqs,
        {
            ("POST", "/repos/owner/kubo-forge/issues/42/comments"): httpx.Response(201, json={}),
            ("PATCH", "/repos/owner/kubo-forge/pulls/42"): httpx.Response(
                200, json={"state": "closed"}
            ),
        },
    )
    close_pull_request(
        base_url=_BASE,
        token=_TOKEN,
        owner="owner",
        repo="kubo-forge",
        number=42,
        reason="rejected: escopo fora da task",
        transport=transport,
    )
    # ordem: comenta o motivo, depois fecha
    assert reqs[0].method == "POST" and reqs[0].url.path.endswith("/comments")
    assert reqs[1].method == "PATCH" and reqs[1].url.path.endswith("/pulls/42")
    # E8: o reason (input do dono) vai no corpo do comentário sem interpolação esquisita
    assert json.loads(reqs[0].content)["body"] == "rejected: escopo fora da task"
    assert json.loads(reqs[1].content)["state"] == "closed"


def test_no_merge_capability_by_construction() -> None:
    # E8/D38: o Kubo não tem capacidade de merge — anti-bypass por construção, não disciplina.
    assert not hasattr(github_api, "merge_pull_request")
    assert not hasattr(github_api, "merge")
    assert not any("merge" in name.lower() for name in dir(github_api))
