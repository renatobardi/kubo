# Sessão 0007 — Import do legado NeonDB (D19)

> **Status:** aprovado pelo dono (2026-07-06, sessão de planejamento no Cowork)
> **Ambiente de execução:** Claude Code CLI (Opus + `/advisor` Fable 5)
> **Timebox:** 6 horas efetivas (stop-loss) — ordem de sacrifício abaixo
> **Estrutura:** 1 PR — branch `feat/0007-neon-import` (título convencional em inglês, D16)
> **Contrato:** executa SOMENTE o que está aqui. Fora dele = reabrir planejamento.

---

## Missão

O grafo herda o legado do RARA: `scripts/neon_import.py` (one-off, via store) importa cadastros e conteúdo do NeonDB — ~6k itens, 3k transcrições, 1.250 destilados — com proveniência completa, validação de borda e **reconciliação completa como condição de desligamento do Neon**.

## Contexto e decisões

- **D19 EMENDADA (proposta do dono):** import é **script one-off**, NÃO worker sob contrato. Racional YAGNI: o mecanismo de worker existe para recorrência; as garantias vêm da **store** que já existe — idempotência (record IDs determinísticos), proveniência (`start_run`/`finish_run` + `collected_by`), invariante "acesso só via store" respeitado. Precedente: `scripts/embedding_smoke.py`. Perda consciente: escopo de ctx do contrato — aceitável em código do dono, revisado em PR, rodado uma vez. ADR-0012 registra.
- **D19a:** 1.250 destilados legados importados. **Marca legacy = proveniência** (`produced_by → run` com `worker="neon_import"` — queryável, zero mudança de schema; o M6 acha os sem-embedding pela ausência de `chunk_of`). `derived_from → item`.
- **D19b:** todo conteúdo + cadastros. Ficam de fora (recuperáveis do pg_dump do dono): `feedback` (2.1k), `gate_decisions` (4.8k), `interest_profile` — fase 3 importa do dump se quiser.
- **FATO CRÍTICO:** o dono fará **pg_dump completo do Neon e o DESATIVARÁ** após a migração (motivo externo). O import é a última chance viva; o no-op precisa ser provado ANTES da desativação. **Rota de fallback registrada:** o script lê `NEON_DATABASE_URL` genérica — roda contra um Postgres local restaurado do pg_dump; cortes são recuperáveis mesmo com Neon morto.

## Pré-requisitos / tarefas do dono

- **Launchd do rsync de backup plugado no Mac ANTES da execução em prod** (gatilho combinado — o import torna o grafo insubstituível). Receita no runbook.
- `NEON_DATABASE_URL` no `.env` do servidor (dono cria; agente nunca lê). **Endpoint DIRETO, não `-pooler`** (PgBouncer transaction-mode quebra named cursors); `sslmode=require`. Recomendado: role read-only no Neon.
- **Mapa manual dos 6 feeds** (input do dono NA SESSÃO): qual `feed_sources` legado corresponde a qual source existente — verificado à mão, não confiado em match automático de URL.
- pg_dump completo do Neon feito e guardado ANTES de qualquer desativação (externo à sessão, registrado no checklist).

## Achado do advisor — bloqueante resolvido em planejamento

**`insert_distilled` NÃO é idempotente** (cria registro novo a cada chamada, por design — destilação é evento). Re-rodar o corpus de distillations duplicaria os 1.250. **Resolução:** adicionar à store, por TDD (~45min), um método de leitura mínimo — `distilled_for(db, item)` (travessia `item ← derived_from ← distilled`) — e o script pula itens que já têm distilled de import. NÃO é superfície especulativa: **o M6 precisa exatamente dessa leitura** para o backfill. Validação linha a linha da thread (é store).

## Marco 7.1 — Script de import (`scripts/neon_import.py`)

