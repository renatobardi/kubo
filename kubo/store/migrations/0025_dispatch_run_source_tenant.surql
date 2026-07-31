-- Migration 0021 — tenant_id em dispatch, run e source (KUBO-128).
-- DDL idempotente via OVERWRITE. tenant_id obrigatório (ASSERT != NONE):
-- dados legados sem tenant_id devem ser backfilled antes do deploy prod, ou
-- o ASSERT rejeita o CREATE/UPDATE (falha alta, nunca lost-write mudo).

-- ── dispatch: fato de entrega de digest (ADR-0015) ────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON dispatch TYPE record<tenant> ASSERT $value != NONE;

-- ── run: execução de worker (ADR-0009) ────────────────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON run TYPE record<tenant> ASSERT $value != NONE;

-- ── source: Cadastro de fonte (ADR-0025) ──────────────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON source TYPE record<tenant> ASSERT $value != NONE;

-- Unicidade de source passa a ser por (tenant_id, kind, canonical), não global.
-- Permite que dois tenants cadastrem a mesma URL de feed independentemente.
DEFINE INDEX OVERWRITE source_kind_canonical ON source FIELDS tenant_id, kind, canonical UNIQUE;
