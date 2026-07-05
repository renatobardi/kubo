# Sessão 0006 — Deploy fundacional no oute-server (M5.5)

> **Status:** aprovado pelo dono (2026-07-05, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5), operando o servidor via SSH
> **Timebox:** 8 horas efetivas (estimativa do advisor: M6.1 1,5h · M6.2 3h · M6.3 2h · M6.4 1,5h — sem gordura; ordem de sacrifício vale)
> **Estrutura:** 1 PR — branch `feat/0006-deploy-oute-server` (artefatos de repo: Dockerfile, compose, runbook, ADR) + operações no servidor
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

O Kubo sai do Mac: container LXC `kubo-test` no `oute-server` com Docker aninhado rodando o compose de produção (SurrealDB RocksDB persistente + scheduler), **primeira coleta real em servidor**, backup diário com **restore testado** — e a topologia registrada em ADR-0011.

## Decisões vigentes desta sessão (emendas registradas aqui; ADR-0011 formaliza)

- **D18 emendada:** `kubo-test` @ `10.173.117.18`. Semântica de faixas: **3000–3999 = DEV** (Kubo na **3900**), **PRD futuro = 2900 reservada** (firewall só abre com gate próprio). Kubo é **Tailscale-only**: a faixa DEV está aberta AO MUNDO no firewall, logo a porta do Kubo **NUNCA ganha proxy device em 0.0.0.0 — o bind no IP Tailscale (100.66.254.24) é a fronteira de segurança, não o firewall**. Nesta sessão: porta 3900 só REGISTRADA no PORTS.md, sem proxy device (nada escuta — API é fase 2). Sem subdomínio nginx.
- **D2 emendada:** SEM Object Storage (sem quota) — o **sidecar de backup já existente no compose** permanece (alcança o SurrealDB sem porta publicada), com 3 mudanças: (1) REMOVER o branch `OCI_UPLOAD_PAR_URL` (e do `.env.example`, mesmo PR); (2) retenção 7d no próprio loop (`find /backups -name 'kubo-*.surql' -mtime +7 -delete` — NUNCA `rm -rf` via ssh, ver regras do harness); (3) dump **encadeado até o host**: `oute-server:~/backups/kubo` → LXD disk device (`shift=true`) → `kubo-test:/backups` → bind do compose → sidecar. O dump sobrevive a `lxc delete kubo-test`. Rsync do Mac puxa de `oracle-arm:~/backups/kubo` (launchd — tarefa do dono, receita no runbook). Bucket OCI fica para PRD. *Plano B se `shift=true` falhar com idmap em cascata: dump em volume local + `lxc file pull` no cron do host — degrada elegância, mantém "cópia fora do container".*
- **Transporte:** ProxyJump — `~/.ssh/config` do Mac: `Host kubo-test / HostName 10.173.117.18 / User ubuntu / ProxyJump oracle-arm` (chave via `lxc file push` uma vez). Rsync direto e incremental: `rsync -az --delete` com excludes `.git .venv __pycache__ .pytest_cache .ruff_cache .coverage` e **filtro protect para `.env`** (o `--delete` JAMAIS apaga o `.env` do servidor). Zero credencial Git no servidor (deploy key = alternativa futura no ADR).
- **Limites LXD:** 3GiB RAM / 2 vCPU / `boot.autostart=true`.

## Regras da sessão — harness × ssh (pré-identificado, não descobrir na hora)

1. `rm -rf` em string ssh é BLOQUEADO pelo guard (não ancorado) → retenção sempre via `find -mtime +7 -delete`.
2. Tokens `.env` sob cat/grep em ssh são bloqueados — e isso é POLÍTICA, não bug: **o `.env` do servidor é criado e verificado PELO DONO, manualmente; o agente nunca lê nem escreve** (invariante 8 + harness alinhados). Verificações do agente: `test -f` / `stat`.
3. Se um ssh longo falso-positivar: reformular o comando, NUNCA contornar o hook.

## Marco 6.1 — Container e base

