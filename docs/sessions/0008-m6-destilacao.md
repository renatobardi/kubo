# Sessão 0008 — M6: destilação + grafo buscável (prova dos 90 dias)

> **Status:** aprovado pelo dono (2026-07-08, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5) — sessão de fronteira: D6 vira código; **Opus-main obrigatório**, sem experimento de modelo
> **Timebox:** 8 horas efetivas (stop-loss) — ordem de sacrifício abaixo
> **Estrutura:** 1 PR — branch `feat/0008-m6-distillation` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

Fechar a fase 1: item bruto vira destilado **PT-BR** com entidades, tudo chunked+embeddado, consultável por `kubo query` com citação de origem (`kubo show --provenance`). A prova dos 90 dias deixa de ser promessa e vira comando executável.

## Decisões do dono (fixadas no planejamento)

- **D21:** M6 em sessão única de 8h (split recusado); a ordem de sacrifício protege D6 — a parte de segurança **nunca** é feita sob pressão de timebox.
- **D22:** LLM de destilação = **Groq free tier** via LiteLLM. Quota estourada = **espera, jamais fallback para provider pago** — zero gasto de token nesta fase. Emenda operacional do advisor (E3): backoff **com teto** (ex.: 3 tentativas exponenciais) dentro do run; estourou o teto → o run fecha graciosamente com o que processou e o **agendamento diário come a fila** (é isso que "backlog leva dias" significa). Nunca backoff infinito — colide com o job do dia seguinte. Job com `max_instances=1` + `coalesce` (padrão ADR-0010).
- **D23:** shape do distilled = **resumo PT-BR + entidades** (aresta `mentions`). Claims FORA — sem consumidor definido (fase 2/3 decide).
- **D24:** destilação **agendada diária** (entry no `schedules.yaml`, ~08:30, após a coleta das 08:00). Sem garantia de ordem com a coleta — se atrasar, itens do dia esperam até amanhã; idempotente, documentado, **não é bug**.

## Decisões fixadas pela consulta ao advisor (GO com emendas E1–E5, todas incorporadas)

- **O que se embedda: chunks do DISTILLED (PT-BR), nunca do item bruto.** O schema já força (`chunk_of: IN chunk OUT distilled ENFORCED`, migration 0001; ADR-0008 §II: item preservado, não embeddado). Consequência: com D20, o corpus vetorial é 100% PT-BR — **não existe par cross-lingual no caminho vetorial**; o conteúdo EN bruto é alcançado por proveniência (`distilled → derived_from → item`), não por vetor. Resíduo: summaries legados do RARA que estiverem em EN — spot-check de idioma no backfill; EN em volume = degradação conhecida registrada no ADR-0013, **não re-destilar legados nesta sessão** (queima quota e timebox).
- **Chunking (a):** split por parágrafo → merge greedy até o teto → **sem overlap**. Contagem por heurística `len(text) // 4` (sem dependência nova; tiktoken mediria errado o Gemini, `countTokens` é rede por chunk). **Teto efetivo ~1.600 tokens estimados** (margem 20–25% sobre o limite ~2.048 — nunca confiar no auto-truncate do provider: truncagem silenciosa é perda silenciosa). Cascata determinística: parágrafo > teto → split por sentença; sentença > teto (patológico) → hard split por chars. Funções puras, TDD.
- **Query (b):** a pergunta do `kubo query` é embeddada com a MESMA tripla `(gemini-embedding-001, 768, SEMANTIC_SIMILARITY)` — obrigatório pelo ADR-0006 (uso simétrico; triplas diferentes não são comparáveis). Ressalva `RETRIEVAL_QUERY/DOCUMENT` já registrada lá como revisável-se-recall-decepcionar.
- **Contrato do destilador (d/E1) — decidido AGORA, não na sessão:**
  1. Worker não conhece RecordID (ADR-0009). O M6 adiciona o **primeiro método do seam `knowledge`**: `items_to_distill(limit) -> list[ItemView]` (view tipada read-only: ref opaco + title + content). Gatilho legítimo do ADR-0009 ("métodos entram quando um worker exigir leitura, com teste que o justifique").
  2. `DistilledPayload` (novo membro da união, `type: Literal["distilled"]`, `schema_version` 1) **ecoa o ref opaco**; o runner resolve ref→RecordID e chama `insert_distilled`. Se o seam opaco não fechar na prática → **consulta extraordinária ao advisor**, não improviso.
  3. Entidades no payload **por nome** (`EntityRef(name, kind)`); o runner faz `get_or_create_entity` e traduz para RecordIDs. Padrão "runner traduz payload→store" preservado.
  4. O payload carrega chunks **já com embedding** (espelho 1:1 com `Chunk` da store, tripla inclusa) → o `RunContext` ganha o cliente de embedding/executor **nesta sessão** (campo preanunciado no ADR-0009; ADR-0013 o emenda registrando).
  5. Lote pequeno por run (`max_items` na config do worker) — reduz a janela de perda da persistência-no-fim-do-run. `insert_distilled` não é idempotente, mas o worker só seleciona itens sem destilado — re-run cura (ADR-0009 §VII).
