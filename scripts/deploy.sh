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

echo "[deploy] 2/3 build + migrations + up (remoto)"
# Heredoc CITADO: o $(...) roda no SERVIDOR. NÃO exportar o env do servidor no shell antes
# do compose — o compose lê o arquivo sozinho, e uma variável exportada no shell fura a
# interpolação ${...} (lição do deploy 0010: gravaria valor velho nos containers).
#
# --force-recreate É OBRIGATÓRIO: `docker compose up -d` (sem ele) NÃO recria um container
# quando a única mudança é a imagem `kubo:latest` rebuildada — o container velho fica no ar
# servindo a versão antiga, e o /healthz passa mentindo (bug do deploy 0011). O guard de
# image-ID abaixo falha o deploy se, por qualquer motivo, o container não estiver na imagem
# recém-buildada — o smoke não confia só no /healthz.
ssh "${HOST}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/kubo
docker compose build
docker compose up -d surrealdb
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q surrealdb)")" = healthy ]; do
  sleep 3
done
docker compose run --rm kubo-scheduler python -m kubo.store.migrations
docker compose up -d --force-recreate
built="$(docker image inspect kubo:latest --format '{{.Id}}')"
running="$(docker inspect "$(docker compose ps -q kubo-api)" --format '{{.Image}}')"
if [ "$built" != "$running" ]; then
  echo "[deploy] FALHOU: kubo-api roda imagem velha ($running != recém-buildada $built)" >&2
  exit 1
fi
echo "[deploy] verificado: kubo-api roda a imagem recém-buildada"
REMOTE

echo "[deploy] 3/3 smoke ${HEALTH_URL}"
body="$(curl -fsS --max-time 10 "${HEALTH_URL}")" || {
  echo "[deploy] FALHOU: /healthz inacessível em ${HEALTH_URL}"
  exit 1
}
[ "${body}" = "ok" ] || {
  echo "[deploy] FALHOU: /healthz devolveu '${body}' (esperado 'ok')"
  exit 1
}
echo "[deploy] OK ✓ — ${HOST} atualizado; ${HEALTH_URL} → ok"
