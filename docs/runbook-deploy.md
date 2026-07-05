# Runbook — Deploy do Kubo no oute-server (DEV)

Operação do deploy fundacional (M5.5, ADR-0011). Ambiente: container LXC
`kubo-test` (`10.173.117.18`) no `oute-server`, Docker aninhado, Tailscale-only.
Acesso do Mac: `ssh kubo-test` (ProxyJump por `oracle-arm`).

> **Segredos:** o `.env` de `~/kubo/.env` é criado e verificado **pelo dono**,
> `chmod 600`. Nenhum passo aqui lê ou escreve o `.env`.

---

## 1. Setup do container (uma vez — já feito, aqui para reprovisionar)

```bash
# no host (oracle-arm):
lxc init ubuntu:24.04 kubo-test
lxc config set kubo-test raw.lxc "lxc.apparmor.profile=unconfined"   # copia do vizinho
lxc config set kubo-test limits.memory 3GiB
lxc config set kubo-test limits.cpu 2
lxc config set kubo-test boot.autostart true
lxc config device override kubo-test eth0 ipv4.address=10.173.117.18
lxc start kubo-test
# disk device de backup (dump sobrevive a lxc delete):
mkdir -p ~/backups/kubo
lxc config device add kubo-test backups disk source=$HOME/backups/kubo path=/backups shift=true
```

Dentro do `kubo-test`, três ajustes de AMBIENTE (ADR-0011 §I,II,IV):

```bash
# IP estatico (DHCP nao dispara com lease estatico do LXD):
cat > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg <<'EOF'
network: {config: disabled}
EOF
cat > /etc/netplan/50-cloud-init.yaml <<'EOF'
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: false
      addresses: [10.173.117.18/24]
      routes: [{to: default, via: 10.173.117.1}]
      nameservers: {addresses: [10.173.117.1]}
EOF
chmod 600 /etc/netplan/50-cloud-init.yaml && netplan apply

# Docker distro (igual ao vizinho — NAO docker-ce):
apt-get install -y docker.io docker-compose-v2 && systemctl enable --now docker
usermod -aG docker ubuntu

# DNS explicito (o resolver do bridge LXD nao responde a container Docker aninhado):
cat > /etc/docker/daemon.json <<'EOF'
{ "dns": ["1.1.1.1", "1.0.0.1"] }
EOF

# AppArmor OFF so para o dockerd (senao docker run/exec quebram no LXC unprivileged):
dpkg-divert --local --rename --divert /usr/sbin/apparmor_parser.disabled --add /usr/sbin/apparmor_parser
systemctl restart docker
```

Verificação do setup: `docker run --rm hello-world` e
`docker run -d --name t alpine sleep 5 && docker exec t echo ok && docker rm -f t`
(exec DEVE funcionar — healthcheck/restore dependem dele).

`unattended-upgrades`: default do Ubuntu (security pocket) fica ligado. O
`dpkg-divert` sobrevive a reinstalação do pacote apparmor.

---

## 2. Deploy / atualização

Do **Mac**, na raiz do repo:

```bash
rsync -az --delete \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='.coverage' \
  --exclude='.env' \
  ./ kubo-test:~/kubo/
```

No `kubo-test` (`cd ~/kubo`), na ordem — SurrealDB saudável ANTES das migrations:

```bash
docker compose build
docker compose up -d surrealdb
# esperar healthy:
until [ "$(docker inspect -f '{{.State.Health.Status}}' kubo-surrealdb-1)" = healthy ]; do sleep 3; done
docker compose run --rm kubo-scheduler python -m kubo.store.migrations   # idempotente
docker compose up -d
```

`docker compose config` (uma vez) confirma que o overlay dev mergeou
(`COMPOSE_FILE` no `.env`): `surreal-backups` com `device: /backups`.

---

## 3. Observabilidade

Fase 1 = logs, não dashboard. Scheduler sem porta não tem healthcheck honesto; a
`restart: unless-stopped` é o mecanismo.

```bash
docker compose ps                          # estado + health do surrealdb
docker compose logs -f kubo-scheduler      # jobs, coletas (feed_collected), erros
docker compose logs backup                 # dump diario
```

