#!/usr/bin/env python3
"""Sanity check for required Kubo secrets/envs before deploy.

Reads a .env file (defaults to ./.env) and reports which variables are present,
missing or look suspicious. Exit code 1 if any required variable is missing.
"""

from __future__ import annotations

import re
import sys
from argparse import ArgumentParser
from pathlib import Path

_REQUIRED_GROUPS: dict[str, list[str]] = {
    "app startup": [
        "KUBO_PASSWORD_HASH",
        "SESSION_SECRET",
    ],
    "telegram": [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME",
        "KUBO_TELEGRAM_WEBHOOK_SECRET",
    ],
    "database write": [
        "KUBO_RW_SURREAL_PASS",
    ],
}

_OPTIONAL_GROUPS: dict[str, list[str]] = {
    "email": [
        "KUBO_EMAIL_HOST",
        "KUBO_EMAIL_PORT",
        "KUBO_EMAIL_USER",
        "KUBO_EMAIL_PASSWORD",
        "KUBO_EMAIL_FROM",
    ],
    "owner seed / base url": [
        "KUBO_OWNER_TELEGRAM_CHAT_ID",
        "KUBO_BASE_URL",
    ],
}


def _unquote(value: str) -> str:
    """Strip matching surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _parse_line(raw: str) -> tuple[str, str] | None:
    """Return (key, value) for a single .env line, or None when ignored."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    value = _unquote(value.strip())
    return (key, value) if key else None


def _parse_env(path: Path) -> dict[str, str]:
    """Very small .env parser; supports unquoted and double/single quoted values."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw in path.read_text().splitlines():
        parsed = _parse_line(raw)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _validate_webhook_secret(secret: str) -> list[str]:
    """Telegram only allows [A-Za-z0-9_-]{1,256} for secret_token."""
    warnings: list[str] = []
    if not secret:
        return warnings
    if len(secret) > 256:
        warnings.append("longer than 256 chars")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", secret):
        warnings.append("contains characters Telegram does not allow (use [A-Za-z0-9_-])")
    return warnings


def _entry_status(key: str, value: str) -> tuple[str, str]:
    """Return (status, extra_note) for a present env entry."""
    if key == "KUBO_TELEGRAM_WEBHOOK_SECRET":
        warnings = _validate_webhook_secret(value)
        if warnings:
            return "WARN", f" ({'; '.join(warnings)})"
    if key == "TELEGRAM_BOT_USERNAME" and value.startswith("@"):
        return "WARN", " (remove the leading @)"
    return "OK", ""


def _check_group(env: dict[str, str], group: str, keys: list[str], required: bool) -> int:
    """Print one group and return the number of missing required keys."""
    missing = 0
    print(f"\n[{group}]")
    for key in keys:
        value = env.get(key, "").strip()
        if value:
            status, extra = _entry_status(key, value)
        else:
            missing += 1
            status = "MISSING" if required else "optional missing"
            extra = ""
        print(f"  {key:<40} {status}{extra}")
    return missing


def main(argv: list[str]) -> int:
    parser = ArgumentParser(description="Check Kubo env secrets.")
    parser.add_argument(
        "env_file",
        nargs="?",
        default=".env",
        help="Path to .env file (default: ./.env)",
    )
    args = parser.parse_args(argv)

    path = Path(args.env_file).expanduser()
    env = _parse_env(path)

    if not env:
        print(f"No variables found in {path}", file=sys.stderr)
        return 1

    print(f"Checking {path}")

    missing_required = 0
    for group, keys in _REQUIRED_GROUPS.items():
        missing_required += _check_group(env, group, keys, required=True)

    for group, keys in _OPTIONAL_GROUPS.items():
        _check_group(env, group, keys, required=False)

    print()
    if missing_required:
        print(f"Result: {missing_required} required variable(s) missing.")
        return 1

    print("Result: all required variables present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
