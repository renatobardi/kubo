-- Migration 0022 — Tema e Plano de estudo (ADR-0043, KUBO-136).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Tema: um material lido com um objetivo ──────────────────────────────────
-- Dado PESSOAL, como o material: escopo `user` DENTRO do tenant.
DEFINE TABLE OVERWRITE topic SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON topic TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON topic TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE material   ON topic TYPE record<material>;
DEFINE FIELD OVERWRITE title      ON topic TYPE string;
DEFINE FIELD OVERWRITE created_at ON topic TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE topic_tenant_user ON topic FIELDS tenant_id, user_id;
-- 1 material = 1 tema: dois temas sobre o mesmo livro seriam dois planos concorrentes.
DEFINE INDEX OVERWRITE topic_material ON topic FIELDS material UNIQUE;

-- ── Plano do tema: cadência + data-alvo derivada ────────────────────────────
-- `target_date` é recalculada enquanto `proposed` e CONGELADA na ativação (ADR-0043).
DEFINE TABLE OVERWRITE study_plan SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id    ON study_plan TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id      ON study_plan TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE topic        ON study_plan TYPE record<topic>;
DEFINE FIELD OVERWRITE status       ON study_plan TYPE string
  ASSERT $value IN ['proposed', 'active', 'paused', 'completed'];
DEFINE FIELD OVERWRITE weekdays     ON study_plan TYPE array<string>;
DEFINE FIELD OVERWRITE target_date  ON study_plan TYPE option<datetime>;
DEFINE FIELD OVERWRITE activated_at ON study_plan TYPE option<datetime>;
DEFINE FIELD OVERWRITE created_at   ON study_plan TYPE datetime DEFAULT time::now() READONLY;

-- 1 tema = 1 plano: repropor SUBSTITUI o plano proposto, nunca acumula.
DEFINE INDEX OVERWRITE study_plan_topic ON study_plan FIELDS topic UNIQUE;

-- ── Lições planejadas ───────────────────────────────────────────────────────
-- tenant_id/user_id repetidos na lição (e não só no plano) para que a leitura corte
-- por usuário sem join — o mesmo motivo de `material_chapter`.
DEFINE TABLE OVERWRITE plan_entry SCHEMAFULL;
DEFINE FIELD OVERWRITE study_plan ON plan_entry TYPE record<study_plan>;
DEFINE FIELD OVERWRITE tenant_id  ON plan_entry TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON plan_entry TYPE record<user> ASSERT $value != NONE;
-- Sem ASSERT de positividade: remover/mover re-sequenciam passando por `seq` negativo
-- temporário para não esbarrar no UNIQUE abaixo (ver `kubo/store/study.py`).
DEFINE FIELD OVERWRITE seq        ON plan_entry TYPE int;
DEFINE FIELD OVERWRITE title      ON plan_entry TYPE string;
DEFINE FIELD OVERWRITE chapters   ON plan_entry TYPE array<record<material_chapter>>;
DEFINE FIELD OVERWRITE created_at ON plan_entry TYPE datetime DEFAULT time::now() READONLY;

-- Posição é identidade dentro do plano: duas lições não ocupam o mesmo lugar.
DEFINE INDEX OVERWRITE plan_entry_seq ON plan_entry FIELDS study_plan, seq UNIQUE;
