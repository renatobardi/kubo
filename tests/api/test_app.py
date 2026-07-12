"""Testes do scaffold da app (marco 9.1): a fábrica sobe, /healthz responde sem
auth nem banco, os estáticos estão montados."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from kubo.api.app import create_app


def test_create_app_returns_fastapi() -> None:
    """A fábrica devolve uma instância nova de FastAPI (não singleton de módulo)."""
    app = create_app()
    assert isinstance(app, FastAPI)
    assert create_app() is not app


def test_healthz_ok_without_auth_or_db() -> None:
    """/healthz responde 200 'ok' — é o healthcheck do compose, fora de qualquer
    guard de auth e sem tocar o banco."""
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_static_mounted() -> None:
    """O htmx vendorizado é servido de /static (o diretório existe no repo)."""
    client = TestClient(create_app())
    resp = client.get("/static/htmx-2.0.4.min.js")
    assert resp.status_code == 200
    assert "htmx" in resp.text[:200]
