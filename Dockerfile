# syntax=docker/dockerfile:1
# Dockerfile — imagem de runtime do Kubo (scheduler + workers da fase 1).
#
# Build NATIVO no servidor aarch64 (sem registry/cross-build): `docker compose build`
# no oute-server. `uv` instala do lock (`--frozen` = falha se o lock divergir do
# pyproject) e só as deps de runtime (`--no-dev` deixa ruff/pyright/pytest de fora).
# Roda como usuário non-root (invariante de superfície mínima).
#
# `uv` vem por COPY do image oficial (build-time), pinado na MESMA versão do dev
# (0.11.17) para ler o lock sem surpresa de formato.

# --- Stage tailwind: gera kubo/api/static/app.css com o binário standalone (D26:
# zero Node). Binário PINADO por versão + SHA256 verificado. Arch linux-arm64 (o
# oute-server é aarch64 — VERIFICAR `uname -m` no LXC kubo-test antes do build; se
# for x86_64, trocar para tailwindcss-linux-x64 e o SHA correspondente). Escaneia os
# templates (@source ../templates no input.css) para incluir só as classes usadas.
FROM python:3.12-slim AS tailwind
ARG TAILWIND_VERSION=v4.3.2
ARG TAILWIND_ARCH=linux-arm64
ARG TAILWIND_SHA256=394ddccc2402cfa3abd97dfba56f3587781a3d6e6ce66e65ceada14beb7664b8
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL -o tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-${TAILWIND_ARCH}" \
    && echo "${TAILWIND_SHA256}  tailwindcss" | sha256sum -c - \
    && chmod +x tailwindcss
COPY kubo/api/styles ./kubo/api/styles
COPY kubo/api/templates ./kubo/api/templates
RUN ./tailwindcss -i kubo/api/styles/input.css -o /build/app.css --minify

# --- Stage runtime ---
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# tzdata: a imagem slim não traz /usr/share/zoneinfo, e o scheduler valida a
# timezone do schedules.yaml (America/Sao_Paulo) via zoneinfo no startup — sem
# isto ele levanta ZoneInfoNotFoundError e não sobe.
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Camada de deps primeiro (só manifest+lock): não invalida o cache a cada mudança
# de código. `--no-install-project` = instala dependências, não o pacote kubo ainda.
# README.md entra aqui porque o build do wheel (hatchling) lê o readme do pyproject.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Código + dados de runtime: o scheduler lê schedules.yaml + destinations.yaml
# (ADR-0015); o runner lê catalogs/integrations.
COPY kubo/ ./kubo/
COPY catalogs/ ./catalogs/
COPY schedules.yaml destinations.yaml ./
# Scripts one-off (import legado, backfill, e a Trilha B do dreno 0014): rodam no
# servidor via `docker compose run --rm kubo-scheduler python -m scripts.X` (mesmo
# esquema do `python -m` do scheduler, CWD /app). Sem eles na imagem, o one-off não
# acha o arquivo. `scripts` é namespace package (sem __init__): `-m scripts.X` acha
# pelo CWD /app.
COPY scripts/ ./scripts/
# CSS gerado pelo stage tailwind (não vem do host — ver .dockerignore). Servido de
# /app/kubo/api/static pelo StaticFiles (o app roda do source /app/kubo via CWD do
# `python -m`, não do wheel — mesmo esquema do scheduler que lê schedules.yaml).
COPY --from=tailwind /build/app.css ./kubo/api/static/app.css
RUN uv sync --frozen --no-dev --no-editable

# non-root: uid fixo alto, dono de /app (inclui o .venv criado acima).
RUN useradd --create-home --uid 10001 kubo && chown -R kubo:kubo /app
USER kubo

ENV PATH="/app/.venv/bin:$PATH"

# Identidade do build, injetada pelo deploy (token único por deploy: SHA curto +
# timestamp). É a ÚLTIMA camada de propósito — só ela rebuilda quando o id muda, e o
# `deploy.sh` a lê via `compose exec printenv` para provar que o container roda a
# imagem recém-buildada (guard honesto: rsync deploya a working tree, não o HEAD, então
# comparar image-id/SHA não basta — ver docs/runbook-deploy.md). Default para builds
# avulsos (`docker build` sem --build-arg) não quebrarem.
ARG KUBO_BUILD_ID=unknown
ENV KUBO_BUILD_ID=$KUBO_BUILD_ID

# BlockingScheduler síncrono; SIGTERM faz shutdown que espera a run em voo
# (compose dá stop_grace_period 60s para isso completar).
CMD ["python", "-m", "kubo.scheduler"]