| # | Tarefa |
|---|---|
| 7.1.1 | Dependência `psycopg` (v3) em **dependency-group próprio do uv** (`import`) — não permanece na imagem de produção. Justificar no PR |
| 7.1.2 | **Lógica de mapeamento legado→grafo em FUNÇÕES PURAS testadas com fixtures** (test-writer RED → implementer GREEN); casca de I/O fina. Execução: dentro do `kubo-test` via `docker compose run`, **um corpus por invocação** (`--corpus`) |
| 7.1.3 | **Mapeamentos:** feed_sources/target_channels/podcast_feeds/email_sources → `source` (upsert por URL canônica; os 6 feeds existentes via **mapa manual do dono** — relatório mostra "6 reconciliadas, N itens sobrepostos deduplicados"; itens legados desses feeds usam a MESMA cadeia de external_id do feed worker: guid → link — senão o período de sobreposição duplica); items/news_items/channel_videos/podcast_episodes/emails → `item` (external_id determinístico das chaves legadas; tags E **timestamps originais** em `item.metadata` — `collected_at` é READONLY e receberá a data do import; o original vai no metadata, custo zero); transcripts → `content` do item de vídeo (**concatenação em STREAMING**: named cursor do psycopg + itersize, `ORDER BY video_id, seq`, agrupamento por vídeo — NUNCA `fetchall()` dos 790k segments); distillations → `insert_distilled(chunks=[])` (**verificado no código: sequência vazia funciona hoje, sem mudança de API** — dizer literalmente para a sessão não inventar) |
| 7.1.4 | **ORDEM OBRIGATÓRIA dos corpora:** sources → todos os itens → distillations POR ÚLTIMO (`derived_from` é ENFORCED — distillation apontando para item inexistente FALHA; órfã vira `skipped_invalid` contada, não crash) |
| 7.1.5 | **Probe empírico CEDO:** importar primeiro o MAIOR transcript. Se o RPC WebSocket engasgar com o payload, trocar `SURREAL_URL` para `http://` (client aceita). Se nem HTTP aguentar → consulta extraordinária ao advisor (chunking de content bruto é decisão de design, não improviso) |
| 7.1.6 | **Política de caps do import ≠ do feed:** teto de sanidade com **reject+log, NUNCA truncamento** (truncar transcript na última chance viva = perda de dado; o cap de 64KiB do feed é do worker, não da store). Conteúdo legado = hostil (veio da web): sanitização na borda pydantic, como sempre. Timestamps de `distilled` (created_at READONLY): **perda consciente**, coberta pelo pg_dump — registrada no ADR, não descoberta depois |
| 7.1.7 | Armadilhas Neon: cold-start do autosuspend (retry simples na 1ª conexão); evitar transação gigante por corpus (statement timeout); cadeia de conexão NUNCA logada |

## Marco 7.2 — Revalidação (condição de desligamento do Neon — NUNCA cortável)

| # | Tarefa |
|---|---|
| 7.2.1 | **Relatório de reconciliação por corpus:** contagem origem (Neon) × destino (grafo) + **toda linha rejeitada contada e logada com motivo** (`skipped_invalid` com razões — nada descartado em silêncio) + sobreposições deduplicadas. Diferenças explicadas linha a linha. Relatório **commitado nas notas da sessão** |
| 7.2.2 | Amostra de proveniência de cada corpus: item → source, item → run de import; distilled → derived_from → item |
| 7.2.3 | **Re-execução provada no-op DURANTE a sessão** (antes de qualquer desativação): ao menos 1 corpus de item re-rodado (contagens idênticas) + corpus de distillations re-rodado (o `distilled_for` impedindo duplicação) |
| 7.2.4 | **Restore do backup do Kubo com o acervo dentro** testado (banco efêmero, contagens > 0 incluindo os importados) |

## Checklist de desligamento do Neon (tarefas do dono — a desativação é externa, o gate fica registrado)

1. Relatório de reconciliação **aceito explicitamente pelo dono**.
2. pg_dump completo do Neon feito e guardado (cobre inclusive feedback/gates/interest não importados).
3. Restore do backup Kubo com acervo testado (7.2.4).
4. Só então desativar. 2ª rodada de cortes (se houver) pode rodar do pg_dump restaurado localmente.

