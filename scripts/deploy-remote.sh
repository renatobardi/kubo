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
  # Resolve repo e digest a partir de uma referência completa, sha256:hex ou hex puro.
  if [[ "$digest" == *@sha256:* ]]; then
    repo="${digest%@sha256:*}"
    normalized="${digest##*@sha256:}"
  else
    repo="${KUBO_IMAGE_REPO:-ghcr.io/renatobardi/kubo}"
    normalized="${digest#sha256:}"
  fi
  export KUBO_IMAGE="${repo}@sha256:${normalized}"
  # COMPOSE_FILE do ambiente (docker-compose.yml:compose.dev-lxc.yml ou
  # ...prd-lxc.yml, conforme .env do host) vem do ARQUIVO, não do shell: uma sessão
  # ssh não-interativa não dá source no .env, então o COMPOSE_FILE do shell está
  # sempre vazio aqui — usá-lo perderia silenciosamente o overlay de porta/volume
  # do ambiente (achado do KUBO-100: kubo-api sem `ports:` nenhum sem o overlay).
  env_compose_file="$(sed -n 's/^COMPOSE_FILE=//p' .env 2>/dev/null | tail -n1)"
  export COMPOSE_FILE="${env_compose_file:-docker-compose.yml}:compose.cd.yml"

  echo "[deploy] modo CD — imagem ${KUBO_IMAGE}"
  docker pull "${KUBO_IMAGE}"

  # Revalida o digest do conteúdo puxado imediatamente antes de subir.
  pulled_digest="$(docker inspect --format '{{index .RepoDigests 0}}' "${KUBO_IMAGE}" | sed 's/.*sha256://')"
  if [ "${pulled_digest}" != "${normalized}" ]; then
    echo "[deploy] FALHOU: digest puxado (${pulled_digest}) não bate com o promovido (${normalized})" >&2
    exit 1
  fi
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
deadline=$((SECONDS + 120))
until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q surrealdb)" 2>/dev/null || true)" == healthy ]]; do
  if (( SECONDS >= deadline )); then
    echo "[deploy] FALHOU: surrealdb não ficou healthy em 120s" >&2
    docker compose ps surrealdb >&2 || true
    docker compose logs --tail=100 surrealdb >&2 || true
    exit 1
  fi
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

# Guard de saúde (incidente 2026-07-30): `up -d` retorna antes do serviço estar de
# pé — um ConfigError de env faltante deixou o kubo-api em crash-loop nos dois
# ambientes e o CD declarou sucesso. Mesma família do incidente do BUILD_ID (PR #38):
# o digest-guard prova que a imagem certa subiu; o health-guard prova que ela FICOU
# de pé. kubo-scheduler não tem healthcheck (decisão do compose), daí o modo stable.
bash scripts/health-guard.sh kubo-api --mode healthy --timeout 90
bash scripts/health-guard.sh kubo-scheduler --mode stable --timeout 90

# Poda imagens dangling do projeto buildadas pelo compose. Imagens puxadas por
# digest do GHCR não são dangling enquanto estão em uso; prune irrestrito evitado
# para não apagar lixo de outras stacks no oute-server.
docker image prune -f --filter "label=com.docker.compose.project=kubo" >/dev/null || true

# Keep only current + 1 previous kubo image (prevents disk exhaustion from
# digest-pulled images accumulating across deploys). Lists all kubo repo images
# by creation time (newest first), keeps the first 2, removes the rest.
# The running image is always among the newest (just pulled this deploy), and
# the previous one serves as rollback target.
repo="${KUBO_IMAGE_REPO:-ghcr.io/renatobardi/kubo}"
kept=0
while IFS=$'\t' read -r img_id _created; do
  if [ "$kept" -lt 2 ]; then
    kept=$((kept + 1))
    continue
  fi
  docker rmi -f "$img_id" 2>/dev/null || true
done < <(docker images --format '{{.ID}}\t{{.CreatedAt}}' --filter "reference=${repo}" | sort -t$'\t' -k2 -r)
docker builder prune -f --filter "until=24h" >/dev/null 2>&1 || true
