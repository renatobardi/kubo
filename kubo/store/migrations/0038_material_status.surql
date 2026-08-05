-- Migration 0038 — Material: ciclo de vida de ingestão (KUBO-202, ADR-0049 §III).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Status de ingestão ──────────────────────────────────────────────────────
-- `pending`: upload gravou o arquivo, processamento (parse+summary+sections) pendente.
-- `ready`:   ingestão completa — chapters, sections e summary disponíveis.
-- `failed`:  ingestão falhou; `error` tem o motivo.
-- `ingested_at`: timestamp do fim da ingestão (ready) ou None.
DEFINE FIELD OVERWRITE status       ON material TYPE string DEFAULT "ready" ASSERT $value IN ["pending", "ready", "failed"];
DEFINE FIELD OVERWRITE error        ON material TYPE option<string>;
DEFINE FIELD OVERWRITE ingested_at  ON material TYPE option<datetime>;

-- Backfill: materiais existentes já têm chapters/sections → ready.
-- (SurrealDB DEFAULT só aplica em CREATE novo; UPDATE retroage os existentes.)
UPDATE material SET status = "ready", ingested_at = time::now() WHERE status = NONE;

-- Índice para o scheduler listar pending por tenant+user sem scan full.
DEFINE INDEX OVERWRITE material_pending ON material FIELDS tenant_id, user_id, status;
