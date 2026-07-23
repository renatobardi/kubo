# ADR-0037 — Esteira de CD: build-once, promoção via Tailscale, gate de aprovação da PRD

> Status: **aceito** · Data: 2026-07-22 · Torna **normativa** a direção "build-once / promote-by-tag" da seção não-normativa do ADR-0011. Fecha a dívida KUBO-11 (CD inexistente).

## Contexto

Não existe CD: o `scripts/deploy.sh` roda **do Mac do dono** (rsync + ssh com ProxyJump) e **builda no host, por ambiente**. O dono quer: merge em `main` → deploy **automático** no DEV (homologação); PRD só **após aprovação dele** na esteira, podendo negar. Fatos levantados em [KUBO-77](https://oute.atlassian.net/browse/KUBO-77); desenho em [KUBO-80](https://oute.atlassian.net/browse/KUBO-80).

## Decisão

### I. Artefato: build-once / promote-by-tag

Imagem construída **uma vez por commit** no runner **`ubuntu-24.04-arm` nativo** (grátis e ilimitado em repo público — não há custo de cross-build, a objeção de arm64 caiu), publicada no **GHCR** taggeada por SHA. O guard de deploy migra de comparar `KUBO_BUILD_ID` para **comparar o digest** da imagem viva contra o publicado — garantia mais forte, cobre conteúdo. **O que foi homologado é literalmente o que vai para a PRD.**

### II. Transporte: a esteira empurra via Tailscale

O job da esteira entra na tailnet como **nó efêmero** (`tailscale/github-action`) e faz **ssh no ambiente** para puxar o digest, subir e rodar migrations. Preserva o `deploy.sh`/`deploy-remote.sh` (troca o Mac pelo runner). **Acopla com o gate:** o Environment gateia o *job de deploy*, os segredos só existem após o "sim", e a falha volta ao run do GitHub.

### III. Gate: Environment `prd` com required reviewer

Environment de produção com **required reviewer = o dono**, **sem `prevent self-review`** (o dono dispara e aprova; ligá-lo tornaria todo deploy de PRD eternamente inaprovável). Rejeitar reprova o workflow; os segredos do environment só existem após a aprovação. (Disponível de graça: o repo é público.)

### IV. Disparo da PRD: `workflow_dispatch` com binding mecânico do digest

O disparo da promoção é **manual** (`workflow_dispatch`). Para não repetir o pecado do rebuild-por-ambiente (promover-qualquer-coisa = homologação sem sentido), o binding é **mecânico**: o job **lê o digest vivo no `kubo-test`** (inspeção do container em execução, pela ssh que já existe) e promove **exatamente aquele** — sem rebuild, sem "latest", sem digitar SHA.

**Race do DEV-que-andou:** o DEV faz auto-deploy a cada merge, então entre homologar o digest X e disparar a promoção, um merge novo pode ter trocado o digest. Guarda: a **tela de aprovação do Environment mostra o digest + o commit de origem** — a aprovação confirma os *bytes*, não é carimbo.

**Rollback:** disparar o último digest bom.

### V. Migrations, seed e backup

- **Migrations no próprio job**, via ssh → falha aparece no run.
- ⚠️ **Backup-antes-de-migrar na PRD:** migração forward-only falhando sobre dado real **não tem rollback**; surfacear ao run ≠ recuperar. Usar o **backup por-ambiente** (ADR-0034 §IV) como snapshot pré-migração na PRD.
- **Seed ≠ migrations:** o seed (só-fontes DEV→PRD, dado one-time) é passo separado e único; migrations (schema) rodam a cada deploy.

### VI. Segredos e edição

- **Tailscale por OIDC/WIF** (`id-token: write`, sem segredo de longa duração — preferível ao OAuth client) + **chave ssh de deploy**; a ACL com tag alcança **kubo-test E kubo-prd**.
- **GHCR público:** zero credencial no host → **preserva a propriedade "nenhum segredo persistente no host"** que motivou o push-via-Tailscale. Pré-condição: o build **não assa nenhum segredo** na imagem (segredos vêm do `.env` em runtime).
- Segredos de deploy vivem como **Environment secrets do `prd`** (só existem após a aprovação); o **`.env` do servidor segue do dono** (invariante 8), nunca lido por agente.
- O arquivo de workflow de CD é **infra security-critical** (o PAT de agente não toca `.github/workflows/`); só o dono edita, por PR humano.

## Consequências

- **Positivo:** fecha KUBO-11; o deploy vira evento com log no run; homologação promove os mesmos bytes; rollback trivial por digest.
- **Trade-off:** um nó efêmero da esteira entra na tailnet a cada deploy (superfície controlada por ACL + OIDC); a regra de security list da exposição (ADR-0035) é independente desta esteira.
- **Migração:** o `deploy.sh` do Mac deixa de ser o caminho normal; fica como escape manual.

## Alternativas rejeitadas

- **GHCR + pull pelo host** (cron/watchtower/webhook) — desacopla o deploy do run do GitHub, tornando o gate de aprovação torto e escondendo falha de migration.
- **Self-hosted runner** — a doc do GitHub o desaconselha explicitamente em repositório público (fork pode rodar código no runner).
- **Rebuild por ambiente (status quo)** — o gate humano aprovaria uma intenção, não um binário; enfraquece o próprio gate e o invariante 5.
- **Disparo por tag `v*`** — o dono preferiu o botão manual (`workflow_dispatch`); o binding mecânico do digest fecha a lacuna que a tag fecharia de graça.
