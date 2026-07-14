#!/usr/bin/env bash
# deploy.sh — deploy DEV do Kubo no kubo-test (ADR-0011). Roda DO MAC, na raiz do repo.
#
#   1) rsync do repo pro servidor (exclui o arquivo de ambiente do servidor — segredo fica)
#   2) build + migrations + up da stack no kubo-test
#   3) smoke /healthz — sai != 0 se qualquer passo falhar ou o /healthz não devolver "ok"
#
# Uso:   ./scripts/deploy.sh
# Env opcionais:
#   KUBO_DEPLOY_HOST   host ssh do kubo-test (default: kubo-test)
#   KUBO_HEALTH_URL    URL do healthz na tailnet (default: http://100.66.254.24:3900/healthz)
# Diretório remoto fixo: ~/kubo. Pré-requisito no servidor: o env com KUBO_RO_SURREAL_PASS
# já configurado (runbook §2c) — senão a kubo-api faz fail-fast.
set -euo pipefail

HOST="${KUBO_DEPLOY_HOST:-kubo-test}"
HEALTH_URL="${KUBO_HEALTH_URL:-http://100.66.254.24:3900/healthz}"

echo "[deploy] 1/3 rsync → ${HOST}:~/kubo"
rsync -az --delete \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='.coverage' \
  --exclude='.env' \
  ./ "${HOST}:kubo/"

# Token ÚNICO por deploy (SHA curto + timestamp UTC), calculado NO MAC (o remoto não tem
# `.git` — rsync exclui). O rsync deploya a WORKING TREE, não o HEAD: comparar image-id ou
# `git HEAD` deixaria um deploy 2 com árvore editada (HEAD igual) passar com código velho.
# O token é injetado na imagem (ARG/ENV KUBO_BUILD_ID) e conferido no container vivo abaixo.
KUBO_BUILD_ID="$(git rev-parse --short HEAD)-$(date -u +%Y%m%dT%H%M%SZ)"
echo "[deploy] 2/3 build ${KUBO_BUILD_ID} + migrations + up (remoto)"
# O bloco remoto roda como ARQUIVO rsynced (scripts/deploy-remote.sh), NÃO por heredoc no
# stdin: um `compose build`/`run` dentro de `ssh HOST bash -s <<EOF` consome o stdin (o próprio
# script) e engole os passos seguintes — foi a causa PROXIMA do incidente (o guard nunca
# rodava). O build_id vai por argumento; `< /dev/null` garante que nada leia o canal do ssh.
ssh "${HOST}" "cd ~/kubo && bash scripts/deploy-remote.sh '${KUBO_BUILD_ID}'" < /dev/null

echo "[deploy] 3/3 smoke ${HEALTH_URL}"
# O kubo-api foi recém-recriado (force-recreate): o uvicorn leva alguns segundos para aceitar
# conexão. Retry com TETO (~30s) em vez de tentativa única — senão o smoke falha flaky logo
# após o recreate (connection refused) e ensina a desconfiar do "FALHOU". O teto evita travar
# se o serviço não subir de fato; a última resposta entra na mensagem de falha.
smoke_ok=""
body=""
for _ in $(seq 1 10); do
  body="$(curl -fsS --max-time 10 "${HEALTH_URL}" 2>/dev/null || true)"
  if [ "${body}" = "ok" ]; then smoke_ok=1; break; fi
  sleep 3
done
[ -n "${smoke_ok}" ] || {
  echo "[deploy] FALHOU: ${HEALTH_URL} não devolveu 'ok' em ~30s (última: '${body:-<sem resposta>}')" >&2
  exit 1
}
echo "[deploy] OK ✓ — ${HOST} atualizado; ${HEALTH_URL} → ok"
