-- Migration 0029 — Tema container de N Materiais + estados explícitos (ADR-0047, KUBO-161).
-- Recomeço limpo: wipe de dados de study existentes e redefinição do schema.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Wipe: dados de study do ADR-0043 são descartados (recomeço limpo) ─────────
-- Ordem: filhos antes de pais (FKs implícitas por record<...>).
DELETE FROM study_log;
DELETE FROM lesson;
DELETE FROM plan_entry;
DELETE FROM study_plan;
DELETE FROM material_chapter;
DELETE FROM material;
DELETE FROM topic;

-- ── Tema: container de N Materiais (reverte 1:1 do ADR-0043) ─────────────────
-- `material` deixa de ser obrigatório (Tema nasce vazio em `draft`).
-- `state` é o ciclo de vida explícito (ADR-0047 §3).
DEFINE TABLE OVERWRITE topic SCHEMAFULL;
-- Remove o campo `material` herdado do ADR-0043 (1:1) — o Tema não aponta mais
-- para um material; os Materiais é que apontam para o Tema (N:1).
REMOVE FIELD IF EXISTS material ON topic;
DEFINE FIELD OVERWRITE tenant_id  ON topic TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON topic TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE title      ON topic TYPE string;
DEFINE FIELD OVERWRITE state      ON topic TYPE string
  ASSERT $value IN ['draft', 'planning', 'scheduled', 'running', 'archived'];
DEFINE FIELD OVERWRITE created_at ON topic TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE topic_tenant_user ON topic FIELDS tenant_id, user_id;
-- Remove o índice UNIQUE topic_material do ADR-0043 (1:1) — N materiais por tema agora.
REMOVE INDEX IF EXISTS topic_material ON topic;

-- ── Material: exclusivo a um Tema (N:1) + sumário gerado no upload ───────────
-- `topic` e `summary` são option nesta fatia: o upload com topic_id vem no KUBO-162.
DEFINE TABLE OVERWRITE material SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id         ON material TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id           ON material TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE topic             ON material TYPE option<record<topic>>;
DEFINE FIELD OVERWRITE title             ON material TYPE string;
DEFINE FIELD OVERWRITE fmt               ON material TYPE string ASSERT $value IN ['epub', 'pdf'];
DEFINE FIELD OVERWRITE original_filename ON material TYPE string;
DEFINE FIELD OVERWRITE file_path         ON material TYPE string;
DEFINE FIELD OVERWRITE size_bytes        ON material TYPE int;
DEFINE FIELD OVERWRITE chapter_count     ON material TYPE int;
DEFINE FIELD OVERWRITE summary           ON material TYPE option<string>;
DEFINE FIELD OVERWRITE created_at        ON material TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE material_tenant_user ON material FIELDS tenant_id, user_id;

-- ── Capítulos: inalterados na estrutura (material_chapter aponta para material) ──
DEFINE TABLE OVERWRITE material_chapter SCHEMAFULL;
DEFINE FIELD OVERWRITE material   ON material_chapter TYPE record<material>;
DEFINE FIELD OVERWRITE tenant_id  ON material_chapter TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON material_chapter TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE seq        ON material_chapter TYPE int;
DEFINE FIELD OVERWRITE title      ON material_chapter TYPE string;
DEFINE FIELD OVERWRITE part       ON material_chapter TYPE option<string>;
DEFINE FIELD OVERWRITE content    ON material_chapter TYPE string;
DEFINE FIELD OVERWRITE created_at ON material_chapter TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE material_chapter_seq ON material_chapter FIELDS material, seq UNIQUE;

-- ── Plano, lições, registros: schema redefinido (OVERWRITE) sem mudanças ──────
-- O schema do study_plan/plan_entry/lesson/study_log é redefinido com OVERWRITE
-- para garantir consistência após o wipe, mas sem mudanças estruturais nesta fatia.
-- O estado do plano (proposed/active/paused/completed) continua no study_plan;
-- o estado do TEMA (draft/planning/scheduled/running/archived) é novo e separado.
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
DEFINE FIELD OVERWRITE paused_at    ON study_plan TYPE option<datetime>;
DEFINE FIELD OVERWRITE paused_days  ON study_plan TYPE int DEFAULT 0;

DEFINE INDEX OVERWRITE study_plan_topic ON study_plan FIELDS topic UNIQUE;

DEFINE TABLE OVERWRITE plan_entry SCHEMAFULL;
DEFINE FIELD OVERWRITE study_plan ON plan_entry TYPE record<study_plan>;
DEFINE FIELD OVERWRITE tenant_id  ON plan_entry TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON plan_entry TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE seq        ON plan_entry TYPE int;
DEFINE FIELD OVERWRITE title      ON plan_entry TYPE string;
DEFINE FIELD OVERWRITE chapters   ON plan_entry TYPE array<record<material_chapter>>;
DEFINE FIELD OVERWRITE created_at ON plan_entry TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE plan_entry_seq ON plan_entry FIELDS study_plan, seq UNIQUE;

DEFINE TABLE OVERWRITE lesson SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id     ON lesson TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id       ON lesson TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE study_plan    ON lesson TYPE record<study_plan>;
DEFINE FIELD OVERWRITE plan_entry    ON lesson TYPE record<plan_entry>;
DEFINE FIELD OVERWRITE scheduled_for ON lesson TYPE datetime;
DEFINE FIELD OVERWRITE concept       ON lesson TYPE string;
DEFINE FIELD OVERWRITE scenario      ON lesson TYPE string;
DEFINE FIELD OVERWRITE application   ON lesson TYPE string;
DEFINE FIELD OVERWRITE recap         ON lesson TYPE option<string>;
DEFINE FIELD OVERWRITE quiz          ON lesson TYPE array<object>;
DEFINE FIELD OVERWRITE quiz.*        ON lesson TYPE object FLEXIBLE;
DEFINE FIELD OVERWRITE provenance    ON lesson TYPE array<object>;
DEFINE FIELD OVERWRITE provenance.*  ON lesson TYPE object FLEXIBLE;
DEFINE FIELD OVERWRITE created_at    ON lesson TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE lesson_plan_day ON lesson FIELDS study_plan, scheduled_for UNIQUE;
DEFINE INDEX OVERWRITE lesson_tenant_user ON lesson FIELDS tenant_id, user_id;

DEFINE TABLE OVERWRITE study_log SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id     ON study_log TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id       ON study_log TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE lesson        ON study_log TYPE record<lesson>;
DEFINE FIELD OVERWRITE answers       ON study_log TYPE array<int>;
DEFINE FIELD OVERWRITE correct_count ON study_log TYPE int;
DEFINE FIELD OVERWRITE reaction      ON study_log TYPE option<string>
  ASSERT $value = NONE OR $value IN ['facil', 'ok', 'dificil'];
DEFINE FIELD OVERWRITE completed_at  ON study_log TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE study_log_lesson ON study_log FIELDS lesson UNIQUE;
DEFINE INDEX OVERWRITE study_log_tenant_user ON study_log FIELDS tenant_id, user_id;
