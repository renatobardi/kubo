"""Helper `python -m kubo.api.hashpw` — gera o valor de `KUBO_PASSWORD_HASH` (ADR-0014).

O dono roda, digita a senha (via getpass — nunca em argv, log ou histórico de shell)
e cola o hash impresso no `.env` do servidor. A senha em claro nunca toca o disco.
"""

from __future__ import annotations

import getpass
import sys

from kubo.api.auth import hash_password


def main() -> int:
    """Lê a senha duas vezes (confirma) e imprime a string de hash portável."""
    pw = getpass.getpass("Senha da UI: ")
    if not pw:
        print("senha vazia — nada gerado", file=sys.stderr)
        return 1
    if getpass.getpass("Confirme a senha: ") != pw:
        print("as senhas não conferem", file=sys.stderr)
        return 1
    print(hash_password(pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