O scheduler loga `scheduler_starting jobs=6 timezone=America/Sao_Paulo` no boot e
`feed_collected ... items=N` a cada coleta.

**Coleta manual (smoke / fora do cron das 08:00):**

```bash
docker compose run --rm kubo-scheduler python -c "
from kubo.scheduler import load_schedules, execute_job
for e in load_schedules().schedules:
    execute_job(e.worker, e.config)
"
```

**Query de verificação (contagem de proveniência):**

```bash
docker compose run --rm kubo-scheduler python -c "
from kubo.store import client
with client.connect(client.config()) as db:
    for t in ['item','source','run']:
        r = db.query(f'SELECT count() FROM {t} GROUP ALL;')
        print(t, r[0]['count'] if r else 0)
"
```

**Runs órfãos:** uma run que ficou em `running` (processo morto no meio). Listar:
`... db.query(\"SELECT id, started_at FROM run WHERE status = 'running';\")`. Na
fase 1 não há reconciliação automática (ADR-0009) — inspeção manual.

---

## 4. Backup e restore

Dump diário automático pelo sidecar → `oute-server:~/backups/kubo/kubo-<TS>.surql`
(retenção 7d). Sobrevive a `lxc delete kubo-test`.

### Restore (DOIS PASSOS — obrigatório, ADR-0011 §VI)

O `/export` do SurrealDB ordena tabelas alfabeticamente, então as relações
`ENFORCED` (`collected_by`, `from_source`) vêm antes de `item`/`source` e o import
de passo único aborta. Separar em base-primeiro, relações-depois:

```bash
# no kubo-test:
DUMP=$(ls -S /backups/kubo-*.surql | head -1)     # ou o dump desejado
mkdir -p ~/restore-tmp
grep -v '^INSERT RELATION' "$DUMP" > ~/restore-tmp/pass1.surql
{ echo 'OPTION IMPORT;'; grep '^INSERT RELATION' "$DUMP"; } > ~/restore-tmp/pass2.surql

# banco efemero para VALIDAR o dump (nao toca o vivo):
docker run -d --name restore-test -v ~/restore-tmp:/rt \
  surrealdb/surrealdb:v3.1.5 start --user root --pass root memory
sleep 6
for p in pass1 pass2; do
  docker exec restore-test /surreal import --endpoint http://localhost:8000 \
    --username root --password root --namespace kubo --database kubo /rt/$p.surql
done
# contagem > 0 = restore real:
printf 'SELECT count() FROM item GROUP ALL;\n' | docker exec -i restore-test \
  /surreal sql --endpoint http://localhost:8000 --username root --password root \
  --namespace kubo --database kubo
docker rm -f restore-test; find ~/restore-tmp -type f -delete; rmdir ~/restore-tmp
```

Para restaurar no banco VIVO: mesma sequência de import, mas apontando o
`--endpoint`/creds para o SurrealDB de produção (com a stack parada e o volume
`surreal-data` limpo, se for substituição total).

### Rsync do dump para o Mac (tarefa do dono — launchd)

O dump vive no host; puxar para o Mac é responsabilidade do dono. Receita launchd
(`~/Library/LaunchAgents/pro.oute.kubo-backup.plist`), roda diário:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>pro.oute.kubo-backup</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/rsync</string><string>-az</string>
    <string>oracle-arm:backups/kubo/</string>
    <string>/Users/bardi/Backups/kubo/</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>30</integer></dict>
</dict></plist>
```

Ativar: `mkdir -p ~/Backups/kubo && launchctl load ~/Library/LaunchAgents/pro.oute.kubo-backup.plist`.
(Rsync puxa via `oracle-arm`; os dumps são world-readable, o `ubuntu` os alcança.)

---

## 5. Cheat-sheet

| Ação | Comando |
|---|---|
| Entrar | `ssh kubo-test` |
| Estado | `cd ~/kubo && docker compose ps` |
| Logs scheduler | `docker compose logs -f kubo-scheduler` |
| Reiniciar stack | `docker compose restart` |
| Parar/subir | `docker compose down` / `docker compose up -d` |
| Reboot container | `ssh oracle-arm lxc restart kubo-test` (religa tudo sozinho) |
