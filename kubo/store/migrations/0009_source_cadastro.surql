-- Migration 0009 — `source` vira Cadastro (ADR-0025, ticket #104).
-- Prefactor: adiciona o estado de Cadastro e a unicidade composta, SEM alterar a
-- coleta atual — aditivo e compatível pra trás. O runner envolve em
-- BEGIN;...;COMMIT; (ADR-0007); DDL idempotente (OVERWRITE / IF EXISTS).

-- ── estado do Cadastro ──────────────────────────────────────────────────────
DEFINE FIELD OVERWRITE enabled     ON source TYPE bool DEFAULT true;
DEFINE FIELD OVERWRITE archived_at ON source TYPE option<datetime>;
DEFINE FIELD OVERWRITE tags        ON source TYPE array<string> DEFAULT [];

-- ── unicidade: UNIQUE(canonical) global → UNIQUE(kind, canonical) ────────────
-- A composta é estritamente MENOS restritiva, então nenhum registro existente
-- conflita (ADR-0025). O índice antigo PRECISA sair: se ficar, continua barrando
-- a mesma canonical em kinds diferentes.
-- NOTA (#105): a composta só passa a ser EXERCÍVEL quando o caminho de escrita
-- virar kind-aware. Hoje `upsert_source` deriva o record id só de `canonical`
-- (sha256), então via store a mesma canonical em kinds diferentes ainda colidiria
-- no mesmo record — a composta é reachable só no nível do banco por ora. O #105
-- troca isso pelo caminho surrogate (lookup por (kind, canonical) → update;
-- ausente → id fresco), SEM reescrever ids/arestas existentes.
REMOVE INDEX IF EXISTS source_canonical ON TABLE source;
DEFINE INDEX OVERWRITE source_kind_canonical ON source FIELDS kind, canonical UNIQUE;

-- ── backfill dos registros criados antes desta migration ────────────────────
-- DEFINE FIELD ... DEFAULT não é retroativo: os 6 feeds + os `source` de GitHub
-- já coletados precisam nascer ativos e sem tags para o sweep (ticket #108) os
-- enxergar. Os dois campos num ÚNICO UPDATE: o SCHEMAFULL valida o registro
-- inteiro a cada escrita, então setar só um deixaria o outro `NONE` e a coerção
-- (`array`/`bool` esperado) falharia. `WHERE enabled = NONE` pega só os legados
-- (idempotente: pós-migration nenhum registro fica sem os campos).
UPDATE source SET enabled = true, tags = [] WHERE enabled = NONE;