| # | Tarefa |
|---|---|
| 6.1.1 | **Pre-flight de CÓPIA, não derivação:** `lxc config show <vizinho-com-docker>` (profile/keys) + `lxc exec <vizinho> -- docker info \| grep Storage` — confirmar **overlay2**. Se vizinhos rodam `vfs`: **PARAR e consultar o advisor** (a topologia do aninhamento reabre — sem debugging heroico no timebox) |
| 6.1.2 | Criar `kubo-test`: Ubuntu LTS, `security.nesting=true` (replicar config do vizinho), IP estático `.18`, limites 3GiB/2vCPU, `boot.autostart=true`. Docker dentro (repo oficial, systemd enabled) |
| 6.1.3 | SSH direto: chave via `lxc file push`, entrada ProxyJump no `~/.ssh/config` do Mac, `ssh kubo-test` funcionando |
| 6.1.4 | Disk device do backup: `~/backups/kubo` do host → `/backups` do container (`shift=true`; plano B acima se falhar) |

## Marco 6.2 — Dockerfile + compose de produção + deploy

| # | Tarefa |
|---|---|
| 6.2.1 | **Dockerfile (artefato NOVO de repo — o maior item da sessão):** Python 3.12 + `uv sync --frozen`, usuário **non-root**, build **no servidor** (aarch64 nativo — sem registry/cross-build). Primeiro comando do marco: `docker pull surrealdb/surrealdb:v3.1.5` no servidor (confirmar arm64 sem surpresa no build) |
| 6.2.2 | Compose prod: SurrealDB **RocksDB + volume nomeado** (fora do overlay — sadio), serviço `kubo-scheduler` (`python -m kubo.scheduler`) com `restart: unless-stopped`, **`stop_grace_period: 60s`** (o default de 10s mataria uma run em voo antes do `shutdown(wait=True)` — desperdiçaria o SIGTERM limpo do M5), **log rotation** (`json-file`, `max-size`/`max-file` — scheduler loga JSON para sempre num disco de 45G; sem rotação é o incidente de 6 meses). Senha SurrealDB forte, `.env` `chmod 600` criado PELO DONO (regra 2). Migrations aplicadas via runner no deploy. Healthcheck do scheduler: **NÃO inventar** — BlockingScheduler sem porta não tem healthcheck honesto; restart policy é o mecanismo; `docker compose logs -f kubo-scheduler` é a observabilidade da fase 1 (runbook) |
| 6.2.3 | Deploy: `rsync` (config acima) + `docker compose build` + `up -d`. **Teste de persistência em DOIS níveis (obrigatório):** (a) `docker compose down && up -d` preserva dados; (b) `lxc restart kubo-test` volta com TUDO de pé sozinho (autostart + systemd + restart policy encadeados) |
| 6.2.4 | **Pendência do M5, ANTES de ligar o agendamento:** smoke manual por feed do servidor (as 6 URLs coletam de verdade — valida funcionamento; a reconciliação com nomenclatura do legado é pendência separada, cortável). `Accept-Encoding: identity` verificado contra os feeds reais (o worker degrada estruturado se rejeitado — pior caso é feed pulado, não corrupção) |
| 6.2.5 | Emendar `.env.example` (remover `OCI_UPLOAD_PAR_URL` etc., refletir D2) — mesmo PR |

## Marco 6.3 — Backup com restore TESTADO

| # | Tarefa |
|---|---|
| 6.3.1 | Sidecar ajustado (D2 emendada: sem OCI, retenção 7d via `find`, dump no bind `/backups` → host) |
| 6.3.2 | **Restore testado — critério inegociável:** dump restaurado num banco efêmero NO PRÓPRIO kubo-test (`docker run --rm surrealdb/surrealdb:v3.1.5 start memory` + `/surreal import`) e **query de proveniência retornando contagem > 0** (coletar ANTES de dumpar — restore de dump vazio é teatro). Invocação exata pré-escrita no runbook (exec form: `docker exec surrealdb /surreal sql --endpoint http://localhost:8000 ...` — imagem distroless, sem shell) |
| 6.3.3 | Receita launchd + rsync para o Mac (`oracle-arm:~/backups/kubo` → pasta local), passo a passo literal no runbook — **tarefa do dono** |

## Marco 6.4 — Registro

