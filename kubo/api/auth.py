"""Hash e verificação da senha única da UI — scrypt stdlib, zero dep de hash (ADR-0014).

Params fixos n=2^14, r=8, p=1 (n=2^15 estoura o cap de memória do OpenSSL — footgun
confirmado). O hash embute os params no próprio formato `scrypt:14:8:1:<salt>:<hash>`,
então o verify lê os params do hash, não de constantes: ajustar o custo no futuro não
invalida silenciosamente uma senha já gravada. Verificação em tempo constante
(`hmac.compare_digest`). A senha entra como `str`, nunca é logada nem persistida.

Separador `:` (não `$`): o hash vai para `KUBO_PASSWORD_HASH` num `.env` lido pelo
docker compose, que interpola `$` no valor (`$14` viraria variável vazia e mutilaria
o hash em silêncio). `:` cola no `.env` sem escape — salt/hash são hex, nunca têm `:`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# log2(N) em vez de N: mantém o formato compacto e legível (14, não 16384).
_N_LOG2 = 14
_R = 8
_P = 1
_PREFIX = "scrypt"
_SEP = ":"
_SALT_BYTES = 16


def _derive(password: str, salt: bytes, n_log2: int, r: int, p: int) -> bytes:
    """Deriva a chave scrypt. maxmem default (0): n=2^14,r=8,p=1 cabe em ~16MiB,
    abaixo do cap de 32MiB do OpenSSL — n=2^15 estouraria."""
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**n_log2, r=r, p=p)


def hash_password(password: str) -> str:
    """Deriva a string de hash portável de uma senha (formato `scrypt:14:8:1:<salt>:<hash>`)."""
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = _derive(password, salt, _N_LOG2, _R, _P)
    return _SEP.join([_PREFIX, str(_N_LOG2), str(_R), str(_P), salt.hex(), dk.hex()])


def verify_password(password: str, stored: str) -> bool:
    """True se `password` bate com o hash `stored`; False para qualquer hash malformado.

    Lê n/r/p do próprio `stored` (não de constantes) — um hash gravado com outro custo
    ainda verifica. `stored` vem de env de confiança (invariante 8), não de entrada hostil."""
    parts = stored.split(_SEP)
    if len(parts) != 6 or parts[0] != _PREFIX:
        return False
    _, n_log2_s, r_s, p_s, salt_hex, hash_hex = parts
    try:
        n_log2, r, p = int(n_log2_s), int(r_s), int(p_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        # _derive fica DENTRO do try: um n fora de faixa (hash corrompido/misconfig)
        # faz scrypt levantar ValueError ("memory limit exceeded") — falha fechada
        # (False), nunca 500 revelador. `stored` é de confiança, mas a robustez importa.
        dk = _derive(password, salt, n_log2, r, p)
    except ValueError:
        return False
    return hmac.compare_digest(dk, expected)
