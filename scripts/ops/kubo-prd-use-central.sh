#!/usr/bin/env bash
# kubo-prd-use-central.sh — igual a kubo-test-use-central.sh, mas para o kubo-prd (PRODUÇÃO).
# Escreve no `.env` do kubo-prd a config da instância central (database `prd`, usuários app_prd/rw_prd/ro_prd).
# NÃO reinicia nada. Pede confirmação explícita porque mexe no .env de produção.
#
#   ./scripts/ops/kubo-prd-use-central.sh --check   # só pré-checagens (sem confirmação, não altera nada)
#   ./scripts/ops/kubo-prd-use-central.sh           # aplica (pede para digitar kubo-prd)
#
# Ordem do cutover do prd: ver docs/runbook-deploy.md §5c.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${1:-}" != "--check" ]; then
  echo "ATENÇÃO: isto altera o .env do kubo-prd (PRODUÇÃO). O banco local não é tocado e o .env anterior"
  echo "fica em ~/.env.pre-central. Nada é reiniciado por este script."
  read -r -p "Digite kubo-prd para continuar: " answer </dev/tty
  [ "${answer}" = "kubo-prd" ] || { echo "cancelado." >&2; exit 1; }
fi

export KUBO_TEST_NAME="${KUBO_TEST_NAME:-kubo-prd}"
export KUBO_CENTRAL_DB="${KUBO_CENTRAL_DB:-prd}"
export KUBO_BASE_COMPOSE_FILE="${KUBO_BASE_COMPOSE_FILE:-docker-compose.yml:compose.prd-lxc.yml}"
exec "${HERE}/kubo-test-use-central.sh" "$@"