## Marco 7.3 — Registro

ADR-0012: D19 emendada (script vs worker, racional, perda consciente), D19a/b, `distilled_for` (novo método da store e por quê não é especulação), legacy-via-proveniência, política de timestamps, política de caps do import, reconciliação por mapa manual + URL canônica, rota de fallback do pg_dump. `doc-writer` draft → **advisor valida** → thread crava.

## Pontos de consulta ao advisor (obrigatórios)

1. ADR-0012 antes de cravar.
2. **Extraordinária:** WS e HTTP falharem no maior transcript (7.1.5); ou external_id legado não reconstruível deterministicamente (degrada a idempotência — repensar prova de no-op por corpus); ou `distilled_for` se revelar invasivo no strict/cobertura do M3 (alternativa: single-shot com verificação de contagem prévia).
3. Conclusão da sessão (deliverables + relatório salvos antes).

## Delegações

Funções puras de mapeamento: `test-writer` RED → `implementer` GREEN. `distilled_for` na store: TDD com **validação linha a linha da thread**. Casca I/O + execução em prod (ssh): thread principal. `security-reviewer` em `scripts/` + no toque da store (conexão nunca logada, binds, conteúdo hostil). `doc-writer`: ADR + template do relatório.

## Ordem de sacrifício (timebox 6h)

1. **Stretch desde o início (não corte sob pressão):** `emails` + `podcast_episodes` — recuperáveis via pg_dump restaurado (rota de fallback), SEM prazo perdido.
2. **2º corte:** `target_channels` (103 canais sem coletor — viram sources adormecidas depois).
3. **NUNCA cortáveis:** feeds/items/transcripts/distillations; relatório de reconciliação completo; no-op provado (itens E distillations); backup+restore com acervo; `distilled_for` testado; ADR-0012.

## Critérios de aceite

- [ ] Corpora núcleo importados com proveniência completa (verificada por amostra de cada corpus).
- [ ] Relatório de reconciliação completo, com rejeitadas contadas/motivadas, commitado nas notas.
- [ ] No-op provado em re-execução (itens + distillations) durante a sessão.
- [ ] `distilled_for` na store por TDD, cobertura mantida ≥85%.
- [ ] Probe do maior transcript passado (ou consulta extraordinária registrada).
- [ ] Restore do backup Kubo com acervo testado.
- [ ] ADR-0012 mergeado; PR conforme (CodeRabbit endereçado; squash; main verificado ponta a ponta).
- [ ] Notas de execução: insumo para o M6 (nº de distilled aguardando backfill de chunks/embedding; nº de itens sem distilled) + checklist de desligamento entregue ao dono.

## Escopo negativo da sessão

- Chunks/embedding NÃO (backfill é M6). Destilação nova NÃO. Coletor YouTube NÃO (canais = sources adormecidas).
- feedback/gates/interest_profile NÃO (vivem no pg_dump do dono). Worker/manifest/integração de catálogo NÃO (D19 emendada).
- Nada agendado (não entra no schedules.yaml). Neon estritamente read-only. Desativação do Neon NÃO é desta sessão.
- Truncamento de conteúdo NUNCA. Nenhuma decisão nova de arquitetura sem reabrir planejamento.

---

*Fontes: sessão de planejamento Cowork de 2026-07-06; consulta de validação ao advisor (Fable 5): GO com emendas, todas incorporadas — achado bloqueante da não-idempotência de `insert_distilled` resolvido via `distilled_for` (que o M6 precisa de qualquer forma), `chunks=[]` verificado no código, legacy-via-proveniência (schema SCHEMAFULL sem campo para marca), timestamps (item→metadata; distilled = perda consciente), probe do maior transcript com fallback WS→HTTP, caps reject+log nunca truncar, mapa manual dos 6 feeds + mesma cadeia de external_id do feed worker, ordem sources→items→distillations (derived_from ENFORCED), named cursors/endpoint direto/sslmode=require, rota de fallback via pg_dump restaurado.*
