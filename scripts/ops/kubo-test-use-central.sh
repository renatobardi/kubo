#!/usr/bin/env bash
# kubo-test-use-central.sh — escreve no `.env` do kubo-test a config para usar a instância SurrealDB
# central (oute-surreal-db): COMPOSE_FILE com o overlay compose.central.yml, endpoint, auth de database
# e as três credenciais (app/rw/ro). NÃO reinicia nada: a troca em si é um passo controlado à parte.
#
# Segredos: pedidos no terminal (read -s) ou lidos de KUBO_CENTRAL_APP_PASS / _RW_PASS / _RO_PASS.
# Seguem pelo stdin do ssh; nunca em argumento, log ou disco local. O `.env` anterior é guardado
# uma vez em `.env.pre-central` (modo 600) — é o rollback.
#
# Uso:
#   ./scripts/ops/kubo-test-use-central.sh --check   # só confere o ambiente (não altera nada)
#   ./scripts/ops/kubo-test-use-central.sh           # aplica no .env do kubo-test
#
# Config:
#   KUBO_REMOTE_HOST   host ssh do oute-server (default: oute-server)
#   KUBO_TEST_NAME     container LXC (default: kubo-test)
#   KUBO_CENTRAL_URL   endpoint (default: ws://10.173.117.50:8000/rpc)
#   KUBO_CENTRAL_DB    database no central (default: test)
set -euo pipefail

REMOTE_HOST="${KUBO_REMOTE_HOST:-oute-server}"
LXC_NAME="${KUBO_TEST_NAME:-kubo-test}"
CENTRAL_URL="${KUBO_CENTRAL_URL:-ws://10.173.117.50:8000/rpc}"
CENTRAL_DB="${KUBO_CENTRAL_DB:-test}"
CENTRAL_HOSTPORT="$(printf '%s' "${CENTRAL_URL}" | sed -E 's#^[a-z]+://([^/]+).*#\1#')"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "${REMOTE_HOST}")

ok()  { printf '  [ok]    %s\n' "$*"; }
die() { printf '  [ERRO]  %s\n' "$*" >&2; exit 1; }

echo "[central] pré-checagens em ${LXC_NAME}"
"${SSH[@]}" true || die "sem ssh para ${REMOTE_HOST}"
ok "ssh ${REMOTE_HOST}"
"${SSH[@]}" "lxc exec ${LXC_NAME} -- test -f /home/ubuntu/kubo/.env" </dev/null || die ".env não encontrado em ${LXC_NAME}:~/kubo"
ok ".env existe"
"${SSH[@]}" "lxc exec ${LXC_NAME} -- test -f /home/ubuntu/kubo/compose.central.yml" </dev/null \
  || die "compose.central.yml ausente em ~/kubo: faça o deploy (CD) com o overlay antes"
ok "overlay compose.central.yml presente"
"${SSH[@]}" "lxc exec ${LXC_NAME} -- bash -c 'exec 3<>/dev/tcp/${CENTRAL_HOSTPORT%%:*}/${CENTRAL_HOSTPORT##*:}'" </dev/null \
  || die "${LXC_NAME} não alcança ${CENTRAL_HOSTPORT} (IP na lista de clientes do firewall do central?)"
ok "${LXC_NAME} alcança ${CENTRAL_HOSTPORT}"

if [ "${1:-}" = "--check" ]; then
  echo "[central] --check concluído; nada foi alterado."
  exit 0
fi

ask() { # <nome da var> <rótulo>
  local var="$1" label="$2" value="${!1:-}"
  if [ -z "${value}" ]; then
    read -rsp "  senha de ${label}: " value </dev/tty; echo >&2
  fi
  [ -n "${value}" ] || die "senha de ${label} vazia"
  printf -v "${var}" '%s' "${value}"
}
ask KUBO_CENTRAL_APP_PASS "app_${CENTRAL_DB} (OWNER)"
ask KUBO_CENTRAL_RW_PASS  "rw_${CENTRAL_DB} (EDITOR)"
ask KUBO_CENTRAL_RO_PASS  "ro_${CENTRAL_DB} (VIEWER)"

