# Sessão 0018 (parte A) — notas de execução

> Complemento do plano `0018-rito-promocao.md`. Corte pré-acordado no marco 18.6 (smoke físico
> owner-gated) — cumprido. Parte B (0018b, marcos 18.7–18.11) fica pré-autorizada por este
> mesmo plano, sem replanejamento.

## Estado dos marcos

| # | Marco | Estado |
|---|-------|--------|
| 18.1 | ADR-0021 esqueleto | ✅ draft commitado (`docs/adr/0021-rito-promocao.md`, status `proposto`) |
| 18.2 | Refactor registry → `kubo/workers/registry.py` | ✅ TDD; API importa sem puxar APScheduler (confirmado empiricamente) |
| 18.3 | dev-mini v2 + gate sequencial | ✅ TDD; auto-open atômico (CREATE condicional testado contra SurrealDB v3.1.5 real antes de implementar), terminal-ness derivada do snapshot, `closed = terminal ∨ decidida` |
| 18.4 | Confirmar promoção (import-oráculo) | ✅ TDD; token read-only separado do PAT de escrita; ordem I/O-antes-do-commit; trap c fechado (`_reject_dev` valida o par antes de tocar a API) |
| 18.5 | Higiene E4 + auditoria E7 | ✅ `gitleaks` sobre 223 commits = 0 leaks; `ci.yml` já tinha `permissions: contents: read` em todos os jobs (compliance pré-existente) |
| 18.6 | Deploy + smoke físico | ✅ **executado 2026-07-15, os DOIS caminhos verdes** (ver abaixo) |

**Revisões independentes:** `security-reviewer` (subagent) sobre o diff completo antes do PR — zero achados. Advisor Fable 5 consultado 2x (antes de fixar a abordagem do gate sequencial; antes deste checkpoint) — 4 achados reais endereçados antes do smoke (wiring do token read-only, mensagens de erro ambíguas, edge case de id fantasma na corrida, nota de limitação residual no ADR). CodeRabbit no PR #45 — 4 achados (1 crítico: coerção `bool()` no campo `merged`) todos corrigidos antes do merge.

**PR:** #45, squash-merged em `main` como `e5400c5` (2026-07-15 18:42:42 UTC).

## Smoke físico (18.6) — executado 2026-07-15, os DOIS caminhos verdes

Deploy via `./scripts/deploy.sh` (build `e5400c5-20260715T185428Z`, migration
`0008_deliverable_merge_sha.surql` aplicada, `/healthz` → ok). Disparo do flow dev via
**kubo-scheduler** (`docker compose run --rm kubo-scheduler python -m kubo flow run dev-mini
"<tarefa>"`) — mesmo caminho da 0016b.

**Flow:** `flow:ts8j9zw2su2o4m1saxn2` — tarefa trivial ("Add a trivial hello() function...").
**Run:** `run:73b04158cfe6202cdf992d1cf820bd8f` — **custo real US$ 0.140258, 6 turnos**, ~19s.
**PR:** `renatobardi/kubo-forge#3`, branch `kubo/ts8j9zw2su2o4m1saxn2`.

| Caminho | O que foi provado | Resultado |
|---|---|---|
| **Confirmar SEM merge** (deploy-gap, D38) | Aprovar no board ≠ mesclar no GitHub | `PromotionError` legível: *"o PR ainda não foi mesclado no GitHub — aprovar não é mesclar (D38)"*. Gate `done` seguiu ABERTO (at-least-once) |
| **Merge real** no GitHub | — | dono mesclou `kubo-forge#3` às 19:04:14 UTC |
| **Confirmar COM merge**, `worker_name=feed` | Import-oráculo: merge via API read-only + registry no processo vivo | Board mostrou **Promovido**; `decide_gate(done→promoted)` transacionado |

**Read-back do grafo (root, pós-smoke):**

