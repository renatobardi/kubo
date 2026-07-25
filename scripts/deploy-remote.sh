#!/usr/bin/env bash
# deploy-remote.sh — bloco REMOTO do deploy (roda NO servidor Kubo, chamado via
# ssh pelo deploy.sh do Mac ou por um workflow de CD).
#
# Modo CD (padrão): recebe o digest da imagem publicada no GHCR e a puxa.
# Modo legado (--build-id): builda a imagem localmente no host (escape manual
# enquanto a esteira CD não está ativa).
#
# Mora no repo (rsynced pelo deploy.sh) e roda como ARQUIVO — não via stdin.
# Isso conserta o incidente pós-#37: heredoc `ssh HOST bash -s <<EOF` consome
# o stdin e engolia passos.
set -euo pipefail

usage() {
  echo "Usage: $0 [--build-id <build-id>] <digest>" >&2
  echo "  digest       imagem@sha256:... ou sha256:... (modo CD, padrão)" >&2
  echo "  --build-id   builda localmente no host com o KUBO_BUILD_ID informado" >&2
  exit 1
}

mode="digest"
digest=""
build_id=""

while [ $# -gt 0 ]; do
  case "$1" in
    --build-id)
      [ -n "${2:-}" ] || usage
      mode="build"
      build_id="$2"
      shift 2
      ;;
    -h|--help) usage ;;
    -*)
      echo "Opção desconhecida: $1" >&2
      usage
      ;;
    *)
      [ -z "$digest" ] || usage
      digest="$1"
      shift
      ;;
  esac
done

if [ "$mode" = "digest" ] && [ -z "$digest" ]; then
  usage
fi
if [ "$mode" = "build" ] && [ -z "$build_id" ]; then
  usage
fi

cd ~/kubo

# Modo CD: puxa a imagem publicada uma vez no GHCR pelo digest.
# KUBO_IMAGE_REPO permite override (padrão = ghcr.io/renatobardi/kubo).
if [ "$mode" = "digest" ]; then
  repo="${KUBO_IMAGE_REPO:-ghcr.io/renatobardi/kubo}"
  # Normaliza digest: aceita 'sha256:...' ou só o hex.
  normalized="${digest#sha256:}"
  export KUBO_IMAGE="${repo}@sha256:${normalized}"
  export COMPOSE_FILE="docker-compose.yml:compose.cd.yml:${COMPOSE_FILE:-}"

  echo "[deploy] modo CD — imagem ${KUBO_IMAGE}"
  docker pull "${KUBO_IMAGE}"
else
  # Modo legado: build local. Mantém compatibilidade com deploy.sh do Mac
  # enquanto o dono não completa a setup da esteira CD (KUBO-99..KUBO-103).
  export KUBO_BUILD_ID="$build_id"
  echo "[deploy] modo build-local — KUBO_BUILD_ID ${KUBO_BUILD_ID}"
  docker compose build
fi

# Quiesce writers before destructive migrations (ADR-0030 §5).
docker compose stop kubo-api kubo-scheduler
docker compose up -d --no-build surrealdb
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q surrealdb)")" = healthy ]; do
  sleep 3
done

docker compose run -T --rm kubo-scheduler python -m kubo.store.migrations
# Seed das fontes RSS legadas como Cadastros (#108): passo IRMÃO das migrations —
# migração é schema, seed é DADO. Idempotente e não-destrutivo, roda a cada deploy.
docker compose run -T --rm kubo-scheduler python -m kubo.store.seed
# Recria SÓ os serviços de app: --force-recreate sem nome não bounceia surrealdb+backup.
docker compose up -d --no-build --force-recreate kubo-api kubo-scheduler
docker compose up -d --no-build

# Guard de deploy: no CD compara o digest da imagem viva contra o promovido.
# No modo build-local mantém o guard por KUBO_BUILD_ID (imagem local sem RepoDigest).
if [ "$mode" = "digest" ]; then
  bash scripts/digest-guard.sh "${normalized}" kubo-api
  bash scripts/digest-guard.sh "${normalized}" kubo-scheduler
else
  for svc in kubo-api kubo-scheduler; do
    got="$(docker compose exec -T "$svc" printenv KUBO_BUILD_ID || true)"
    if [ "$got" != "$KUBO_BUILD_ID" ]; then
      echo "[deploy] FALHOU: $svc roda build '$got' != recém-deployado '$KUBO_BUILD_ID' (imagem velha no ar)" >&2
      exit 1
    fi
  done
  echo "[deploy] verificado: kubo-api e kubo-scheduler rodam o build ${KUBO_BUILD_ID}"
fi

# Poda imagens dangling do projeto. No modo CD as imagens puxadas usam reference
# ghcr.io/renatobardi/kubo*; no modo legado usam label compose. Nenhum prune
# irrestrito para não apagar lixo de outras stacks no oute-server.
repo_ref="${KUBO_IMAGE_REPO:-ghcr.io/renatobardi/kubo}"
docker image prune -f --filter "label=com.docker.compose.project=kubo" >/dev/null || true
docker image prune -f --filter "reference=${repo_ref}*" >/dev/null || true
