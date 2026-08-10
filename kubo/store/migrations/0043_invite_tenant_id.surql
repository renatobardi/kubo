-- Migration 0043 — associa convite ao tenant e destinos de convite a um tenant (KUBO-215).
-- O convite Telegram é criado por um tenant e, ao ser aceito, gera um destination
-- dentro desse mesmo tenant. tenant_id passa a ser obrigatório em destination
-- (caminho restante de convite) e o convite carrega o tenant de origem.

-- ── invite: qual tenant criou o convite ────────────────────────────────────
DEFINE FIELD OVERWRITE tenant_id ON invite TYPE record<tenant>;

-- ── destination: todo destino nasce com tenant ─────────────────────────────
-- A migration 0025 deixou o campo opcional para rollout suave. Agora todos os
-- callers de `create destination` já passam `tenant_id` (kubo/store/destinations.py
-- e o novo caminho de `accept_invite`); ASSERT é seguro.
DEFINE FIELD OVERWRITE tenant_id ON destination TYPE record<tenant> ASSERT $value != NONE;

-- Backfill: convites/destinos antigos sem tenant apontam para o tenant breakglass.
UPDATE invite SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE;
UPDATE destination SET tenant_id = tenant:breakglass WHERE tenant_id IS NONE;
