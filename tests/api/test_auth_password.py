"""Testes do hash/verify da senha da UI (9.2) — scrypt stdlib, formato portável."""

from __future__ import annotations

import pytest

from kubo.api.auth import hash_password, verify_password

_PW = "correct-horse-battery-staple"


def test_hash_verify_roundtrip() -> None:
    """A senha certa verifica; a errada não."""
    stored = hash_password(_PW)
    assert verify_password(_PW, stored) is True
    assert verify_password("wrong", stored) is False


def test_hash_embeds_params_and_is_salted() -> None:
    """O hash carrega os params no formato scrypt$14$8$1$... e usa salt aleatório
    (dois hashes da mesma senha diferem)."""
    stored = hash_password(_PW)
    assert stored.startswith("scrypt$14$8$1$")
    assert stored != hash_password(_PW)


def test_verify_rejects_malformed_hash() -> None:
    """Hash malformado (não é o formato esperado) é rejeitado, não explode."""
    for bad in ["", "garbage", "scrypt$14$8", "scrypt$14$8$1$nothex$nothex", "bcrypt$x$y"]:
        assert verify_password(_PW, bad) is False


def test_verify_reads_params_from_hash_not_constants() -> None:
    """O verify parseia n/r/p do próprio hash — um hash gravado com params diferentes
    (ex.: n=2^13) ainda verifica, provando que o custo não vem de constante fixa."""
    import hashlib

    salt = b"\x00" * 16
    dk = hashlib.scrypt(_PW.encode(), salt=salt, n=2**13, r=8, p=1)
    stored = f"scrypt$13$8$1${salt.hex()}${dk.hex()}"
    assert verify_password(_PW, stored) is True
    assert verify_password("wrong", stored) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
