# ADR-0034 — Topologia da PRD: LXC irmão no oute-server (emenda ao ADR-0011 §IV)

> Status: **aceito** · Data: 2026-07-22 · **Emenda o ADR-0011 §IV** (a promessa "PRD/OCI mantém AppArmor intocado" cai).

## Contexto

O mapa de wayfinder [KUBO-72](https://oute.atlassian.net/browse/KUBO-72) leva o Kubo a produção. O dono decidiu (D-a) que a **PRD roda num LXC irmão do `kubo-test`** no mesmo `oute-server`, porta 2900 (já reservada no `PORTS.md` do host), **não** numa compute instance dedicada na OCI. O `oute-server` já é uma VPS na própria OCI, então "na minha VPC" vale para os dois candidatos — o desempate foi caminho mais curto (Tailscale, Docker aninhado, runbook e backup já existentes).

O **ADR-0011 §IV** prometeu literalmente *"PRD/OCI mantém AppArmor intocado"* — premissa escrita assumindo a PRD como instância OCI dedicada rodando **Docker nativo**. Uma PRD como LXC irmão tem o **mesmo Docker aninhado** do `kubo-test`, que só funciona com `raw.lxc: lxc.apparmor.profile=unconfined` + o `dpkg-divert` do `apparmor_parser` (ADR-0011 §IV). A promessa quebra de qualquer forma; este ADR decide **como**, e o resto do isolamento DEV↔PRD no mesmo host. Detalhe em [KUBO-73](https://oute.atlassian.net/browse/KUBO-73).

## Decisão

### I. AppArmor: PRD copia o DEV, risco reaceito por nome

A PRD copia a config do `kubo-test`: `unconfined` + `dpkg-divert`. **A promessa do ADR-0011 §IV cai**, com o risco reaceito por escrito: produção exposta à internet roda com confinamento de container **mais fraco** — um RCE no `kubo-api` tem escape de container mais fácil.

Fundamento de por que `allow_nesting` (que daria confinamento de verdade) **não** é adotado:
1. Os segredos que importam (chave de LLM, token do Telegram) já estão **dentro** do container exposto por definição — o AppArmor não os protege. Contra a ameaça nomeada, `allow_nesting` não move a agulha.
2. A fronteira real com o host continua sendo o **LXC unprivileged** (ADR-0011 §I), intacta nas duas opções; o AppArmor só endureceria container→rootfs-do-LXC.
3. `lxc.apparmor.allow_nesting` **daria** confinamento real (não é "testado e falhou" — o que falhou no §I foi o `security.nesting`, knob diferente; o `allow_nesting` foi rejeitado no §IV por complexidade/benefício marginal), mas é caminho **não-provado neste host** e adiciona complexidade contra a premissa de fadiga de complexidade.

`allow_nesting` fica como **dívida reabrível** se a PRD um dia sair do LXC para instância dedicada.

### II. Recurso: "PRD sagrada"

DEV **capado** (limites apertados), PRD com **garantia de recurso + boot priority**, **storage pools separados por LXC** (disco cheio no DEV não mata a PRD). Coerente com o DEV virar homologação (D-c).

**Gate de viabilidade (não cosmético):** o DEV já está em 3 GiB/2 vCPU. Os números exatos saem de `free -h`/`nproc`/`df -h` no host, no build. Se a folga do host vier magra, a política aperta o DEV demais — **flag-back antes de cravar os números**.

### III. Destino-de-falha residual aceito

Falha de **nível host** (reboot, LXD, kernel panic) continua **compartilhada** entre DEV e PRD e é **aceita** como inerente à D-a. O split de recurso/storage cobre só faminto e disco-cheio, não falha de host.

### IV. Separação por ambiente

- **Rede:** proxy device próprio no IP Tailscale do host para a 2900 (espelha a emenda §III do ADR-0011, que usa 3900 no DEV; `nat=true`). `lxdbr0` compartilhada. Risco da §III-emenda carrega (bind na bridge alcançável por outros LXCs) — a defesa é a auth (ADR-0036). SurrealDB nunca exposto.
- **Tailscale:** `tailscaled` do host compartilhado; os dois proxy devices escutam no mesmo IP Tailscale, portas distintas (3900 DEV, 2900 PRD).
- **Credenciais SurrealDB:** separadas por ambiente (containers e storage distintos). Cada **root pass 32+ aleatória** (hazard `INFO FOR ROOT` da emenda 0010 do ADR-0014). `kubo_ro`/`kubo_rw` próprios por ambiente.
- **Backup:** destino e retenção **separados por ambiente** — a cadeia `sidecar→bind→host` do ADR-0011 §VI escreve em `~/backups/kubo`; se a PRD herdar o overlay do DEV, os backups cruzam e o restore em dois passos não os distingue. PRD escreve em caminho próprio.

## Consequências

- **Positivo:** caminho mais curto (infra do host reusada); PRD isolada de faminto e disco-cheio do DEV.
- **Trade-off de segurança nomeado:** produção exposta roda sem AppArmor no Docker aninhado; a verba de segurança vai para a auth (ADR-0036) e para reduzir o que o container exposto carrega, não para o AppArmor.
- **Fragilidade aceita:** DEV e PRD dividem hardware, kernel e destino de falha de host.
- **Backup off-site da PRD** (destino próprio, ex.: bucket OCI) fica como trabalho de build futuro (dívida KUBO-12).

## Alternativas rejeitadas

- **Compute instance dedicada na OCI** — decisão do dono (D-a): provisionar do zero por benefício de isolamento não priorizado agora; direção reabrível.
- **`lxc.apparmor.allow_nesting`** — daria confinamento real, mas é não-provado neste host e complexo por benefício marginal (§I acima); dívida reabrível.
