# Sessão 0003 — Schema de conhecimento + Store (M3)

> **Status:** aprovado pelo dono (2026-07-05, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas (stop-loss) — ordem de sacrifício abaixo
> **Estrutura:** 1 PR — branch `feat/0003-knowledge-schema-store` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

O grafo de conhecimento nasce: migrations do schema (spec §2.3, com desvios registrados no ADR-0008) + tabela `run` (ADR-0002) + camada `kubo/store/` completa **para os consumidores da fase 1**, por TDD — com o gate de cobertura de 85% passando a valer a partir desta sessão.

## Contexto e roadmap

M1 ✅ M2 ✅ (+ mini-sessão do smoke). Insumos herdados: runner de migrations testado (ADR-0007), pins v3.1.5/SDK 2.0.0 (ADR-0005), embeddings `gemini-embedding-001` @ 768/cosseno (ADR-0006, com emendas: proveniência de embedding no schema; chunking obrigatório ~2k tokens).

**Roadmap da fase 1 (atualizado):** M3 (esta) → M4 (contrato de worker + loader integrations) → M5 (feed + APScheduler) → **M5.5 (deploy)** → M6 (destilação + CLI, prova dos 90 dias).

**Decisões novas registradas nesta sessão de planejamento:**
- **D17 — `memory` adiada:** sem produtor/consumidor até a fase 3; entra por migration futura. Registrada como desvio no ADR-0008 (não ganha ADR próprio).
- **D18 — topologia de deploy:** o host OCI (`oute-server`) é multi-projeto com LXD + convenção `PORTS.md` (raiz do host). Kubo = container LXC `kubo-test`, IP `10.173.117.16`, portas 3560 (frontend) / 4520 (backend), **ambiente único "DEV" fechado no Tailscale** — nada público; PRD público é decisão futura com gate próprio, não infraestrutura antecipada. SurrealDB sem porta nem no container (só rede interna do compose). Docker aninhado exige `security.nesting=true`; definir limites de recursos no LXD. Materializa no **M5.5** (ADR de topologia + emenda no CLAUDE.md + atualização do PORTS.md no host). Nesta sessão: nada de deploy.

## Marco 3.1 — Migrations (a fundação do grafo)

| # | Tarefa | Quem |
|---|---|---|
| 3.1.1 | **Migration 0001 — schema de conhecimento + run.** Tabelas: `source`, `item`, `distilled`, `chunk`, `entity`, `run`. Arestas: `from_source`, `derived_from`, `mentions`, `relates_to` (com campo `kind` string — a spec a define tipada; sem enum/ASSERT por ora, nenhum produtor na fase 1), `chunk_of`, `produced_by → run`. Nomes EXATAMENTE como a spec (inglês). SEM `memory`/`grounded_in` (D17) | `implementer` escreve; thread valida linha a linha |
| 3.1.2 | **Postura SCHEMAFULL** nas tabelas de conhecimento (entrada externa é hostil; o schema é a primeira borda); `FLEXIBLE` apenas em campos de payload variável (ex.: metadados brutos de `item`). `run`: campos mínimos (`worker`, `started_at`, `finished_at`, `status`, `stats`, `error: option<object>` flexível) — a forma do erro estruturado NÃO se crava aqui; o contrato de worker (M4) a define | idem |
| 3.1.3 | **Desenho de chunking (decidido; ADR-0008 registra):** `chunk` é tabela própria com `text`, `order`, o vetor (768) e a proveniência de embedding (`model`, `dim`, `task_type` — ADR-0006). Aresta `chunk -[chunk_of]-> distilled` (filho→pai, convenção da spec). O texto chunkado é o conteúdo do **distilled** (agregado), não do item. KNN roda sobre `chunk`; a store resolve chunk→distilled (busca devolve conhecimento, não fragmento órfão). Embedding no chunk: obrigatório-vs-opcional fica para a validação de DDL do advisor | idem |
| 3.1.4 | **Migration 0002 — índice HNSW** (768, cosseno) sobre `chunk`, separada (D5). **ARMADILHA PRÉ-AUTORIZADA:** o runner envolve `.surql` em `BEGIN;…;COMMIT;` e `DEFINE INDEX HNSW` em transação NÃO foi verificado no spike (ADR-0007). O PRIMEIRO teste do marco é: a migration HNSW aplica via runner. Se DDL de índice não rodar em transação → **PARAR e consultar o advisor** antes de tocar o runner; proibido improvisar flag "não-transacional" | idem |
| 3.1.5 | **Validação de DDL pelo advisor ANTES do commit** (decisão mais duradoura do projeto depois do banco em si). Ele decide os detalhes finos: tipos SurrealQL, forma dos campos de proveniência, parâmetros EFC/M do índice, embedding obrigatório no chunk | thread + **advisor** |
| 3.1.6 | **ADR-0008 — desvios de schema da §2.3** (um documento só): chunk-como-registro (CONTRARIA a linha "embeddings como campos vector nos records distilled" — HNSW indexa um vetor por registro; array não é indexável, então nem é opcional); `memory`/`grounded_in` adiados (D17); `item` NÃO é embeddado na fase 1 (corpus preservado, não vetorizado — só o destilado ganha vetor via chunks); escopo temporal: o ADR fixa o SCHEMA de chunking, o ALGORITMO é do M6 | `doc-writer` draft → **advisor valida** → thread crava |

## Marco 3.2 — Camada store (TDD, pyright strict, validação linha a linha da thread)

**Regra anti-especulação:** a store expõe SOMENTE o que os consumidores da fase 1 exigem. Nenhum método sem teste que o exija.

**Chaves naturais e idempotência (decididas — não deliberar na sessão):**
- Padrão uniforme: **record ID determinístico derivado da chave natural** (`UPSERT type:⟨hash⟩`) — elimina a corrida do get-or-create sem SELECT-then-CREATE.
- `item`: hash de source + id externo (D4). `source`: URL/identificador canônico. `entity`: nome normalizado = **NFC + casefold + colapso de whitespace**, match exato (sem fuzzy — decisão vigente), índice UNIQUE no campo normalizado.

| # | Tarefa | Quem |
|---|---|---|
| 3.2.1 | RED: comportamentos da store (unit onde couber; integração real para o que exige banco) — upsert idempotente de `source`/`item` (teste "2× = no-op"); inserção atômica de `distilled`+chunks+arestas; get-or-create de `entity` + escrita de `mentions` (entram JUNTOS — ou ambos cortados, ver sacrifício); travessia de proveniência distilled→item→source; busca vetorial; ciclo de vida de `run` (start/finish/fail); **teste de rejeição de dimensão errada** no insert de chunk | `test-writer` (Sonnet), RED apresentado |
| 3.2.2 | **Wrapper transacional da store (entregável nomeado):** escrita multi-statement atômica via `query_raw` **checando TODOS os statements** — contrato do ADR-0005 (no 3.x, erro no meio da transação reverte mas não propaga via `query()`). A inserção distilled+chunks+arestas usa este wrapper | `implementer` (Sonnet); thread valida linha a linha |
| 3.2.3 | GREEN: repositórios/queries. **Bind params SEMPRE, inclusive dentro de strings transacionais** (ADR-0005) — conteúdo coletado nunca interpolado. Busca vetorial: função única com EF injetado (`ef = max(k*4, 40)`, ADR-0005); PROIBIDO derivar constantes de similaridade do smoke (ADR-0006) | `implementer` (Sonnet) |
| 3.2.4 | **Gate de cobertura — MUDANÇA DE MECÂNICA (bloqueador resolvido em planejamento):** 85% NÃO roda no job unit (a store só é exercível contra banco real; medir só unit induz mock-que-testa-mock). O gate roda no **job `integration`** (required, SurrealDB real), com `--cov` sobre a suíte completa (unit + integration). Emendar a linha correspondente do CLAUDE.md no MESMO PR | thread principal (ci.yml + CLAUDE.md) |
| 3.2.5 | `security-reviewer` em `kubo/store/` — critérios entregues a ele: queries parametrizadas (inclusive em transação), wrapper checando todos os statements, nenhum acesso fora da store | Sonnet; achados à thread |

## Pontos de consulta ao advisor (obrigatórios)

1. **DDL das migrations antes do commit** (3.1.5).
2. ADR-0008 (3.1.6).
3. Extraordinária: HNSW não aplicar em transação via runner (3.1.4).
4. Conclusão da sessão (deliverables salvos antes).

## Ordem de sacrifício (timebox 8h)

1. **1º corte:** `entity` + `mentions` (viram primeiro item do M6 — são consumidos pela destilação).
2. **2º corte:** travessia de proveniência como método dedicado da store (se a query do teste de integração já cobre o comportamento).
3. **NUNCA cortáveis:** migrations + validação de DDL pelo advisor + gate de cobertura na nova mecânica + teste de proveniência + ADR-0008.

## Critérios de aceite

- [ ] Migrations aplicam limpo em banco zerado via runner; reexecução é no-op (testado). HNSW aplicado (ou consulta extraordinária registrada).
- [ ] Cobertura ≥85% gateando no job `integration` (suíte completa); linha do CLAUDE.md emendada no mesmo PR.
- [ ] Nomes de tabela/aresta idênticos à spec §2.3 (+ `chunk`/`chunk_of`/`run` via ADRs 0002/0008).
- [ ] Travessia de proveniência distilled→item→source coberta por teste de integração — o embrião da prova dos 90 dias.
- [ ] Teste de rejeição de dimensão errada passando.
- [ ] ADR-0008 mergeado com TODOS os desvios da §2.3 (chunk, memory adiada, item não embeddado, escopo schema-vs-algoritmo).
- [ ] PR conforme (branch/título/template); CodeRabbit endereçado (regra de parada vigente); squash-merge.
- [ ] Notas de execução no plano (pendências para M4 explícitas) + transparência de custo no checkpoint final.

## Escopo negativo da sessão

- Nenhum worker, scheduler, API ou CLI. Geração de embedding NÃO (só campos no schema — algoritmo de chunking e geração são M6). litellm NÃO.
- `memory`/`grounded_in` NÃO (D17). Tabelas de trabalho NÃO (só `run`).
- Deploy NÃO (M5.5). PRD NÃO (D18: decisão futura).
- Store não ganha método sem teste que o exija; nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-05; consulta de validação ao advisor (Fable 5): GO com correções, todas incorporadas — gate de cobertura movido para o job de integração (bloqueador), wrapper transacional como entregável nomeado, chaves naturais/normalização pré-decididas, SCHEMAFULL, `mentions` junto de `entity`, `relates_to.kind`, `run.error` minimal, armadilha do HNSW-em-transação pré-autorizada, ADR-0008 alargado para todos os desvios, desenho chunk-registro validado ("nem é opcional": HNSW indexa um vetor por registro).*
