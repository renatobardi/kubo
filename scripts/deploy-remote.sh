#!/usr/bin/env bash
# deploy-remote.sh — bloco REMOTO do deploy (roda NO kubo-test, chamado pelo deploy.sh via
# ssh). Recebe o BUILD_ID único do deploy como $1.
#
# Mora no repo (rsynced pelo deploy.sh) e roda como ARQUIVO — não via stdin. Isso é o que
# conserta o incidente pós-#37: um `docker compose build`/`run` dentro de um heredoc
# `ssh HOST bash -s <<EOF ... EOF` CONSOME o stdin (que é o próprio script) e engole os passos
# seguintes — o `up -d --force-recreate` e o guard NUNCA rodavam, e o deploy dizia `OK ✓` com
# o container velho no ar. Como arquivo, não há script no stdin para nenhum comando consumir.
set -euo pipefail

build_id="${1:?deploy-remote.sh exige o BUILD_ID do deploy como argumento}"
cd ~/kubo

# KUBO_BUILD_ID alimenta o build.arg do kubo-scheduler (único builder de kubo:latest). É a
# ÚNICA export aqui — não existe no .env, então não fura a interpolação ${...} dos segredos
# (lição do 0010: exportar segredo no shell grava valor velho nos containers).
export KUBO_BUILD_ID="$build_id"
docker compose build
docker compose up -d surrealdb
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q surrealdb)")" = healthy ]; do
  sleep 3
done
docker compose run -T --rm kubo-scheduler python -m kubo.store.migrations
# Seed das fontes RSS legadas como Cadastros (#108, corte RSS): passo IRMÃO das migrations —
# migração é schema, seed é DADO. Idempotente e não-destrutivo (coalesce), roda a cada deploy.
docker compose run -T --rm kubo-scheduler python -m kubo.store.seed
# Recria SÓ os serviços de app: --force-recreate sem nome bounceava surrealdb+backup a cada
# deploy à toa. O `up -d` seguinte garante o restante (backup) de pé em host fresh.
docker compose up -d --force-recreate kubo-api kubo-scheduler
docker compose up -d
# Guard por BUILD_ID (não por image-id): cada serviço de app tem que reportar o token recém-
# injetado. Confere os DOIS (kubo-api E kubo-scheduler — o scheduler é o caminho de ESCRITA,
# mesmo risco de ficar velho). rsync deploya a WORKING TREE, não o HEAD, então image-id/git-SHA
# não bastam; só o token único por deploy é inequívoco.
for svc in kubo-api kubo-scheduler; do
  got="$(docker compose exec -T "$svc" printenv KUBO_BUILD_ID || true)"
  if [ "$got" != "$build_id" ]; then
    echo "[deploy] FALHOU: $svc roda build '$got' != recém-deployado '$build_id' (imagem velha no ar)" >&2
    exit 1
  fi
done
echo "[deploy] verificado: kubo-api e kubo-scheduler rodam o build ${build_id}"
# Poda a imagem dangling que cada deploy deixa no containerd store. ESCOPADA ao projeto por
# label — o oute-server é multi-stack (PORTS.md), um prune irrestrito apagaria lixo de outra
# stack (acoplamento surpresa que este projeto evita).
docker image prune -f --filter "label=com.docker.compose.project=kubo" >/dev/null
