-- Migration 0025 — tenant_id em dispatch, run, source e destination (KUBO-128).
-- DDL idempotente via OVERWRITE. tenant_id obrigatório (ASSERT != NONE):
-- dados legados sem tenant_id são backfilled abaixo para o tenant breakglass
-- antes do ASSERT; se houver múltiplos tenants em prod, rodar backfill manual
-- antes do deploy (KUBO-128 follow-up).

-- ── Backfill: registros legados sem tenant_id apontam para o tenant breakglass.
-- Idempotente: só atualiza onde tenant_id IS NONE.
UPDATE dispatch SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE;
UPDATE run SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE;
UPDATE source SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE;
UPDATE destination SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE;

-- ── dispatch: fato de entrega de digest (ADR-0015) ────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON dispatch TYPE record<tenant> ASSERT $value != NONE;

-- ── run: execução de worker (ADR-0009) ────────────────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON run TYPE record<tenant> ASSERT $value != NONE;

-- ── source: Cadastro de fonte (ADR-0025) ──────────────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON source TYPE record<tenant> ASSERT $value != NONE;

-- ── destination: cadastro de destino (ADR-0027) ───────────────────────────────
-- tenant_id opcional nesta fase: rollout suave, ASSERT vem quando todos os
-- callers forem migrados (KUBO-128 follow-up).
DEFINE FIELD OVERWRITE tenant_id ON destination TYPE option<record<tenant>>;

-- Unicidade de source passa a ser por (tenant_id, kind, canonical), não global.
-- Permite que dois tenants cadastrem a mesma URL de feed independentemente.
DEFINE INDEX OVERWRITE source_kind_canonical ON source FIELDS tenant_id, kind, canonical UNIQUE;

-- Unicidade de destination passa a ser por (tenant_id, channel, address).
DEFINE INDEX OVERWRITE destination_tenant_channel_address
  ON destination FIELDS tenant_id, channel, address UNIQUE;
