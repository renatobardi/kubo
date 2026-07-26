"""Verificação server-side de ID token Firebase (KUBO-93, ADR-0036 §II).

Busca as chaves públicas do Google no endpoint JWKS, seleciona pelo `kid` do header,
verifica a assinatura com pyjwt aceitando **apenas RS256** e valida as claims
obrigatórias. Fail-closed em todas as bordas (algoritmo, kid, claims, allowlist).

Não usa firebase-admin (gRPC/Firestore, service account em disco) — verificar exige
só chave pública + project id (ADR-0036).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from jwt.api_jwk import PyJWKSet

from kubo.errors import FirebaseTokenError

_GOOGLE_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
_DEFAULT_JWKS_TTL = 3600

_jwks_cache: tuple[PyJWKSet, float, int] | None = None


def clear_jwks_cache() -> None:
    """Limpa o cache de chaves públicas (útil para testes unitários)."""
    global _jwks_cache  # noqa: PLW0603
    _jwks_cache = None


def _parse_max_age(cache_control: str | None) -> int:
    """Extrai max-age de Cache-Control; fallback de 1h se ausente/malformado."""
    if not cache_control:
        return _DEFAULT_JWKS_TTL
    for directive in cache_control.split(","):
        part = directive.strip().lower()
        if part.startswith("max-age="):
            try:
                return max(0, int(part.split("=", 1)[1]))
            except ValueError:
                break
    return _DEFAULT_JWKS_TTL


def _fetch_jwks() -> PyJWKSet:
    """Retorna o JWKS do Google, cacheado pelo max-age do Cache-Control."""
    global _jwks_cache  # noqa: PLW0603

    now = time.time()
    if _jwks_cache is not None:
        jwks, fetched_at, ttl = _jwks_cache
        if now - fetched_at < ttl:
            return jwks

    try:
        response = httpx.get(_GOOGLE_JWKS_URL, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FirebaseTokenError(
            "jwks_unavailable", f"chaves do Google indisponíveis: {exc}"
        ) from exc

    ttl = _parse_max_age(response.headers.get("cache-control"))
    try:
        jwks = PyJWKSet.from_json(response.text)
    except jwt.PyJWTError as exc:
        raise FirebaseTokenError("jwks_unavailable", f"JWKS do Google inválido: {exc}") from exc

    _jwks_cache = (jwks, now, ttl)
    return jwks


def _signing_key(jwks: PyJWKSet, kid: str) -> jwt.PyJWK:
    """Seleciona a chave pública pelo kid; falha fechado se não encontrar."""
    for key in jwks.keys:
        if key.key_id == kid:
            return key
    raise FirebaseTokenError(
        "unknown_kid", f"kid {kid!r} não encontrado nos certificados do Google"
    )


def verify_id_token(
    token: str, project_id: str, allowed_uids: set[str] | None = None
) -> dict[str, Any]:
    """Verifica um ID token do Firebase e devolve uid/email.

    Levanta `FirebaseTokenError` com `code` discriminatório para qualquer falha.
    `allowed_uids`, quando fornecida, aplica allowlist fail-closed (vazia nega tudo).
    Quando `None`, o token é verificado sem checagem de allowlist (self-signup).
    """

    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise FirebaseTokenError("invalid_token", f"token malformado: {exc}") from exc

    if header.get("alg") != "RS256":
        raise FirebaseTokenError("invalid_algorithm", "algoritmo do token não é RS256")

    kid = header.get("kid")
    if not kid:
        raise FirebaseTokenError("unknown_kid", "header não contém kid")

    jwks = _fetch_jwks()
    key = _signing_key(jwks, kid)

    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise FirebaseTokenError("invalid_token", "token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise FirebaseTokenError("invalid_token", f"token inválido: {exc}") from exc

    if not payload.get("sub"):
        raise FirebaseTokenError("invalid_token", "sub ausente ou vazio")
    if payload.get("email_verified") is not True:
        raise FirebaseTokenError("invalid_token", "email não verificado")

    uid = payload.get("sub")
    if allowed_uids is not None and uid not in allowed_uids:
        raise FirebaseTokenError("uid_not_allowed", "uid não está na allowlist")

    return {"uid": uid, "email": payload.get("email", ""), "provider": "firebase"}
