# ADR-0011 — Topologia de deploy no oute-server (LXD + Docker aninhado)

> Status: **aceito** · Data: 2026-07-05
> · **Emendado por ADR-0034** (§IV: a promessa "PRD/OCI mantém AppArmor intocado" cai — a PRD é LXC irmão, mesmo Docker aninhado do kubo-test)
> · **Estendido por ADR-0037** (a direção não-normativa "build-once / promote-by-tag" vira normativa na esteira de CD).

## Contexto

O Kubo saiu do Mac (M5.5, sessão 0006): primeira execução em servidor. O ambiente
de produção do dono é a VPC da Oracle Cloud (OCI), mas a fase 1 roda num container
**LXC** (`kubo-test`) no servidor `oute-server` (alias ssh `oracle-arm`, aarch64,
LXD 5.21, Ubuntu 24.04), com **Docker aninhado** rodando o compose. Este ADR
registra a topologia efetiva e as decisões que a sustentam — várias delas
**emendas ao plano da sessão**, tomadas contra evidência empírica do próprio
servidor (o plano assumiu premissas que o pre-flight provou falsas).

O escopo é DEV. PRD (build-once/promote-by-tag na OCI) fica como direção
**não-normativa** (seção final) — registrada, não decidida.

## Decisão

### I. Container: cópia do vizinho, não derivação

`kubo-test` replica a config dos 8 vizinhos que já rodam Docker aninhado no host:
**`raw.lxc: lxc.apparmor.profile=unconfined`**, profile `default`, storage LXD
`dir`. Sobre essa base, o plano adiciona: `limits.memory=3GiB`, `limits.cpu=2`,
`boot.autostart=true`, IP estático `.18`.

**Divergência da prosa do plano (registrada, não silenciosa):** o plano 6.1.2
prescreveu `security.nesting=true` "para replicar o vizinho". O pre-flight provou
que os vizinhos **não** têm `security.nesting` — o que habilita o Docker aninhado
é o `raw.lxc: apparmor.profile=unconfined`. Testei `security.nesting=true` e ele
**não** resolveu o problema de apparmor (ver §IV); revertido para casar com o
vizinho exatamente. Princípio: **evidência de 8 vizinhos funcionando > suposição da
prosa.** Nota honesta de segurança: `apparmor unconfined` é confinamento **mais
fraco** que nesting — remove o perfil AppArmor do container. Aceitável em DEV
Tailscale-only; revisitar em PRD.

Docker interno: pacote **distro `docker.io` (29.1.3-0ubuntu3)** + `docker-compose-v2`,
idêntico ao vizinho. **Não** docker-ce do docker.com — o docker-ce falhou no
apparmor onde o distro (ajustado para LXD) funcionaria; copiar venceu derivar.
Storage driver interno: **`overlayfs`** (saudável; o gatilho de PARADA do plano era
`vfs`, que não ocorreu).

### II. Rede: IP estático via netplan, DNS explícito no dockerd

**IP estático `.18` via netplan**, não DHCP. O DHCPv4 nunca disparou apesar da
reserva `ipv4.address` no LXD (quirk conhecido do LXD ao adicionar lease estático
pós-boot). Config estática é mais correta para servidor de IP fixo e remove a
dependência frágil. `cloud-init` de rede desabilitado
(`/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg`) para o netplan estático
sobreviver a reboot.

**DNS explícito no daemon Docker** (`/etc/docker/daemon.json`: `dns: [1.1.1.1,
1.0.0.1]`). O resolver do bridge LXD (`10.173.117.1`) **não responde** a queries
vindas de containers Docker aninhados — o build (`uv sync` baixando wheels) e o
runtime (feed worker resolvendo URLs de RSS) falhavam sem isto. Necessário, não
opcional.

### III. Portas: PORTS.md do host é lei, Tailscale-only

Semântica das faixas no `oute-server`: **3000–3999 = DEV** (Kubo na **3900**),
**2900 = PRD reservada** (firewall só abre com gate próprio). A faixa DEV está
aberta AO MUNDO no firewall.

**A fronteira de segurança do Kubo é o bind no IP Tailscale (`100.66.254.24`), NÃO
o firewall.** Como a faixa DEV é pública, a porta do Kubo **nunca** ganha proxy
device em `0.0.0.0`. Nesta fase a API não existe (fase 2) — a 3900 fica só
**registrada** no PORTS.md do host, sem proxy device, sem subdomínio nginx. Nada
escuta. SurrealDB nunca é exposto (sem `ports:` no compose — invariante de rede do
CLAUDE.md).

