# Sessão 0007 — Relatório de execução do import do legado NeonDB

> Execução em produção (kubo-test) em 2026-07-07. Todos os corpora reconciliados.
> Fonte de verdade: os `run.stats` gravados por cada corpus (worker `neon_import`).

## Reconciliação por corpus (§7.2.1)

Reconciliado = origem(Neon) = importados + já-presentes + rejeitados, para todo corpus.

| corpus | origem (Neon) | importados | (sem_conteudo) | já-presentes | rejeitados | reconciliado |
|---|---:|---:|---:|---:|---:|:---:|
| sources | 140 | 123 | 0 | 17 | 0 | ✓ |
| news | 741 | 603 | 544 | 138 | 0 | ✓ |
| videos | 2.273 | 2.273 | 59 | 0 | 0 | ✓ |
| podcasts | 614 | 614 | 0 | 0 | 0 | ✓ |
| emails | 226 | 226 | 0 | 0 | 0 | ✓ |
| linkedin | 7 | 7 | 0 | 0 | 0 | ✓ |
| distillations | 1.267 | 935 | — | 0 | 332 | ✓ |

### Rejeitados / cortes — contados e motivados (nada some em silêncio)

- **distillations: 332 pulados** — TODOS "sem summary" (`distillations.content` vazio).
  Verificado no Neon: `sem_content=332`, `sem_content_mas_com_claims=0` (nenhum tinha
  `structured.claims` a preservar — **zero perda de conhecimento**), `sem_content_e_nao_done=325`
  (destilados falhos/incompletos). **0 órfãos** — todo `source_key` resolveu a um item
  (cobertura de itens completa).
- **sem_conteudo (item com `content=""`)** é sub-contagem de `importados`, não corte: o item
  é âncora do `derived_from` (ADR-0012 §VIII). news 544 = Hacker News (posts-link, 536) +
  itens sem corpo/excerpt/transcript; videos 59 = transcripts youtube com coluna de texto vazia.
  Verificado que a join de transcript funciona (`news_com_transcript_texto=115`), então os
  vazios são fiéis ao legado, não bug.
- **já-presentes:** sources 17 = 6 rss vivas (preservadas, não mutadas) + 11 HN colapsadas;
  news 138 = urls legadas que casam com itens da coleta viva (dedupe).

## Estado final do grafo (contagens vivas)

| tabela/aresta | contagem |
|---|---:|
| source | 129 |
| item | 5.709 |
| distilled | 935 |
| from_source (item→source) | 5.709 |
| collected_by (item→run) | 5.709 |
| derived_from (distilled→item) | 935 |
| produced_by (distilled→run) | 935 |

Proveniência COMPLETA: todo item tem source + run; todo destilado tem item + run.

## Prova de no-op (§7.2.3) — re-execução durante a sessão

Critério (ADR-0012 §VIII): contagens estáveis, ZERO registros novos, IDs estáveis.

- **distillations re-rodado:** importados=0, já-presentes=935, rejeitados=332 — RECONCILIADO.
  (`distilled_for` detectou os 935 como já-presentes; nenhum destilado novo.)
- **emails re-rodado:** importados=0, já-presentes=226 — RECONCILIADO.

## Restore com o acervo (§7.2.4)

Dump fresco `kubo-20260707T225224Z.surql` (55,7 MB, vs 2,6 MB pré-import) restaurado num
banco EFÊMERO em dois passos (base → relations ENFORCED, runbook §4). Ambos os passos
"Import executed with no errors". Contagens restauradas == vivas: source=129, item=5.709,
distilled=935, derived_from=935, from_source=5.709, produced_by=935. **Backup+restore do
acervo provados.**

## Insumo para o M6 (backfill de chunks/embedding)

- **935 destilados aguardando backfill** de chunks/embedding — TODOS os legados (o import
  não gera chunks; §II/§IV). O M6 os acha por `produced_by → run(worker="neon_import")` sem
  `chunk_of` (§IV).
- **4.774 itens sem destilado** (5.709 − 935) — conteúdo bruto no grafo, sem destilação
  legada; disponíveis para destilação futura.

## Checklist de desligamento do Neon (tarefas do dono)

1. [x] **Relatório de reconciliação aceito** pelo dono — 2026-07-08.
2. [x] **pg_dump completo do Neon** feito e guardado (44 MB, `~/Backups/neon/`, 2026-07-06).
3. [x] **Restore do backup Kubo com acervo** testado (§7.2.4 acima).
4. [ ] **Desativar o Neon** — os 3 pré-requisitos acima estão ✅; liberado para o dono
   desativar. 2ª rodada de cortes (se houver) roda do pg_dump restaurado localmente
   (rota de fallback, ADR-0012 §IX).
