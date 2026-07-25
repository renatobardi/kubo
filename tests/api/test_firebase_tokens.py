"""Testes de verificação server-side de ID token Firebase (KUBO-93).

Usa chave RSA de teste assinando tokens JWT; os certificados públicos do Google
são mockados via respx no endpoint JWKS. Matriz cobre claims, algoritmo, kid,
expiração e allowlist fail-closed.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import respx
from jwt import encode as jwt_encode

from kubo.api.firebase_tokens import clear_jwks_cache, verify_id_token
from kubo.errors import FirebaseTokenError
from tests.api._firebase_test_helpers import rsa_keypair

_PROJECT_ID = "kubo-test-project"
_OWNER_UID = "owner-google-uid"
_OTHER_UID = "other-uid"
_KID = "test-kid"
_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)


def _mock_jwks(respx_mock: respx.MockRouter, jwk: dict[str, Any]) -> None:
    respx_mock.get(_JWKS_URL).respond(200, json={"keys": [jwk]})


def _token(
    *,
    private_pem: str,
    uid: str = _OWNER_UID,
    email: str = "owner@example.com",
    email_verified: bool = True,
    aud: str | None = _PROJECT_ID,
    iss: str | None = f"https://securetoken.google.com/{_PROJECT_ID}",
    exp: int | None = None,
    sub: str | None = _OWNER_UID,
    kid: str = _KID,
    alg: str = "RS256",
    include_uid: bool = False,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "email": email,
        "email_verified": email_verified,
        "iat": now,
        "sub": sub if sub is not None else uid,
    }
    if include_uid:
        payload["uid"] = uid
    if aud is not None:
        payload["aud"] = aud
    if iss is not None:
        payload["iss"] = iss
    payload["exp"] = exp if exp is not None else now + 3600
    return jwt_encode(payload, private_pem, algorithm=alg, headers={"kid": kid, "alg": alg})


@pytest.fixture(autouse=True)
def _reset_jwks_cache() -> None:
    """Cada teste começa com cache limpo para não cruzar chaves RSA geradas no teste."""
    clear_jwks_cache()


def test_valid_token_returns_uid_when_in_allowlist(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem)

    result = verify_id_token(token, _PROJECT_ID, {_OWNER_UID})

    assert result["uid"] == _OWNER_UID
    assert result["email"] == "owner@example.com"


def test_valid_token_uses_sub_claim_not_uid(respx_mock: respx.MockRouter) -> None:
    """Tokens reais do Firebase não contêm 'uid'; a identidade está em 'sub'."""
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, include_uid=False)

    result = verify_id_token(token, _PROJECT_ID, {_OWNER_UID})

    assert result["uid"] == _OWNER_UID


def test_uid_outside_allowlist_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, sub=_OTHER_UID)

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "uid_not_allowed"


def test_empty_allowlist_is_fail_closed(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem)

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, set())
    assert exc.value.code == "uid_not_allowed"


def test_wrong_algorithm_is_rejected(respx_mock: respx.MockRouter) -> None:
    """Token HS256 (mesmo com kid válido) deve ser rejeitado antes de tocar no JWKS."""
    token = jwt_encode(
        {"uid": _OWNER_UID, "aud": _PROJECT_ID},
        "any-secret",
        algorithm="HS256",
        headers={"kid": _KID, "alg": "HS256"},
    )

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "invalid_algorithm"


def test_unknown_kid_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, kid="unknown-kid")

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "unknown_kid"


def test_expired_token_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, exp=int(time.time()) - 1)

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "invalid_token"


def test_wrong_audience_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, aud="other-project")

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "invalid_token"


def test_wrong_issuer_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, iss="https://other.issuer.com")

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "invalid_token"


def test_email_unverified_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, email_verified=False)

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "invalid_token"


def test_missing_sub_is_rejected(respx_mock: respx.MockRouter) -> None:
    private_pem, jwk = rsa_keypair()
    _mock_jwks(respx_mock, jwk)
    token = _token(private_pem=private_pem, sub="")

    with pytest.raises(FirebaseTokenError) as exc:
        verify_id_token(token, _PROJECT_ID, {_OWNER_UID})
    assert exc.value.code == "invalid_token"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
