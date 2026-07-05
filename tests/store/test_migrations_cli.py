"""Testes do runner de deploy `python -m kubo.store.migrations` (unit, sem banco).

O runner é o passo de deploy que aplica migrations pendentes — desacoplado do boot
do scheduler (auto-migrate no boot de réplicas é risco). Aqui só validamos a fiação
(conecta por ambiente → aplica → devolve o que aplicou); a lógica de aplicação em si
tem cobertura de integração em test_migrations.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from kubo.store.migrations import __main__ as cli


def test_main_connects_applies_and_returns() -> None:
    """main() abre conexão por ambiente, chama apply_migrations e devolve o aplicado."""
    fake_db = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = fake_db
    cm.__exit__.return_value = False

    with (
        patch.object(cli.client, "connect", return_value=cm) as connect,
        patch.object(cli, "apply_migrations", return_value=["0001_x.surql"]) as apply,
    ):
        result = cli.main()

    connect.assert_called_once_with()
    apply.assert_called_once_with(fake_db)
    assert result == ["0001_x.surql"]
