"""Rotas de autenticação (marco 9.2): /login (GET form + POST verify), /logout.

Vazio no scaffold — preenchido no 9.2 por TDD (scrypt, cookie de sessão,
sleep-on-fail, log estruturado). Rotas síncronas (ADR-0014).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
