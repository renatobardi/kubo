#!/usr/bin/env bash
# seed-rss-sources.sh — cadastra a segunda leva de feeds RSS no Kubo PRD.
# Roda no LXC de produção via docker compose, usando o .env local do LXC
# (que já contém KUBO_RW_SURREAL_PASS).
#
# Config:
#   KUBO_REMOTE_HOST   host ssh do oute-server (default: oute-server)
#   KUBO_PRD_NAME      nome do container LXC (default: kubo-prd)
set -euo pipefail

REMOTE_HOST="${KUBO_REMOTE_HOST:-oute-server}"
PRD_NAME="${KUBO_PRD_NAME:-kubo-prd}"

echo "[seed-rss] cadastrando feeds adicionais no ${PRD_NAME}"
ssh -T "${REMOTE_HOST}" \
  "lxc exec ${PRD_NAME} -- bash -c 'cd /home/ubuntu/kubo && docker compose run -T --rm kubo-scheduler python -m kubo.store.seed_extra_rss'"

echo "[seed-rss] OK"
