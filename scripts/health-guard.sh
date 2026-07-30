#!/usr/bin/env bash
# health-guard.sh — wrapper shell para scripts/health_guard.py.
#
# Uso: ./scripts/health-guard.sh <service> --mode healthy|stable [--timeout N] [--stable-for N]
#
# O wrapper existe para ser chamado diretamente por workflows e pelo
# deploy-remote.sh, sem depender do entrypoint python. O binário python3 é
# o mesmo do host que roda os containers Kubo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

exec python3 scripts/health_guard.py "$@"
