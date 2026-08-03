-- Migration 0036 — Section: partição tópica de capítulo (KUBO-184, ADR-0048).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Seções de capítulo ───────────────────────────────────────────────────────
-- Particionamento tópico do conteúdo do capítulo, produzido pela persona
-- `sectionizer` no upload. Aditivo: plan_entry continua referenciando
-- material_chapter; material_section é persistida para consumo futuro.
DEFINE TABLE OVERWRITE material_section SCHEMAFULL;
DEFINE FIELD OVERWRITE material_chapter ON material_section TYPE record<material_chapter>;
DEFINE FIELD OVERWRITE material        ON material_section TYPE record<material>;
DEFINE FIELD OVERWRITE tenant_id       ON material_section TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id         ON material_section TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE seq             ON material_section TYPE int;
DEFINE FIELD OVERWRITE title           ON material_section TYPE string;
DEFINE FIELD OVERWRITE anchor_text     ON material_section TYPE string;
DEFINE FIELD OVERWRITE content         ON material_section TYPE string;
DEFINE FIELD OVERWRITE summary         ON material_section TYPE string;
DEFINE FIELD OVERWRITE created_at      ON material_section TYPE datetime DEFAULT time::now() READONLY;

-- seq é local ao capítulo: (material_chapter, seq) é único.
DEFINE INDEX OVERWRITE material_section_chapter ON material_section FIELDS material_chapter, seq UNIQUE;
-- Filtro por material (tenant + user já filtram; índice acelera listagem por material).
DEFINE INDEX OVERWRITE material_section_material ON material_section FIELDS material, tenant_id, user_id;