```yaml
tasks:
  dev      @ promoted   (terminal de sucesso, sem decisão — contraparte não-humana)
  humano   @ done       (decision=approved, decided_at=19:01:10 — gate `review`, FECHADA por decisão)
  humano   @ promoted   (decision=approved, decided_at=19:04:29 — gate de promoção)
deliverable:
  kind=pr, pr_number=3, pr_url=.../kubo-forge/pull/3
  merge_commit_sha=5e0a4b0e759e930e9e35acdf209bbfbb010cc5be
list_flows: status=entregue, gate_open=false, tasks_open=0
```

**Auditoria cruzada (GitHub API, fora do grafo):** `gh pr view 3 --repo renatobardi/kubo-forge`
confirma `mergeCommit.oid = 5e0a4b0e759e930e9e35acdf209bbfbb010cc5be` — **SHA idêntico** ao
gravado pelo Confirmar. `mergedBy = renatobardi`, `mergedAt = 2026-07-15T19:04:14Z`.

Prova ao vivo, de ponta a ponta: terminal-ness derivada do snapshot (v1 e v2 coexistindo),
`closed = terminal ∨ decidida` (a task `humano@done` decidida conta como fechada mesmo não
sendo terminal no v2), auto-open atômico do gate de promoção, import-oráculo com token
read-only separado do PAT de escrita — estes quatro comportamentos, agora confirmados em
produção (kubo-test), não só em teste (o escopo NÃO coberto está nomeado abaixo).

## Escopo NÃO coberto pelo smoke físico (nomeado, não silencioso)

