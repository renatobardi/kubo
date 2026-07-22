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


def _parse_env(path: Path) -> dict[str, str]:
    """Very small .env parser; supports unquoted and double/single quoted values."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # strip a leading "export " so "export KEY=value" works too
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
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


def _check_group(env: dict[str, str], group: str, keys: list[str], required: bool) -> int:
    """Print one group and return the number of missing required keys."""
    missing = 0
    print(f"\n[{group}]")
    for key in keys:
        value = env.get(key, "").strip()
        if value:
            status = "OK"
            extra = ""
            if key == "KUBO_TELEGRAM_WEBHOOK_SECRET":
                warnings = _validate_webhook_secret(value)
                if warnings:
                    status = "WARN"
                    extra = f" ({'; '.join(warnings)})"
            if key == "TELEGRAM_BOT_USERNAME" and value.startswith("@"):
                status = "WARN"
                extra = " (remove the leading @)"
            print(f"  {key:<40} {status}{extra}")
        else:
            missing += 1
            status = "MISSING" if required else "optional missing"
            print(f"  {key:<40} {status}")
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