- **Executor api (e): esqueleto sim, framework não.** `kubo/executors/api.py` com `ApiExecutor` (config tipada: model, temperature, max_tokens, response_format) — é onde **D6 regra 1 vira construção**: a assinatura **não aceita tools**, e o template de demarcação untrusted (regra 3) mora nele, não na disciplina do worker. Worker **nunca** chama `litellm.completion` cru. SEM roteamento por persona, SEM fallback declarável, SEM budget (fase 3; ADR-0013 registra que o executor nasce parcial). Não depender de json_schema enforcement do lado do Groq (varia por modelo) — o gate é o pydantic nosso.
- **Modelo Groq (g):** primário **`llama-3.3-70b-versatile`**; comparação empírica com **`moonshotai/kimi-k2-instruct`** no smoke (10 itens × 2 modelos, free tier — 20 chamadas). **Verificar o catálogo vivo do Groq no dia** (depreciam rápido; muda a escolha, não a mecânica). Modelo escolhido = pinagem-por-evidência no ADR-0013 (padrão ADR-0005).
- **Smoke de qualidade = gate de "não-lixo", não de qualidade fina** (mesma epistemologia do ADR-0006, n=10). Critérios explícitos: 10/10 JSON válido no schema; 10/10 saída em PT-BR; entidades não-absurdas; **canários de injection ignorados (E4)** — 1–2 dos itens do smoke carregam tentativa de prompt injection embutida no conteúdo (fabricada sobre item real). D6 sem teste adversarial é D6 por fé. Falhou → troca modelo, **não afrouxa critério**; os 2 candidatos reprovarem em PT-BR → a premissa de D22 cai e a conversa volta **ao dono** (nunca fallback pago silencioso).
- **Backfill dos 935 = script one-off (E5)**, não worker (precedente ADR-0012/D19: worker é para recorrência). Retomável por construção: pula distilled que já tem chunk (condição transitória do ADR-0012 §IV dá de graça). Inclui **spot-check de idioma** dos summaries legados.

## Marcos (ordem corrigida pelo advisor — query provada cedo, D6 sem pressão)

| # | Marco |
|---|---|
| 8.1 | **ADR-0013 em esqueleto PRIMEIRO** (E2 — "ADR antes do código" é regra): decisões acima viram o draft; no fim da sessão só se finaliza. Emenda o ADR-0009 (mecânica D6 regras 1 e 3; campo novo no ctx) |
| 8.2 | **Chunking** — funções puras, TDD, conforme (a) |
| 8.3 | **Cliente de embedding** gemini batch; tripla de proveniência em cada chunk; rate limit tratado (retomável) |
| 8.4 | **Backfill dos 935** (script one-off, E5) — **gated no "pode executar" do dono**; spot-check de idioma; relatório de contagens (distilled sem chunk antes/depois) |
| 8.5 | **CLI `kubo query` + `kubo show --provenance`** — provada ponta a ponta **sobre o acervo legado** logo após o backfill: mesmo se a destilação nova atrasar, "conhecimento consultável" já está entregue |
| 8.6 | **ApiExecutor + worker destilador** (D6 vira código, sem pressão de timebox): demarcação untrusted, zero tools por construção, saída JSON schema-validated (falha = rejeita e conta, nunca "aproveita"), backoff com teto (D22/E3). **Smoke 10 itens × 2 modelos com canários** antes de qualquer backlog. Logger nunca loga payload NEM a saída crua do LLM pré-validação (contadores + `errors(include_input=False)` apenas) — **com teste** |
| 8.7 | **Entry no `schedules.yaml`** (~15min, último ato) — destilação diária 08:30, `max_items` configurado; o agendado come a fila do backlog |
| 8.8 | **Registro:** ADR-0013 finalizado (advisor valida antes de cravar); notas de execução com estado da fila (itens sem destilado restantes) e insumos da fase 2 |

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0013 antes de cravar (fim da sessão; o esqueleto do 8.1 já nasce desta consulta de planejamento).
2. **Extraordinária:** seam opaco não fechar (ref→RecordID vazando pro worker); smoke reprovar os 2 modelos em PT-BR (decisão volta ao dono); summaries legados majoritariamente EN (advisor pediria trios cross-lingual no smoke de embedding); batch embed + insert transacional estourar memória/latência em lote real.
3. Conclusão da sessão (deliverables salvos antes).