- **Caminho negativo do registry** ("worker não está na imagem viva; confira o nome ou rode
  `./scripts/deploy.sh`"): o erro MECÂNICO (digitar um nome inexistente, ex. `banana`) seria
  disparável em segundos e não foi exercitado no smoke — coberto só por teste de integração
  (`test_promote_rejects_unknown_worker_name`). O que genuinamente não é disparável com `feed`
  é o cenário SEMÂNTICO (worker mesclado mas ainda não deployado) — esse exige um worker
  realmente novo, e é o objeto do smoke da parte B (18.10, `github_releases`). Ação barata
  registrada: incorporar um clique com nome errado ANTES do confirm real no roteiro do 18.10
  (custo zero — o flow já estará com o gate aberto), fechando também o caso mecânico ao vivo.
- **Autoria do agente no repo principal** (D41): o smoke usou `kubo-forge` (sandbox existente,
  ADR-0019) e um worker JÁ registrado (`feed`) para confirmar — cerimônia conscientemente
  encenada (nomeada no ADR-0021 antes do smoke rodar), não o rito completo com PR no
  `renatobardi/kubo`. Isso é o objeto da parte B.

## Pré-condições operacionais do smoke — ordem seguida

1. `GITHUB_TOKEN_READONLY` (fine-grained, restrito a `kubo-forge`, Contents+PR **read-only**)
   criado pelo dono e gravado no `.env` do servidor — env já preparado antes do "pode executar".
2. `./scripts/deploy.sh` rodado do Mac (nunca do kubo-test) — build + migrations + smoke
   `/healthz` verificados antes de qualquer disparo.
3. Disparo do flow por CLI (`kubo-scheduler`, nunca botão na UI — C1, disparo síncrono minutos).
4. Decisões (aprovar/confirmar) pelo dono, no browser, nos dois caminhos (negativo antes do
   positivo — a ordem que prova o deploy-gap de verdade).

## Custo total registrado

**US$ 0.140258** (1 run do agente dev, 6 turnos). O Confirmar promoção não chama LLM — só
API REST read-only do GitHub + leitura do registry em processo; custo zero adicional.

---

**Parte A encerrada.** Parte B (0018b, marcos 18.7–18.11) pré-autorizada pelo mesmo plano —
path-guard no CI, integração `github-releases.yaml`, e o smoke com o agente escrevendo de
fato no repo principal. **D45 revertida em 2026-07-15** (commit `367b161`, ANTES da execução
da parte B abaixo): sem conta-máquina `kubo-dev` — o agente usa o PAT fine-grained DO DONO,
restrito a `renatobardi/kubo`, sem a permissão Workflows; PR de agente identificado por
prefixo de branch (`agent/*`), não por autor; require-review fica desligado (o dono não
aprova o próprio PR) — o gate humano vira disciplina do dono, nomeada como postura mais
fraca no ADR-0021.

## Parte B — marcos 18.7–18.9 (código), executados 2026-07-15

| # | Marco | Estado |
|---|-------|--------|
| 18.7 | PAT do dono (`GITHUB_PAT_KUBO`) + envs (`KUBO_MAIN_*`) no `.env` do kubo-test; `GITHUB_TOKEN_READONLY` editado (mesmo valor) para cobrir `renatobardi/kubo` além de `kubo-forge` (ambos públicos) | ✅ feito pelo dono |
| 18.8 | Segundo alvo do flow dev (`dev-kubo`) + path-guard CI (E3) + whitelist (E8) + `.claude/` (E6) | ✅ |
| 18.9 | `catalogs/integrations/github-releases.yaml` + spec cravada da task do worker | ✅ |

**18.8 — desenho validado pelo advisor Fable 5 antes de travar** (checkpoint obrigatório, código
estrutural em `kubo/workers/`, `kubo/runtime/`, `catalogs/`): terceira via em vez das duas
opções que a thread trouxe — **o alvo é função do TEMPLATE**, não de um parâmetro em runtime.
Novo template `catalogs/flow_templates/dev-kubo.yaml` (mesmo board/gates do `dev-mini` v2,
mirado no repo principal); `_FLOW_REGISTRY` (E4) despacha `dev-mini`→forge / `dev-kubo`→kubo
via `functools.partial` amarrando um `_DevTarget` (env-map, classe de worker, prefixo de
branch, integração de escrita). A whitelist E8 fica **enforced por construção**: não existe
forma de expressar um terceiro repo sem PR novo — não há parâmetro de "alvo" pra validar
contra uma lista. `KuboDevWorker(DevWorker)` é subclasse (não atributo de instância) — resolve
a integração via `self.manifest.integrations[0]`, preservando `DevWorker.manifest` como
atributo de CLASSE (load-bearing em `_validate_registered_worker`/`build_scheduler`).
Tripwire de defesa-em-profundidade em `_reject_dev`/`_promote_dev`: o `pr_url` do deliverable
tem que bater com owner/repo do alvo resolvido ANTES de qualquer chamada à API — fecha o
cenário (hipotético, não disparado no smoke) de um `pr_number` colidir entre os dois repos.
TDD completo (test-writer RED → implementer GREEN, `tests/runtime/test_flow_dev_kubo_vertical.py`
+ `tests/workers/test_dev.py`); `security-reviewer` sobre o diff completo — zero achados
críticos/altos, duas notas BAIXO (comparação de string sem normalização no tripwire — falha
fechado, não é bug; garantia do PAT sem Workflows é operacional, não verificável em código).

**E6 (`.claude/` no workspace do agente) — decidido: worker limpa.** Clonar `renatobardi/kubo`
(alvo `dev-kubo`) traria os hooks/settings/agents deste MESMO projeto para dentro do sandbox
do agente — imprevisível. `DevWorker.run()` remove `.claude/` do workspace logo após o clone,
antes do agente rodar (`shutil.rmtree(..., ignore_errors=True)` — no-op seguro quando não
existe, como no sandbox `kubo-forge`). Testado (`test_claude_dir_removed_from_workspace_before_agent_runs`).

**Duas lacunas achadas (fora do plano original) que teriam impedido QUALQUER PR de agente de
passar CI — corrigidas junto do path-guard:**
1. O job `pr-conventions` (já `required`) só aceitava branch `(feat|fix|chore|docs|test|refactor|ci)/slug`
   — `agent/<flowid>` reprovaria. Adicionada segunda taxonomia válida `^agent/[a-z0-9]+$`.
2. `_title()` do `DevWorker` produzia `[kubo dev] ...`, que não bate com o regex de título
   convencional que `pr-conventions` também exige. Trocado para `feat(dev): ...` (TDD).

**Armadilha do próprio path-guard, achada e corrigida antes de aplicar:** a primeira versão
tinha `if:` de NÍVEL DE JOB checando `startsWith(github.head_ref, 'agent/')` — um required
status check cujo JOB é pulado via `if:` fica "pending" pra sempre no GitHub (não conta como
"passou"), o que teria travado TODO PR humano (branch fora de `agent/*`) indefinidamente. A
condição de branch foi movida para DENTRO do step (`case "$BRANCH" in agent/*) ... ; *) exit 0`),
com o job sempre rodando em `pull_request` — validado localmente contra branches/diffs reais e
adversariais (incluindo confusão de prefixo tipo `kubo/workers-evil/`, corretamente bloqueada)
antes de aplicar como required check.

**`agent-path-guard` promovido a required status check** (autorizado explicitamente pelo dono,
2026-07-15, com dupla confirmação: lista final `[quality, tests, pr-conventions, integration,
agent-path-guard]` batendo exatamente com o que já existia + o novo; PR humano passa verde,
não pending):
```
gh api -X PATCH repos/renatobardi/kubo/branches/main/protection/required_status_checks \
  -F strict=false -f 'contexts[]=quality' -f 'contexts[]=tests' \
  -f 'contexts[]=pr-conventions' -f 'contexts[]=integration' -f 'contexts[]=agent-path-guard'
```

**Gates locais completos, verdes** (equivalente ao CI): `ruff check`/`ruff format --check`/
`pyright` limpos; `pytest -m "not integration"` 502 passed; suíte completa + cobertura
(`--cov=kubo/store --cov=kubo/contracts --cov=kubo/runtime --cov-fail-under=85`) 696 passed,
95.17% (gate 85%); `docker compose config` válido (com envs dummy); YAML do CI parseado com
`yaml.safe_load`.

### 18.9 — spec cravada da task do worker `github-releases` (usar literal em 18.10)

Integração `catalogs/integrations/github-releases.yaml` criada — `secret_ref: env:GITHUB_TOKEN_READONLY`
(reusa o token read-only da parte A, já editado pelo dono pra cobrir `renatobardi/kubo`, E13).

Texto abaixo é o `question` a passar em `kubo flow run dev-kubo "<texto>"` no 18.10 — em
inglês (código/identificadores/PR do projeto são em inglês; a task espelha esse registro).
Cravado o suficiente para não deixar o agente inventar contrato, dedupe ou tratamento de
rate-limit (E13, spec da 0018-rito-promocao.md, seção "Spec da task do worker github-releases"):

```text
Implement the `github-releases` worker (contract per ADR-0009), mirroring `kubo/workers/feed.py`
in shape and security posture (read it first).

File: kubo/workers/github_releases.py, class GithubReleasesWorker. Register in
kubo/workers/registry.py as WORKER_REGISTRY["github-releases"] = GithubReleasesWorker (exact
string "github-releases", matching manifest.name).

Manifest: WorkerManifest(name="github-releases", version="0.1.0",
integrations=["github-releases"], config=GithubReleasesConfig). The integration catalog file
catalogs/integrations/github-releases.yaml already exists — do not recreate it, just declare it.

Config (GithubReleasesConfig, pydantic BaseModel, extra="forbid"):
- repos: list[str] — "owner/repo" strings to poll. This is RUNTIME/execution config (like
  FeedConfig.feed_url in feed.py), never a catalog file. Do NOT touch schedules.yaml or wire
  this into the scheduler — scheduling this worker is future-session work, out of scope here.
- Validate each entry matches "owner/repo" shape (non-empty, exactly one "/", no path
  traversal chars) at construction, same idiom as feed.py's _http_scheme_only validator.

Behavior of run(ctx):
1. Read ctx.integrations["github-releases"] for the bearer token + base_url. Missing/no
   secret -> raise ConfigError (never silently skip).
2. For each repo in config.repos, call GitHub REST GET /repos/{owner}/{repo}/releases. A
   single page (default 30) is enough for v1 — upsert idempotency covers re-collection.
3. Only draft == false releases. Exclude prerelease == true in this v1 (skip, don't error).
4. For each qualifying release, build an ItemPayload:
   - source = SourcePayload(kind="github-releases",
     canonical=f"https://github.com/{owner}/{repo}", title=f"{owner}/{repo} releases")
   - external_id = str(release["id"]) — the natural dedupe key (releases get EDITED after
     publish; upsert_item already handles overwrite-not-duplicate, no new store code needed).
   - content = release body (markdown), cleaned with the SAME discipline as feed.py's _clean
     (strip control/format/surrogate chars, keep \n/\t, cap ~65536 chars) — this is
     THIRD-PARTY UNTRUSTED markdown (CLAUDE.md: all collected content is hostile by default).
   - url = release["html_url"] (structural, from the API).
   - title = release["name"] or tag_name if name is empty, capped ~500 chars.
   - metadata = {"tag_name": ..., "repo": "owner/repo"} — small structural fields only, no
     large blobs.
5. Rate limiting: on 403/429 for a given repo, do NOT retry (retry is the orchestrator's job,
   never the worker's). Record a structured ErrorInfo(kind="rate_limit", message=...,
   detail={"repo": ..., "status": ...}) and continue to the NEXT repo — one rate-limited repo
   must not block collecting from the others. Other transport/HTTP errors (timeout, 5xx, DNS):
   same treatment, ErrorInfo(kind="http", ...), no retry, continue. If multiple repos error,
   return the FIRST error encountered (document this deliberate difference from feed.py, which
   handles exactly one feed per run — this worker handles multiple repos per run).
6. Return RunResult(payloads=[...all ItemPayloads across all repos...],
   stats=Stats(repos_seen=N, releases_seen=N, items=N, rate_limited=N), error=...). Payloads
   already collected before a later repo's error MUST still be returned (ADR-0009 §VII
   partial-failure semantics, same pattern as feed.py).

Tests: tests/workers/test_github_releases.py, mirroring tests/workers/test_feed.py's mocking
style (mock httpx, no real network). UNIT-ONLY — your workspace has no database; do not write
anything requiring SurrealDB or the `integration` pytest marker. Cover: happy path (multiple
repos, multiple releases), draft/prerelease filtering, dedupe key stability, rate-limit on one
repo not aborting the others, malformed/missing fields from the API not crashing.

Do NOT: touch schedules.yaml, any persona catalog, or kubo/scheduler/; add a retry loop or
backoff; fetch anything beyond the releases REST endpoint for the configured repos; add a new
dependency (httpx is already a project dependency, reuse it exactly as feed.py does).

Run ruff check, ruff format --check, and pytest -m "not integration"
tests/workers/test_github_releases.py before finishing. Keep the diff inside
kubo/workers/github_releases.py, the one new registry line, and
tests/workers/test_github_releases.py.
```

**Nota para o 18.10:** ao clicar "Confirmar promoção" na UI, o dono digita `github-releases`
(com hífen, igual ao `manifest.name`/chave do registry acima) — não `github_releases`.

## 18.10 — smoke físico, executado 2026-07-15 (os DOIS caminhos do D44)

**Disparo:** `docker compose run --rm kubo-scheduler python -m kubo flow run dev-kubo "<spec
acima>"` contra o kubo-test já deployado no `main` do 18.7-18.9 (build `7bd73ef-20260716T002016Z`,
`/healthz` ok, `dev-kubo`/`dev-mini` confirmados no `_FLOW_REGISTRY` do processo vivo antes do
disparo).

**Run 1 (agente):** `flow:s7v7s5wgb3wr2e64afrc`, `run:87d4f5df3efcac355d9dd00db6822739` —
US$1.2685, 30 turnos, status `ok`. Abriu **PR #49** (`agent/s7v7s5wgb3wr2e64afrc`,
`renatobardi/kubo`, autor `renatobardi` — D45 revertida, sem conta-máquina), 3 arquivos
(`kubo/workers/github_releases.py`, `kubo/workers/registry.py`,
`tests/workers/test_github_releases.py`), +533/-0. `agent-path-guard` e `pr-conventions`
PASSARAM legitimamente (branch `agent/*`, título `feat(dev): ...` — as duas correções do 18.8
provadas em produção). `quality` FALHOU: pyright achou um bug de type-narrowing no próprio
teste do agente (`.payloads[0].external_id` acessado sem `isinstance`, diferente de todo resto
do arquivo, que usava o idioma correto). CodeRabbit rodou de verdade (sem rate limit) e não
achou nada.

