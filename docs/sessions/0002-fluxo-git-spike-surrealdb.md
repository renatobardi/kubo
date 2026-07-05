# Sessão 0002 — Fluxo Git + Spike SurrealDB (M2)

> **Status:** aprovado pelo dono (2026-07-04, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas (stop-loss, não estimativa) — ordem de sacrifício abaixo
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.
> **Idioma (D16):** a partir do primeiro commit desta sessão, **commits e PRs em inglês**. ADRs, docs, planos de sessão e reviews do CodeRabbit seguem em PT-BR. Histórico existente não se reescreve.

---

## Missão

Duas entregas: (1) convenções de fluxo Git enforçadas por harness + CI; (2) o **spike que compra com evidência a aposta estrutural do projeto** — SurrealDB como banco único (document + graph + vector). O spike produz os pins definitivos, a decisão de embeddings e o esqueleto do runner de migrations que o M3 consome.

**Gate de reversão:** se o spike revelar fragilidade séria (HNSW instável, SDK async impraticável, travessia lenta), a sessão PARA no checkpoint, registra o veredito e reabre a aposta do banco com o dono. **NO-GO é sucesso do processo, não falha da sessão.**

## Contexto

M1 concluído (PR #1). Pendências herdadas (Notas de execução do plano 0001): religar SurrealDB no CI; pin definitivo; enforce de cobertura fica para o M3. Decisões vigentes: D1–D10 (plano 0001, Apêndice A), D11–D15 (`docs/design/README.md`), D16 (idioma, acima). **Correção factual a uma nota do M1:** o probe `surreal isready` NÃO funciona no runner do GitHub (binário ausente, imagem distroless) — usar `curl -sf http://127.0.0.1:8000/health` em retry loop.

## Pré-requisitos / tarefas do dono

- `GEMINI_API_KEY` no ambiente local (key gratuita: https://aistudio.google.com) — para o smoke de embedding. Sem a key, o smoke é cortado (ver ordem de sacrifício), não bloqueia.
- `gh` autenticado (já está). Se `gh repo edit --delete-branch-on-merge` falhar por permissão, vira tarefa do dono — não bloqueia.

## Estrutura: DOIS PRs, nesta ordem

1. **PR 1** — branch `ci/0002-git-flow` (M2.0). Merge ANTES de abrir o PR 2.
2. **PR 2** — branch `feat/0002-surrealdb-spike` (M2.1) — validado pelos validadores novos (dogfood real).

Regra de parada do CodeRabbit (herdada do 0001): bloqueantes resolvidos; nitpicks respondidos ou registrados, não necessariamente implementados. Vale para os dois PRs.

---

## Marco 2.0 — Fluxo Git (timebox interno: 2h; estourou 2h30, corta o cortável e segue)

| # | Tarefa | Quem |
|---|---|---|
| 2.0.1 | Emenda no `CLAUDE.md`: idioma commits/PRs → inglês (D16); taxonomia de branch `(feat\|fix\|chore\|docs\|test\|refactor\|ci)/slug` documentada | thread principal |
| 2.0.2 | Ajuste `.claude/agents/doc-writer.md`: commits/PRs em inglês (hoje instrui PT-BR) | thread principal (`.claude/` é sensível) |
| 2.0.3 | `ci.yml`: steps bash puro validando nome de branch e título de PR convencional. **Armadilhas:** (a) steps SÓ com `if: github.event_name == 'pull_request'` — em push/schedule o contexto não existe e o branch é main; (b) título de PR é INPUT HOSTIL — passar via `env:` e ler `"$PR_TITLE"`, NUNCA interpolar `${{ ... }}` no script | thread principal (`.github/` sensível) |
| 2.0.4 | `.github/PULL_REQUEST_TEMPLATE.md` (em inglês): What/Why · TDD evidence (RED→GREEN) · Quality gates checklist · ADR touched? · Session plan of origin · New dependency? (justification) | `doc-writer` (Haiku) draft; thread instala |
| 2.0.5 | `guard-bash.sh`: bloqueio local de `git checkout -b` / `switch -c` / `branch` fora da taxonomia. **CORTÁVEL** (1º corte — o gate real é o CI; não polir regex com timebox correndo) | thread principal (harness) |
| 2.0.6 | `gh repo edit --delete-branch-on-merge` | thread principal |
| 2.0.7 | ADR-0004 — convenções de fluxo Git (taxonomia, 2 camadas de enforcement, D16, deleção pós-merge nativa; tags/release adiados até existir deploy) | `doc-writer` draft → **advisor valida** → thread crava |

## Marco 2.1 — Spike SurrealDB (ordem de ataque obrigatória: spike local → client/runner → CI → smoke → ADRs)

| # | Tarefa | Quem |
|---|---|---|
| 2.1.1 | **Dependência nova (a primeira de produção):** SDK `surrealdb` (PyPI). Justificar no PR; **pinar SDK e servidor JUNTOS** (matriz de compatibilidade SDK↔server é armadilha real — o pin dos dois sai deste spike, por evidência, não por changelog) | thread declara; `implementer` usa |
| 2.1.2 | Teste de integração do spike (`tests/integration/`, `@pytest.mark.integration`), casos separados: (a) insert/select document; (b) `RELATE` + travessia de grafo; (c) `DEFINE INDEX ... HNSW` + busca vetorial — **vetores sintéticos, dimensão pequena** (testa a MECÂNICA do índice; semântica de modelo é papel exclusivo do smoke); (d) transação multi-escrita — **uma única chamada `.query("BEGIN; ...; COMMIT;")`** (SDK não expõe begin/commit programático; confirmar na versão pinada); (e) SDK async básico. **Verificação operacional:** matar e resubir o container e observar persistência/reconstrução do índice HNSW — achado vai para o veredito | `test-writer` (Sonnet), RED apresentado |
| 2.1.3 | Execução local: container efêmero `docker run --rm -d -p 127.0.0.1:8000:8000 surrealdb/surrealdb:<pin> start --user root --pass root memory` (MESMO comando do CI; não usar o stack do compose, que não publica portas por segurança). Testes leem conn/user/pass de env com defaults; com `-m integration` e DB ausente, falham CLARO (não skip silencioso) | `test-writer`/`implementer` |
| 2.1.4 | Client mínimo de conexão + esqueleto do runner de migrations (~100 linhas: `.surql` numerados, tabela `migration`, aplicação sequencial, sem down-migrations), por TDD | `implementer` (Sonnet); **validação linha a linha da thread** (é `kubo/store/`) |
| 2.1.5 | Religar SurrealDB no CI (pendência 1 do M1): step `docker run -d ... start ... memory` + probe `curl -sf http://127.0.0.1:8000/health` em retry loop (SEM `surreal isready` — corrigido, ver Contexto) | thread principal |
| 2.1.6 | Smoke AO VIVO de embedding: `scripts/embedding_smoke.py`, standalone (httpx/stdlib — **litellm NÃO entra agora**; só no M6, quando tiver consumidor). Candidato primário: **modelo de embedding da família Gemini** (confirmar nome vigente na doc oficial do AI Studio; registrar dimensão). ~10 pares de frases PT-BR, validar ordenação de similaridade. Key SÓ por env `GEMINI_API_KEY` (invariante 8). NUNCA no CI nem na suite. Fallback documentado: OpenAI `text-embedding-3-small` (exigiria key nova — decisão do dono). **CORTÁVEL** (2º corte, junto com ADR-0006 — viram mini-sessão; não bloqueia o M3 porque o índice HNSW é migration separada, D5) | `implementer` (script) + thread avalia resultado |
| 2.1.7 | **Consulta OBRIGATÓRIA ao advisor com os resultados do spike** → GO/NO-GO da aposta + pins definitivos + embeddings. NUNCA CORTÁVEL — é a razão do M2 existir antes da store. Se evidência empírica contradisser expectativa na 1ª hora (SDK exigindo workarounds em série), ANTECIPAR esta consulta em vez de esticar | thread + **advisor** |
| 2.1.8 | ADRs: **ADR-0005 — veredito do spike + pins (servidor E SDK)** — a decisão registrável é "a aposta se sustenta sob estas evidências, com estas limitações conhecidas"; o pin é consequência. ADR-0006 — embeddings (modelo, dimensão, custo de re-embed aceito). ADR-0007 — mecânica de migrations. NÃO fundir 0005 e 0007 | `doc-writer` drafts → **advisor valida** → thread crava |
| 2.1.9 | `security-reviewer` no que tocou `kubo/store/` e `scripts/` | Sonnet; achados à thread |

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0004 antes do merge do PR 1.
2. Resultados do spike → GO/NO-GO + pins + embeddings (2.1.7).
3. ADRs 0005–0007 + conclusão da sessão (deliverables salvos antes da chamada).

## Critérios de aceite

- [ ] PR 1 mergeado: validadores de branch/título ativos (e comprovadamente disparando no próprio PR), template de PR em uso, CLAUDE.md e doc-writer emendados (D16), ADR-0004 mergeado.
- [ ] PR 2 conforme ao novo padrão (branch `feat/0002-surrealdb-spike`, título convencional em inglês, template preenchido).
- [ ] CI verde com job de integração rodando SurrealDB real (docker run + probe curl).
- [ ] Spike cobre os 5 comportamentos + verificação de restart do HNSW.
- [ ] Runner de migrations testado (aplicação sequencial + tabela de controle + reexecução é no-op).
- [ ] Pins definitivos (imagem no compose e CI; SDK no pyproject/uv.lock) — **condicionado ao GO**.
- [ ] **Veredito explícito do spike registrado** (ADR-0005). Em GO: ADRs 0005–0007 mergeados. Em NO-GO: ADR-0004 + veredito registrado + sessão parada = sessão bem-sucedida.
- [ ] Smoke de embedding executado e resultado no ADR-0006 (se não cortado).
- [ ] Checkpoint final com transparência de custo (delegações, consultas ao advisor, cortes exercidos e porquês).

## Ordem de sacrifício (timebox 8h)

1. **1º corte:** guard-bash de branch local (2.0.5) — CI já enforça.
2. **2º corte:** smoke de embedding + ADR-0006 (2.1.6) — viram mini-sessão dedicada.
3. **NUNCA cortável:** veredito do spike (2.1.7) e ADR-0005.

## Escopo negativo da sessão

- Nenhum schema de conhecimento (M3); nenhuma tabela além de `migration` + efêmeras do spike.
- Camada store completa NÃO — só client mínimo + runner.
- Embeddings decididos, não implementados. litellm não entra.
- Nenhum worker; cobertura segue sem enforce (M3); nada de tags/release/CD.
- Nenhuma decisão nova de arquitetura sem reabrir planejamento — inclusive: se o SDK async decepcionar mas o servidor convencer, o meio-termo (HTTP direto via httpx na store) é DECISÃO DO DONO, não da sessão.

---

## Notas de execução (2026-07-05, CLI Opus + advisor Fable 5)

**Marco 2.0 (PR #2, `ci/0002-git-flow`, squash-merged em `main`):** entregue completo. ADR-0004 validado pelo advisor (GO-com-emendas, todas aplicadas: `pr-conventions` como required check, trigger `edited`, force-create no guard, seção de limites). Estratégia de merge: **squash-only** decidida pelo dono, registrada no ADR-0004 e configurada no repo. CodeRabbit: 5 achados (3 aplicados, 2 declinados com justificativa técnica — `name:` quebraria o contexto do required check; MD041 sem markdownlint). SonarCloud S8264 (permissões por job) resolvido.

**Correções de harness (mesma família de bug: hook agindo por substring/fora do repo — CLAUDE.md manda corrigir no hook, não contornar). Todas com evidência nesta sessão, teste `guard-bash.test.sh`:**
1. `guard-bash.sh`: regras de git e coderabbit casam só em **posição de comando** (início ou após `; & |`), não substring — mensagem de commit citando `checkout -b`/`git push` e `... | grep coderabbit` deixaram de bloquear.
2. `guard-files.sh`: `/private/tmp/*` no allowlist (scratchpad do Claude Code).
3. `check-quality.sh`: arquivo fora de `$CLAUDE_PROJECT_DIR` → exit 0 (probes descartáveis não são código do projeto).

**Marco 2.1 (spike) — veredito GO.** Advisor consultado com a evidência (2.1.7): **GO na aposta**, mas recusou o pin `v2.1.4` (versão de dez/2024, uma major atrás do 3.x GA). A suíte de 14 testes virou canário de upgrade e foi rodada contra `v3.1.5`: verde, e **corrige o footgun do EF** (KNN sem EF passa a falhar alto em vez de retornar vazio). **Pin definitivo: servidor `v3.1.5` + SDK `surrealdb==2.0.0`** (ADR-0005). O canário também pegou a quebra do healthcheck do compose (`isready --conn` → `is-ready --endpoint` no 3.x). Store (client + runner de migrations) por TDD (RED→GREEN visível), 14 testes verdes, pyright strict limpo. CI: job `integration` religado (docker run + probe `curl /health`).

**Cortes exercidos:** 2º corte (smoke de embedding + ADR-0006) — `GEMINI_API_KEY` ausente; diferido para mini-sessão, com dono/prazo nomeados no ADR-0005. Nenhum outro corte (o guard local 2.0.5 foi mantido).

**Delegações:** advisor (Fable 5) 2× (ADR-0004; GO/NO-GO do spike) — ambas mudaram o resultado (emendas no fluxo git; pin 2.1.4→3.1.5). security-reviewer em `kubo/store/`. Demais artefatos (ADRs, tests, store, CI) escritos na thread: o conteúdo era ditado pelo plano/advisor ou pela descoberta empírica de API que vivia na thread — delegar custaria mais que fazer, com validação linha a linha inerente.

### Mini-sessão diferida — smoke de embedding + ADR-0006 (branch `feat/0002-embedding-smoke`)

O 2º corte do M2 (2.1.6 + ADR-0006) foi executado quando o dono forneceu `GEMINI_API_KEY`. `scripts/embedding_smoke.py` (stdlib pura, sem httpx/litellm): 10 trios PT-BR com distratores de **polissemia adversarial** (banco, fonte, prova, luz, remédio). Três configs rodadas ao vivo — `gemini-embedding-001` @768 e @3072, e `gemini-embedding-2` @768 — **todas 10/10**. Achados que decidiram: 768 == 3072 em qualidade (MRL grátis) a 4× menos custo de índice; o `embedding-2` separa 2× melhor mas n=10 não dá poder pra provar qualidade e seu contrato task-as-instruction é fuzzy. **ADR-0006: `gemini-embedding-001` @ 768, `task_type=SEMANTIC_SIMILARITY`, cosseno.** Advisor consultado (fable-advisor, nativo indisponível): **GO-com-emendas**, 8 emendas todas incorporadas (task_type na decisão, proveniência no schema, chunking obrigatório no M3, deprecação Google + ToS free como riscos, escada de fallback 768→1536→embedding-2, self-hosted como alternativa rejeitada, proibição de derivar thresholds do smoke). security-reviewer no `scripts/`. litellm segue fora (M6).

---

*Fontes: sessão de planejamento Cowork de 2026-07-04; consulta de validação ao advisor (Fable 5): GO com emendas, todas incorporadas (probe corrigido, dependência nomeada, 2 PRs, ordem de sacrifício, ADR-0005 como veredito, injection no CI, transação single-query, verificação de restart do HNSW).*
