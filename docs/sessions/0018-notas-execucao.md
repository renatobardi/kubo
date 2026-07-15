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
inclui conta-máquina `kubo-dev`, path-guard no CI, integração `github-releases.yaml`, e o
smoke com o agente escrevendo de fato no repo principal.