> **Emenda (sessão 0009, fase 2 — validada pelo advisor):** a API da fase 2 existe.
> Descoberta empírica: o `100.66.254.24` é o `tailscale0` do **host**, NÃO existe
> dentro do LXC — o publish do compose (que roda no LXC) não consegue bindá-lo. O
> *executor* do bind Tailscale-only passa a ser um **LXD proxy device no host**:
> `lxc config device add kubo-test kubo-ui proxy listen=tcp:100.66.254.24:3900
> connect=tcp:10.173.117.18:3900 nat=true`. O compose (overlay dev) publica no IP de
> **bridge** do LXC (`10.173.117.18:3900`, RFC1918, interno ao host — coerente com a
> proibição de `0.0.0.0`), e o device encaminha o IP Tailscale para lá. `nat=true` =
> DNAT de kernel (sem listener no host, persiste em reboot, sem corrida de boot com o
> tailscaled). O princípio de §III fica intacto — muda só o instrumento do bind, do
> `ports:` do compose para o device do LXD. **Risco aceito:** o bind na bridge é
> alcançável por outros LXCs da `lxdbr0`; o login de browser (ADR-0014) é a defesa em
> DEV. Setup, smoke e reversão em `runbook-deploy.md` §2b.

### IV. AppArmor userspace desabilitado no kubo-test (efeito: o dockerd deixa de aplicar perfis) — decisão de segurança explícita

> **Emenda (ADR-0034, 2026-07-22):** a nota "PRD/OCI mantém AppArmor intocado" abaixo assumia a PRD como instância OCI dedicada com Docker nativo. A PRD nasceu como **LXC irmão** do kubo-test (mesmo Docker aninhado), então ela também roda `unconfined` + `dpkg-divert` — o risco é reaceito por nome no ADR-0034.

**AppArmor userspace é desabilitado no kubo-test** via `dpkg-divert` do
`/usr/sbin/apparmor_parser`. O divert remove o parser do caminho, então o dockerd
para de aplicar perfis AppArmor (efeito prático desejado); como o LXC já é
unconfined por fora, o container inteiro fica sem AppArmor userspace, sem perda
real. PRD/OCI mantém AppArmor intocado.

Fundamento (evidência primária): num LXC unprivileged o dockerd falha ao verificar
o perfil `docker-default` e recusa executar. O erro é literal e explícito —
`Could not check if docker-default AppArmor profile was loaded: open
/sys/kernel/security/apparmor/profiles: permission denied`: o securityfs do host é
visível mas ilegível para o container unprivileged, e a **verificação** do perfil
falha na leitura. Isso quebra `docker run` E `docker exec` — e `docker exec` é
load-bearing: healthcheck honesto do SurrealDB, `depends_on: service_healthy`,
verificação de restore (§VI), debug operacional. Como o LXC já é
apparmor-unconfined na camada externa, confinar os containers aninhados adicionava
confinamento marginal; desligá-lo alinha o dockerd à realidade do host-container.

Mecanismo: `dpkg-divert` (não mover o binário à mão) porque o dpkg honra a
diversão através de reinstalações/upgrades (`unattended-upgrades` incluso) e
`dpkg-divert --list` se autodocumenta. **Verificado sobrevivendo a `lxc restart`
frio** (divert + autostart + restart policy religam tudo sozinhos). Reversão de uma
linha (`dpkg-divert --remove`).

**Alternativas rejeitadas (com evidência):**
- `security_opt: apparmor=unconfined` no compose — **insuficiente**: conserta
  `docker run` mas o dockerd aplica `docker-default` no `docker exec`
  independentemente do `security_opt`; healthcheck ficou `unhealthy`
  (`docker exec` → exit **126**). Testado e descartado.
- Desabilitar healthcheck + rebaixar `depends_on` para `service_started` + rotear
  todo exec para `docker run` — imposto operacional permanente + sinal falso
  (`unhealthy` eterno no `ps`) para preservar confinamento marginal.
- `systemctl mask apparmor` — não remove o parser; o dockerd continua tentando.
- `lxc.apparmor.allow_nesting` no host — faria o confinamento funcionar de verdade;
  complexidade por benefício marginal em DEV.

### V. Compose: principal portável (PRD), overlay dev mínimo

O `docker-compose.yml` é o artefato de PRD (mantém AppArmor, backup em volume
nomeado, sem workarounds). O deploy DEV sobrepõe **`compose.dev-lxc.yml`** via
`COMPOSE_FILE=docker-compose.yml:compose.dev-lxc.yml` no `.env` do servidor
(ambiente-específico, não commitado). Com o apparmor resolvido no ambiente (§IV), o
overlay ficou **mínimo**: só remapeia o volume `surreal-backups` para um bind no
`/backups` do container. Segredos só no `.env` (invariante 8), criado e verificado
**pelo dono**, `chmod 600` — o agente nunca leu nem escreveu.