**Decisão do dono:** rejeitar via UI (`reject_gate`), motivo registrado no PR, provando o
caminho de reject no repo PRINCIPAL com `GITHUB_PAT_KUBO` (não só no sandbox `kubo-forge` da
parte A) — `test_reject_closes_pr_via_api_with_kubo_pat` (18.8) é a mesma asserção, agora
confirmada fisicamente. Board foi a `rejected`.

**Fallback D44 (tropeço 1 → CLI assume a escrita):** thread principal buscou o commit do PR
fechado (`81284ee`, branch já auto-deletado pelo GitHub), reaproveitou o código do agente
quase integralmente (era correto, batia com o enunciado), corrigiu o bug de type-narrowing, e
ANTES de reabrir rodou um passe de `security-reviewer` dedicado (fora do CI) — achou 1 ALTO
(`tag_name` cru em `metadata`, abortaria o batch inteiro no encoder CBOR estrito do SDK
SurrealDB) + 2 achados menores (streaming sem cap de bytes, `follow_redirects` implícito).
Todos corrigidos + 2 testes novos, suite local completa verde (521 unit + 19 do worker),
`ruff`/`pyright`/`format` limpos.

**PR #50** (`feat/github-releases-worker`, CLI/dono, D44): CI verde (5 required + SonarCloud),
CodeRabbit rodou de verdade e achou 3 itens acionáveis (403≠rate-limit real, teste de streaming
cobre só o caminho fácil, nit do validador — **não é o mesmo achado do security-reviewer**,
são achados INDEPENDENTES, registrados em `docs/sessions/fase4-roadmap.md` D51). Revisão do
dono/Cowork (repo público, leu o diff) aprovou o merge sabendo dos achados do D51.
`reviewDecision` do PR ficou `CHANGES_REQUESTED` (CodeRabbit formal review, não só o check) —
mergeado com `gh pr merge --admin` por autorização explícita do dono, que já tinha triado os 3
achados. Squash-merged em `main` como `ff38c18`.

**Achados registrados no ADR-0021 (seções XI-XIII, validadas pelo advisor no 18.11):** gate
automático + revisor-LLM-como-serviço são necessários mas não suficientes (o ALTO só apareceu
no passe adversarial dedicado, fora do CI); o agente segue a letra do enunciado, não generaliza
princípio para campo não-enumerado (responsabilidade do enunciado é de quem escreve); duas
lacunas/decisões em aberto nomeadas (catálogo pré-colocado vs. PR autossuficiente; allowlist do
path-guard mais largo que `catalogs/integrations/`); `agent-path-guard ✅` no #50 é vácuo como
evidência (branch `feat/*`) — a evidência real do guard é o #49.

**Falta para fechar 18.10/18b:** deploy do kubo-test com `main` pós-#50 + "Confirmar promoção"
(`github-releases`, com hífen) na UI pelo dono — ação humana por construção (D38), não
delegável ao CLI.
