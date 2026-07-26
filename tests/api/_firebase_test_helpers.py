"""Helpers compartilhados para testes de Firebase ID tokens."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from itsdangerous import TimestampSigner
from jwt import encode as jwt_encode

_FIREBASE_PROJECT_ID = "kubo-test-project"
_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)


def _b64u(n: int) -> str:
    return (
        base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big"))
        .rstrip(b"=")
        .decode("ascii")
    )


def rsa_keypair(*, kid: str = "test-kid") -> tuple[str, dict[str, Any]]:
    """Retorna (private_pem, jwk_dict) para assinar tokens de teste."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    numbers = key.public_key().public_numbers()

    jwk = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }
    return private_pem, jwk


def _decode_session_cookie(set_cookie: str) -> dict[str, Any]:
    """Decodifica o cookie itsdangerous assinado pelo SessionMiddleware (só para testes)."""
    cookie_value = set_cookie.split(";")[0].split("=", 1)[1]
    signer = TimestampSigner(os.environ["SESSION_SECRET"])
    data = signer.unsign(cookie_value.encode(), max_age=14 * 24 * 3600)
    return json.loads(base64.b64decode(data))


def _firebase_token(
    *, private_pem: str, uid: str, email: str | None = None, kid: str = "test-kid"
) -> str:
    """Gera um ID token Firebase assinado para testes."""
    now = int(time.time())
    payload = {
        "email": email or f"{uid}@example.com",
        "email_verified": True,
        "aud": _FIREBASE_PROJECT_ID,
        "iss": f"https://securetoken.google.com/{_FIREBASE_PROJECT_ID}",
        "iat": now,
        "exp": now + 3600,
        "sub": uid,
    }
    return jwt_encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": kid, "alg": "RS256"},
    )
