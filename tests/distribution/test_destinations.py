"""Loader de `destinations.yaml` + resolução de endereço por env (ADR-0015 §I).

Unit (sem DB): valida a forma declarativa (`extra="forbid"`, kind/channel
fechados), que `address_ref` é só referência `env:VAR` (PII nunca inline, nunca
ecoada no erro), e que o endereço resolvido fica fora do repr (chat_id/e-mail é
PII — mesmo fechamento por tipo do `ResolvedIntegration.secret`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kubo.distribution.destinations import (
    load_destinations,
    resolve_base_url,
    resolve_destinations,
)
from kubo.errors import ConfigError

_VALID = """
destinations:
  - id: owner-telegram
    name: Renato (Telegram)
    kind: pessoa
    channel: telegram
    address_ref: env:KUBO_OWNER_TELEGRAM_CHAT_ID
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "destinations.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_destinations(tmp_path: Path) -> None:
    """YAML válido carrega com os campos declarados."""
    dests = load_destinations(_write(tmp_path, _VALID))
    assert len(dests) == 1
    assert dests[0].id == "owner-telegram"
    assert dests[0].channel == "telegram"
    assert dests[0].address_ref == "env:KUBO_OWNER_TELEGRAM_CHAT_ID"


def test_extra_field_rejected(tmp_path: Path) -> None:
    """Campo desconhecido é rejeitado (extra=forbid) — borda declarativa."""
    text = _VALID + "    priority: 1\n"
    with pytest.raises(ConfigError):
        load_destinations(_write(tmp_path, text))


def test_unknown_channel_rejected(tmp_path: Path) -> None:
    """Canal fora de {telegram, email} é rejeitado."""
    text = _VALID.replace("channel: telegram", "channel: carrier-pigeon")
    with pytest.raises(ConfigError):
        load_destinations(_write(tmp_path, text))


def test_unknown_kind_rejected(tmp_path: Path) -> None:
    """kind fora de {pessoa, sistema} é rejeitado (D11)."""
    text = _VALID.replace("kind: pessoa", "kind: robot")
    with pytest.raises(ConfigError):
        load_destinations(_write(tmp_path, text))


def test_inline_address_rejected_without_echo(tmp_path: Path) -> None:
    """address_ref com valor inline (não env:VAR) é rejeitado, e o valor NÃO
    aparece no erro (PII colada por engano não pode vazar)."""
    secret = "123456789"
    text = _VALID.replace("env:KUBO_OWNER_TELEGRAM_CHAT_ID", secret)
    with pytest.raises(ConfigError) as exc:
        load_destinations(_write(tmp_path, text))
    assert secret not in str(exc.value)


def test_resolve_address_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_destinations lê o endereço do env referenciado."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "42")
    resolved = resolve_destinations(load_destinations(_write(tmp_path, _VALID)))
    assert resolved[0].address == "42"


def test_resolve_missing_env_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env referenciada ausente → ConfigError (não sobe destino meio-resolvido)."""
    monkeypatch.delenv("KUBO_OWNER_TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ConfigError):
        resolve_destinations(load_destinations(_write(tmp_path, _VALID)))


def test_resolved_address_not_in_repr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O endereço resolvido (PII) NUNCA aparece em repr/str/traceback (repr=False)."""
    monkeypatch.setenv("KUBO_OWNER_TELEGRAM_CHAT_ID", "secret-chat-id")
    resolved = resolve_destinations(load_destinations(_write(tmp_path, _VALID)))
    assert "secret-chat-id" not in repr(resolved[0])


def test_resolve_base_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_base_url lê KUBO_BASE_URL do env (sem barra final)."""
    monkeypatch.setenv("KUBO_BASE_URL", "http://100.66.254.24:3900/")
    assert resolve_base_url() == "http://100.66.254.24:3900"


def test_resolve_base_url_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """KUBO_BASE_URL ausente → ConfigError (o link do digest não pode ficar quebrado)."""
    monkeypatch.delenv("KUBO_BASE_URL", raising=False)
    with pytest.raises(ConfigError):
        resolve_base_url()
