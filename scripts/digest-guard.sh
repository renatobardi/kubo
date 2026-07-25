#!/usr/bin/env bash
# digest-guard.sh — wrapper shell para scripts/digest_guard.py.
#
# Uso: ./scripts/digest-guard.sh <expected-digest> <service-name> [--project <project>]
#
# O wrapper existe para ser chamado diretamente por workflows e pelo
# deploy-remote.sh, sem depender do entrypoint python. O binário python3 é
# o mesmo do host que roda os containers Kubo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

exec python3 scripts/digest_guard.py "$@"
