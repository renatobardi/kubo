-- Migration 0021 — fundação do módulo Estudos (ADR-0043, KUBO-135).
-- Contexto de trabalho no `user` + Material e seus capítulos.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Perfil: contexto de trabalho do usuário ─────────────────────────────────
-- Texto livre que entra em prompts de persona; opcional (o dono pode se retratar).
DEFINE FIELD OVERWRITE work_context ON user TYPE option<string>;

-- ── Material de estudo (epub/PDF) ───────────────────────────────────────────
-- Dado PESSOAL: escopo `user` DENTRO do tenant — as leituras filtram pelos dois.
DEFINE TABLE OVERWRITE material SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id         ON material TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id           ON material TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE title             ON material TYPE string;
DEFINE FIELD OVERWRITE fmt               ON material TYPE string ASSERT $value IN ['epub', 'pdf'];
DEFINE FIELD OVERWRITE original_filename ON material TYPE string;
DEFINE FIELD OVERWRITE file_path         ON material TYPE string;
DEFINE FIELD OVERWRITE size_bytes        ON material TYPE int;
DEFINE FIELD OVERWRITE chapter_count     ON material TYPE int;
DEFINE FIELD OVERWRITE created_at        ON material TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE material_tenant_user ON material FIELDS tenant_id, user_id;

-- ── Capítulos, na ordem de leitura ──────────────────────────────────────────
-- tenant_id/user_id repetidos no capítulo (e não só no material) para que a leitura
-- corte por usuário sem join — o mesmo motivo do índice acima.
DEFINE TABLE OVERWRITE material_chapter SCHEMAFULL;
DEFINE FIELD OVERWRITE material   ON material_chapter TYPE record<material>;
DEFINE FIELD OVERWRITE tenant_id  ON material_chapter TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON material_chapter TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE seq        ON material_chapter TYPE int;
DEFINE FIELD OVERWRITE title      ON material_chapter TYPE string;
DEFINE FIELD OVERWRITE part       ON material_chapter TYPE option<string>;
DEFINE FIELD OVERWRITE content    ON material_chapter TYPE string;
DEFINE FIELD OVERWRITE created_at ON material_chapter TYPE datetime DEFAULT time::now() READONLY;

-- Ordem de leitura é identidade: um material não tem dois capítulos no mesmo `seq`.
DEFINE INDEX OVERWRITE material_chapter_seq ON material_chapter FIELDS material, seq UNIQUE;