## Tarefas do dono

- `GROQ_API_KEY` no `.env` do servidor (dono cria; agente nunca lê — invariante 8).
- **"Pode executar"** explícito antes do backfill (8.4) e antes do primeiro run de destilação em produção (8.6/8.7).
- Decidir, se o smoke reprovar ambos os modelos: trocar candidato Groq vs. rediscutir D22.

## Ordem de sacrifício (timebox 8h)

1. **1º corte (esperado, não é falha):** backlog completo dos 4.774 — o agendado come a fila nos dias seguintes.
2. **2º corte:** entidades (degrada para só-resumo; D23 vira pendência registrada).
3. **NUNCA cortáveis:** D6 completo com canários; chunking+embedding+`kubo query` ponta a ponta sobre o legado; smoke de qualidade; idempotência/retomabilidade; ADR-0013.

## Critérios de aceite

- [ ] `kubo query "pergunta em PT-BR"` retorna hits do acervo com origem citável; `kubo show --provenance` percorre distilled → item → source → run.
- [ ] 935 legados com chunks+embedding (tripla de proveniência em cada chunk); backfill retomável provado (re-run = no-op).
- [ ] Worker destilador sob contrato (manifest + `DistilledPayload` + seam `items_to_distill`); smoke 10×2 com canários de injection passado e registrado.
- [ ] D6 regras 1 e 3 como construção (executor sem tools; demarcação untrusted) com testes — incluindo o teste de que log não vaza payload nem saída crua.
- [ ] Ao menos um lote real de destilação em produção (novos distilled PT-BR com entidades no grafo, proveniência completa).
- [ ] Entry no `schedules.yaml`; quota estourada comprovadamente fecha o run gracioso (teste com mock).
- [ ] Cobertura ≥85% mantida; ADR-0013 mergeado; PR conforme (CodeRabbit endereçado; squash; main verificado ponta a ponta).
- [ ] Notas de execução: fila restante, modelo pinado, resultado do spot-check de idioma, insumos para fase 2.

## Escopo negativo da sessão

- Claims NÃO (D23). Re-destilar legados NÃO. Embeddar item bruto NÃO (schema força; ADR-0008).
- Roteamento por persona / fallback / budget NÃO (executor nasce parcial — fase 3). Personas YAML NÃO.
- Provider pago NÃO, nem como fallback de emergência (D22 — decisão volta ao dono se Groq falhar).
- Overlap de chunking NÃO (entra na escada de revisão junto com RETRIEVAL_* se recall decepcionar).
- API/views NÃO (fase 2). Harvest/scribe NÃO. Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-08; decisões do dono D21–D24; consulta de validação ao advisor (Fable 5): GO com emendas E1–E5, todas incorporadas — shape do DistilledPayload/seam fixado em planejamento, ADR-esqueleto no início, backoff com teto + lote por run, canários de injection no smoke, backfill como script one-off com spot-check de idioma; chunking parágrafo/greedy/sem-overlap com teto ~1.600 tokens estimados; embedda-se o distilled PT-BR (cross-lingual dissolvido pelo schema + D20); executor api como esqueleto onde D6 vira construção; llama-3.3-70b-versatile vs kimi-k2 decidido por smoke empírico.*