# Edita o .env de forma idempotente. Lê os valores do stdin (JSON); código segue em base64.
MERGE_PY="$(cat <<'PY'
import json, os, shutil, sys
path = "/home/ubuntu/kubo/.env"
new = json.load(sys.stdin)
owner = os.stat(path)  # dono original: o `lxc exec` roda como root e não pode deixar o .env root:root
backup = path + ".pre-central"
if not os.path.exists(backup):
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    os.chown(backup, owner.st_uid, owner.st_gid)
lines = open(path).read().splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
    if key in new:
        if key == "COMPOSE_FILE":
            # só acrescenta o overlay; nunca troca a lista que o servidor já tem
            if "compose.central.yml" not in line:
                line = line.rstrip() + ":compose.central.yml"
        else:
            line = f"{key}={new[key]}"
        seen.add(key)
    out.append(line)
for key, value in new.items():
    if key not in seen:
        out.append(f"{key}={value}" if key != "COMPOSE_FILE" else f"{key}={value}:compose.central.yml")
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    fh.write("\n".join(out) + "\n")
os.chmod(tmp, 0o600)
os.chown(tmp, owner.st_uid, owner.st_gid)
os.replace(tmp, path)
print("chaves gravadas:", ", ".join(sorted(new)))
PY
)"
MERGE_B64="$(printf '%s' "${MERGE_PY}" | base64 | tr -d '\n')"

echo "[central] gravando no .env de ${LXC_NAME} (backup em .env.pre-central)"
CENTRAL_URL_="${CENTRAL_URL}" CENTRAL_DB_="${CENTRAL_DB}" \
KUBO_CENTRAL_APP_PASS="${KUBO_CENTRAL_APP_PASS}" KUBO_CENTRAL_RW_PASS="${KUBO_CENTRAL_RW_PASS}" KUBO_CENTRAL_RO_PASS="${KUBO_CENTRAL_RO_PASS}" \
python3 -c '
import json, os
d = os.environ["CENTRAL_DB_"]
print(json.dumps({
    "COMPOSE_FILE": "docker-compose.yml:compose.dev-lxc.yml",
    "SURREAL_URL": os.environ["CENTRAL_URL_"], "SURREAL_AUTH_LEVEL": "database",
    "SURREAL_NS": "kubo", "SURREAL_DB": d,
    "SURREAL_USER": "app_" + d, "SURREAL_PASS": os.environ["KUBO_CENTRAL_APP_PASS"],
    "KUBO_RW_SURREAL_USER": "rw_" + d, "KUBO_RW_SURREAL_PASS": os.environ["KUBO_CENTRAL_RW_PASS"],
    "KUBO_RO_SURREAL_USER": "ro_" + d, "KUBO_RO_SURREAL_PASS": os.environ["KUBO_CENTRAL_RO_PASS"],
}))' | "${SSH[@]}" "lxc exec ${LXC_NAME} -- sudo -u ubuntu -H python3 -c \"import base64;exec(base64.b64decode('${MERGE_B64}'))\""

echo "[central] validando o compose resultante (sem subir nada)"
# O compose lê o .env sozinho (COMPOSE_FILE e interpolação); NÃO fazer `source` dele: o .env do
# servidor tem valores com espaço sem aspas e não é shell válido.
"${SSH[@]}" "lxc exec ${LXC_NAME} -- sudo -u ubuntu -H bash -c 'cd ~/kubo && KUBO_IMAGE=x docker compose config --quiet && docker compose config --services | tr \"\\n\" \" \"'" </dev/null
echo
echo "[central] OK: .env pronto. Nada foi reiniciado; o rollback é restaurar .env.pre-central."