Serviço `kubo-scheduler`: `restart: unless-stopped`, `stop_grace_period: 60s` (o
SIGTERM do BlockingScheduler espera a run em voo — o default de 10s a mataria),
log rotation `json-file` (o scheduler emite JSON continuamente). Sem healthcheck no
scheduler (BlockingScheduler sem porta não tem check honesto; a restart policy é o
mecanismo). Migrations aplicadas por runner explícito de deploy
(`python -m kubo.store.migrations`), desacoplado do boot do scheduler.

### VI. Backup: cadeia até o host, sem OCI, restore em dois passos

Sidecar alpine dumpa o endpoint HTTP `/export` do SurrealDB (D2). **Sem upload
externo** (D2 emendada): o dump é encadeado até o host —
`sidecar → bind /backups → disk device LXD (shift=true) → oute-server:~/backups/kubo`
— e **sobrevive a `lxc delete kubo-test`**. Retenção 7d no próprio loop
(`find /backups -name 'kubo-*.surql' -mtime +7 -delete` — nunca `rm -rf`). O rsync
do Mac puxa do host (launchd, tarefa do dono — runbook). Bucket OCI fica para PRD.

**Restore em DOIS PASSOS (defeito descoberto e contornado):** o `/export` do
SurrealDB v3.1.5 emite as tabelas em ordem **alfabética**, então as relações
`collected_by` e `from_source` (ambas `ENFORCED`, IN `item`) são inseridas **antes**
de `item`/`source` existirem → o import aborta (`record 'item:…' does not exist`).
O restore separa o dump: **passo 1** aplica tudo menos `INSERT RELATION` (schema +
tabelas base); **passo 2** aplica só as relações, agora que os endpoints existem.
Testado ponta a ponta: dump com 1982 itens restaurado num banco efêmero → `item`
1982, `source` 6, `from_source` 1982 (contagem > 0, casando com o vivo). Invocação
literal no runbook. Defeito observado no `/export` do v3.1.5 (par SDK↔server pinado
por evidência — ADR-0005): bump do pin exige revalidar se o restore em dois passos
ainda é necessário (o bug pode ter sido corrigido ou mudado de forma).

### VII. Transporte: rsync + ProxyJump, zero credencial Git no servidor

Deploy por **rsync** direto do Mac (`rsync -az --delete` com excludes
`.git .venv __pycache__ .pytest_cache .ruff_cache .coverage` e `.env`), via
**ProxyJump** (`~/.ssh/config`: `kubo-test` → `HostName 10.173.117.18` →
`ProxyJump oracle-arm`, chave `kura_deploy`). O `--exclude='.env'` garante que o
`--delete` nunca apaga nem sobrescreve o `.env` do servidor. Zero credencial Git no
servidor; deploy key fica como alternativa futura.

## Consequências

- **Divergência do vizinho no host:** o kubo-test tem `dpkg-divert` do apparmor_parser
  e `daemon.json` com DNS — deltas do vizinho, mas cada um é decisão de uma linha,
  documentada, com reversão de uma linha.
- **`docker exec` é operacional** no kubo-test (divert), então healthcheck, restore
  e debug funcionam normalmente.
- **Fragilidade conhecida:** se uma versão futura do Docker falhar duro na ausência
  do `apparmor_parser` (em vez de degradar), revisitar divert vs. `allow_nesting`.

## Estado-alvo (seção NÃO-NORMATIVA — direção, não decisão)

> **Realizado (2026-07-22):** a direção abaixo virou decisão nos **ADR-0034** (topologia PRD como LXC irmão), **ADR-0035** (exposição), **ADR-0037** (build-once/promote-by-tag, agora **normativo**) e **ADR-0038** (distribuição). A PRD deixou de ser direção não-normativa — é decisão aceita nesses ADRs. O texto abaixo fica como registro histórico do que se antecipava aqui.

DEV (kubo-test) → Aprovação → PRD (OCI). Direção registrada: **build-once /
promote-by-tag** (imagem construída uma vez, promovida por tag; sem rebuild por
ambiente), CD/registry como evolução. **A questão dos dados na promoção fica
ABERTA** (migrar dump? começar limpo em PRD?) — não decidida aqui. Alternativas
futuras: deploy key (em vez de rsync do Mac), bucket OCI para backup (em vez da
cadeia até o host).
