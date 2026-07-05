# Sessão 0005 — Porte do feed + Scheduler (M5)

> **Status:** aprovado pelo dono (2026-07-05, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 8 horas efetivas (stop-loss) — o plano mais denso até aqui; ordem de sacrifício abaixo
> **Estrutura:** 1 PR — branch `feat/0005-feed-worker-scheduler` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

O Kubo coleta pela primeira vez: porte do feed do RARA como worker sob contrato (ADR-0009) + agendamento — fechando com **conteúdo RSS real entrando no grafo por execução agendada**, com proveniência completa (`item→source` e `item→run`).

## Contexto e decisões novas

M1–M4 ✅ (82 testes, 98.7% cobertura). Pendências herdadas do 0004 quitadas aqui: aresta `item→run`; validação de nome duplicado no loader; narrowing de `ctx.integrations`.

- **D19 — importação do legado NeonDB:** será worker sob contrato (`neon-import`, integração `postgres` no catálogo), em sessão própria pós-M5, prioridade elevada (corpus: 3k transcrições, 1.250 destilados, 3.5k items — o grafo nasce cheio; scribe provavelmente sai do roadmap). NÃO entra nesta sessão.
- **D20 — prova dos 90 dias reformulada:** "conteúdo (EN/PT-BR) entra, é destilado **em PT-BR**, vira grafo consultável com citação de origem". Fontes reais são EN; embedding multilíngue já validado.
- **Fontes reais (do legado):** 6 feeds RSS — OpenAI, Google DeepMind, Hugging Face, GitHub AI/ML, Import AI, SemiAnalysis. (Os 4 HTML são harvest futuro; as 12 buscas HN são modo futuro do feed — fora desta sessão.)

## Marco 5.1 — Worker feed (`kubo/workers/feed.py`, porte do rara-feed)

**Delegação volta ao normal:** `test-writer` RED → `implementer` GREEN (Sonnet); thread valida contra ADR-0009; `security-reviewer` em `workers/`. **Exceção rule 1:** o toque em `kubo/store/` (param `run` no `upsert_item`, ver 5.2) tem validação linha a linha da thread.

| # | Tarefa |
|---|---|
| 5.1.1 | **Dependências novas (justificar no PR):** `httpx` (fetch **SÍNCRONO** — o contrato é `def run()` síncrono, a store é síncrona, 1 feed por run; async seria fadiga de complexidade sem consumidor), `feedparser` (parse battle-tested), `apscheduler` **pinado na série 3.x** (a 4.x é reescrita pre-release incompatível) |
| 5.1.2 | **Regra inegociável do fetch:** httpx busca, **feedparser recebe BYTES crus** (nunca URL — ele faria fetch próprio sem timeout/teto/limite de redirect; e bytes crus, não texto — ele sniffa encoding melhor). httpx: timeout explícito sempre; `follow_redirects=True` com `max_redirects` baixo; allowlist de scheme http/https; **teto de bytes via `client.stream()` contando chunks** (`response.read()` não tem cap) |
| 5.1.3 | **`external_id` — cadeia de fallback determinística (a idempotência inteira repousa nisto):** `entry.id` → `entry.link` → hash(título+published). RSS real tem guid ausente/instável — sem a cadeia escrita, re-execução no-op vira loteria |
| 5.1.4 | **bozo policy:** bozo=1 com entries parseadas → prossegue + contador em `stats`; bozo com zero entries → erro estruturado. `published_parsed=None` não é erro. Campos sanitizados/capados na borda pydantic; logger nunca loga payload (ADR-0009) — mas **faz bind do `canonical` da source** (config do dono, logável) para run sem item ter rastro de qual feed era |
| 5.1.5 | **SSRF fechado por classe:** o feed worker NUNCA faz fetch de URL vinda de dentro do feed (link de item é dado, não destino — fetch de link é harvest, fase futura). URLs de feed vêm só do schedules.yaml (dono) |
| 5.1.6 | Narrowing de `ctx.integrations` → `ResolvedIntegration` (pendência M4). Tags do legado (`ai`, `dev`, `confiavel`...) vivem **só no schedules.yaml** (no máximo `item.metadata`); `SourcePayload` NÃO ganha campo novo — tags como propriedade de source é problema do neon-import (D19) |
| 5.1.7 | **Testes:** fixtures hostis LIMITADAS aos 4 casos (XML quebrado, entry gigante, encoding estranho, data malformada — fixture adicional só com bug real); unit com respx; integração e2e com servidor HTTP local — **CI nunca toca a internet**. Teste de "dispara de verdade": UM só, trigger de intervalo curto contra servidor local, margem generosa (testes wall-clock são a fonte nº 1 de flake); o resto testa a função do job diretamente |

## Marco 5.2 — Proveniência de execução (carry-over 2× diferido — inadiável)

- Migration `0003`: aresta **`item -[collected_by]-> run`** (simétrica a `produced_by`; ecoa `item.collected_at`).
- **Semântica na re-coleta: last-wins** (DELETE+RELATE na mesma transação do `upsert_item`, consistente com `from_source`) — acumular violaria "re-execução no-op" e cresce sem teto. `upsert_item` ganha param `run` → **validação linha a linha da thread** (é store).
- **Emenda ao ADR-0008** (não ao 0010): registra a aresta, declara **`collected_by` PERMANENTE apontando para `run`** (proveniência de *execução*/observabilidade — diferente de `produced_by`, que é temporário e a fase 3 religa a `flow`), e **argumenta explicitamente que arestas de proveniência não contam no orçamento de contenção do ADR-0002** (precedente: `chunk_of` entrou sem contar) — senão é violação silenciosa da própria regra.

## Marco 5.3 — Scheduler (`kubo/scheduler/`, entrypoint `python -m kubo.scheduler`)

| # | Tarefa |
|---|---|
| 5.3.1 | **ADR-0010 — agendamento da fase 1** (validar com advisor ANTES de implementar): existência/formato/localização do `schedules.yaml`; argumento "não é 4º catálogo" (catálogo descreve ARTEFATOS do ateliê — o que existe; schedules descreve OPERAÇÃO — quando roda; mesma manobra consciente da tabela `run`); misfire policy (`coalesce=true`, `max_instances=1`, grace explícito — quita a miudeza da 0001); **1 feed por entry**; timezone; lifecycle/SIGTERM |
| 5.3.2 | **`schedules.yaml` na RAIZ do repo** (ao lado do docker-compose — a localização é parte do argumento), formato mínimo: `timezone:` top-level **obrigatório e explícito** (`America/Sao_Paulo`) + entries `{worker, cron, config}`. Loader pydantic `extra="forbid"`. **Mapeamento nome→classe de worker = dict HARDCODED** no módulo (análogo do match do `_persist`; registry/plugin/entrypoint é DSL disfarçada — PROIBIDO) |
| 5.3.3 | **1 feed por schedule entry, SEM lista** — premissa load-bearing do ADR-0009 item VII ("uma run = um feed = source consistente"; lista quebraria o last-write-wins sem cross-check). Bônus: isolamento de falha e stats por feed. Custo: 6 jobs em vez de 1 — irrelevante. Os 6 feeds reais entram como 6 entries |
| 5.3.4 | **Lifecycle:** `BlockingScheduler` + handler SIGTERM → `scheduler.shutdown(wait=True)` (run em andamento termina). Cron trigger com a timezone do YAML SEMPRE explícita (default do APScheduler é tz do processo — diverge Mac dev vs container UTC silenciosamente). Kill duro deixa run em `running`: **aceito e documentado no runbook** (query para achar runs órfãos) — SEM janitor/sweep (idempotência já cura os dados; row órfão não tem consumidor) |
| 5.3.5 | Validação de nome duplicado no loader de integrations (pendência M4) |
| 5.3.6 | Runbook curto (docs ou README): subir o scheduler, ler runs órfãos, rodar um worker avulso |
| 5.3.7 | Atualizar a árvore do repositório no CLAUDE.md (`kubo/scheduler/` + `schedules.yaml` não constam) — mesmo PR |

## Pontos de consulta ao advisor (obrigatórios)

1. **ADR-0010 + emenda ADR-0008** antes de implementar o scheduler (5.3.1) — inclui confirmar `collected_by` e a permanência do alvo.
2. Conclusão da sessão (deliverables salvos antes).
3. **Extraordinária:** porte do rara-feed revelar comportamento que o contrato não suporta (ex.: escrita incremental) — reabre D4 **com o dono**, nunca decidido na sessão. Idem se batch multi-fonte se mostrar necessário (revisitaria o item VII do ADR-0009 com o dono).

## Ordem de sacrifício (timebox 8h)

1. **1º corte:** validação de nome duplicado no loader (5.3.5) — re-registra para M6.
2. **2º corte:** misfire fina (fica `coalesce` + `max_instances=1`, grace default documentado).
3. **NUNCA cortáveis:** feed ponta a ponta com item real no grafo; aresta `item→run` com last-wins; execução agendada disparando de verdade; ADR-0010 validado.

## Critérios de aceite

- [ ] Execução agendada real coleta um feed e os itens aparecem no grafo com proveniência completa (`item→source`, `item→run`) — verificável por query documentada.
- [ ] Re-execução é no-op com feed real (idempotência via cadeia de `external_id`).
- [ ] Fixtures hostis (4 casos) passando; zero rede no CI; teste temporal único e estável.
- [ ] `schedules.yaml` com os 6 feeds reais + timezone explícita; loader `extra="forbid"`; dict hardcoded worker→classe.
- [ ] SIGTERM com shutdown limpo testado/documentado; runbook com query de runs órfãos.
- [ ] Cobertura ≥85% mantida; ADR-0010 + emenda ADR-0008 mergeados; CLAUDE.md com árvore atualizada.
- [ ] PR conforme; CodeRabbit endereçado; squash-merge; **main verificado ponta a ponta após o merge** (lição do incidente do M4 — todos os commits pretendidos presentes antes de declarar concluído).
- [ ] Notas de execução com pendências explícitas para M5.5/M6 e para a sessão do neon-import (D19).

## Escopo negativo da sessão

- Destilação NÃO (itens ficam brutos no grafo — e está certo assim). Embedding NÃO. litellm NÃO.
- Fetch de link de item NÃO (é harvest). Modo HN NÃO. Scribe NÃO. `neon-import` NÃO (D19, sessão própria).
- Deploy NÃO (M5.5 — scheduler roda local nesta sessão). API/CLI NÃO. Flow/board NÃO.
- Async NÃO (httpx síncrono + BlockingScheduler — sem event loop, sem AsyncIOScheduler). Registry/plugin NÃO.
- Nenhuma dependência além das 3 declaradas. Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-05; fontes reais mineradas do NeonDB legado (feed_sources); consulta de validação ao advisor (Fable 5): GO condicionado a 10 correções, todas incorporadas — external_id com cadeia de fallback, 1-feed-por-entry (premissa do ADR-0009 VII), síncrono em vez de async, feedparser-recebe-bytes, SSRF fechado por classe, aresta na emenda ADR-0008 com permanência declarada e argumento do orçamento de contenção, last-wins na re-coleta, timezone obrigatória + pin APScheduler 3.x, schedules.yaml na raiz + kubo/scheduler/, SIGTERM/runs órfãos no runbook.*
