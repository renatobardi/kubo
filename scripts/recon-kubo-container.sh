#!/usr/bin/env bash
# recon-kubo-container.sh — inventário SÓ-LEITURA do ambiente do Kubo (LXC kubo-test).
# Roda DENTRO do kubo-test (ssh kubo-test), na pasta ~/kubo.
# Nada é alterado. NÃO imprime segredos: do .env mostra só os NOMES das chaves, nunca valores.
# Uso:  cd ~/kubo && bash recon-kubo-container.sh
set -uo pipefail
line(){ printf '\n=== %s ===\n' "$1"; }
have(){ command -v "$1" >/dev/null 2>&1; }

line "IDENTIDADE"
uname -a; echo "hostname: $(hostname)"
[ -f /etc/os-release ] && . /etc/os-release && echo "distro: $PRETTY_NAME"

line "REDE — IPs deste container"
ip -br addr 2>/dev/null || hostname -I
have tailscale && { echo "-- tailscale --"; tailscale ip 2>/dev/null; tailscale status 2>/dev/null | head -3; } || echo "sem tailscale aqui (bind é no host)"

line "DOCKER COMPOSE — serviços do Kubo"
if have docker; then
  docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}' 2>/dev/null \
    || docker compose ps 2>/dev/null
  echo "-- portas publicadas por container --"
  docker ps --format '{{.Names}}: {{.Ports}}' 2>/dev/null
  echo "-- rede interna do compose --"
  docker network ls 2>/dev/null | grep -i kubo
fi

line "compose file — services, portas e depends (topologia, sem segredo)"
f=docker-compose.yml; [ -f compose.yaml ] && f=compose.yaml
if [ -f "$f" ]; then
  grep -nE '^\s{2}[a-z0-9_-]+:|image:|ports:|- "?[0-9]|expose:|depends_on:|container_name:' "$f" | head -60
else echo "compose não encontrado no diretório atual"; fi

line ".env — SÓ OS NOMES das variáveis (valores NUNCA)"
if [ -f .env ]; then
  grep -vE '^\s*#|^\s*$' .env | sed -E 's/=.*/=<oculto>/' | sort
else echo ".env não encontrado no diretório atual"; fi

line "kubo-api — como escuta hoje (porta interna do uvicorn)"
grep -rhoE 'uvicorn[^&|]*|--host[^ ]* [^ ]*|--port[^ ]* [^ ]*|port=[0-9]+|0\.0\.0\.0:[0-9]+' "$f" 2>/dev/null | sort -u | head -20
echo "-- healthz responde? --"
curl -s -m 5 -o /dev/null -w "kubo-api /healthz interno: %{http_code}\n" http://localhost:8000/healthz 2>/dev/null \
  || echo "porta 8000 local não respondeu (talvez publique noutra porta)"

line "TLS / cert já presente no container?"
ls -1 /etc/ssl/certs/ 2>/dev/null | grep -iE 'kubo|oute' || echo "sem cert kubo/oute no container (esperado)"

line "FIM — cole a saída no Cowork"