| # | Tarefa |
|---|---|
| 6.4.1 | **ADR-0011 — topologia de deploy:** LXD + Docker aninhado (overlay2 confirmado), PORTS.md como lei, semântica das faixas DEV/PRD, **Tailscale-only com a frase de segurança da 3900** (bind no IP Tailscale é a fronteira, não o firewall — a faixa DEV é pública), design de backup (sidecar + cadeia até o host + rsync Mac), transporte rsync/ProxyJump, deploy key e bucket OCI como alternativas futuras. Seção **explicitamente NÃO-NORMATIVA**: estado-alvo DEV→Aprovação→PRD (build-once/promote-by-tag; questão dos dados ABERTA — direção registrada, não decisão). Draft `doc-writer` → **advisor valida** → thread crava |
| 6.4.2 | Emenda no CLAUDE.md: seção de deploy ganha a realidade do oute-server (LXD, PORTS.md, Tailscale-only). Nota sobre unattended-upgrades: default do Ubuntu (security pocket) — uma linha no runbook |
| 6.4.3 | **PORTS.md do HOST atualizado** (via ssh): linha do Kubo — `kubo-test`, `10.173.117.18`, 3900 (DEV, Tailscale-only, sem nginx), 2900 (PRD, reservada). Next available atualizado |
| 6.4.4 | Runbook `docs/runbook-deploy.md` (ou seção no existente): deploy, logs, runs órfãos, query de verificação, restore, launchd do dono |

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0011 antes de cravar (6.4.1).
2. **Extraordinária:** pre-flight revelar `vfs` nos vizinhos (6.1.1) — topologia reabre; ou `shift=true` falhar (plano B da D2).
3. Conclusão da sessão (deliverables salvos antes).

## Delegações

Sessão atipicamente ops: o grosso é a **thread principal** executando via `ssh kubo-test`/`oracle-arm` (subagents não carregam o contexto do servidor). `doc-writer` (Haiku): drafts de ADR-0011 e runbook. `security-reviewer` (Sonnet): Dockerfile + compose + sidecar (superfície: `.env`, non-root, portas, volume, log rotation). `test-writer`/`implementer` só se o entrypoint exigir código em `kubo/` (mínimo necessário, TDD normal).

## Ordem de sacrifício (timebox 8h)

1. **1º corte:** launchd/rsync do Mac (dump no host fica; receita documentada para o dono plugar).
2. **2º corte:** reconciliação de nomenclatura dos feeds com o legado (o smoke de funcionamento das 6 URLs NÃO é cortável — vira pendência pré-M6 explícita).
3. **NUNCA cortáveis:** persistência provada nos dois níveis; scheduler coletando em produção (query com contagem > 0 no servidor); dump + restore testados; ADR-0011.

## Critérios de aceite

- [ ] `kubo-test` up com limites, autostart, overlay2 confirmado no Docker interno.
- [ ] Compose prod rodando; `docker compose down/up` E `lxc restart` preservam dados e religam tudo sozinhos.
- [ ] Migrations aplicadas via runner no servidor; scheduler coletou de verdade (run + itens verificáveis por query, contagem > 0).
- [ ] Smoke das 6 URLs executado do servidor; `Accept-Encoding` verificado (resultado registrado).
- [ ] Dump diário no host (`~/backups/kubo`, retenção 7d) + **restore testado com contagem > 0**.
- [ ] `.env` criado pelo dono (agente nunca leu/escreveu); `.env.example` emendado no PR.
- [ ] ADR-0011 mergeado; CLAUDE.md emendado; PORTS.md do host atualizado; runbook completo.
- [ ] PR conforme (branch/título/template; CodeRabbit endereçado; squash); main verificado ponta a ponta após merge.
- [ ] Notas de execução: pendências para a sessão do neon-import e para o M6 explícitas.

## Escopo negativo da sessão

- PRD NÃO (2900 só reservada; firewall intocado). Proxy device / nginx / subdomínio NÃO (nada escuta na 3900).
- neon-import NÃO (sessão própria, D19). Destilação/embedding NÃO (M6). CD/registry NÃO (deploy manual documentado; estado-alvo é seção não-normativa do ADR).
- Mudança de código em `kubo/` além do necessário para Dockerfile/entrypoint NÃO. Object Storage NÃO (D2 emendada).
- Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-05; infra verificada por comandos no host (aarch64, LXD 5.21, 8 vizinhos com Docker aninhado, PORTS.md); consulta de validação ao advisor (Fable 5): GO com achados A–E incorporados — Dockerfile como tarefa explícita (não existia), sidecar de backup mantido e reconciliado com a D2 (sem OCI, retenção 7d, cadeia até o host), regras harness×ssh pré-identificadas, reboot/restart em dois níveis + log rotation + stop_grace_period, verificação/restore pré-escritos (imagem distroless), ProxyJump como transporte, smoke de feeds separado da reconciliação de nomenclatura.*
