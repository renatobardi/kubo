-- Migration 0027 — Módulo de perfil de usuário (KUBO-147).
-- Cria tabela `user_profile` (1:1 com `user`, sem tenant_id) e adiciona `theme` ao `membership`.
-- DDL idempotente via OVERWRITE.

-- ── Perfil do usuário ─────────────────────────────────────────────────────────
DEFINE TABLE OVERWRITE user_profile SCHEMAFULL;

DEFINE FIELD OVERWRITE user ON user_profile TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE display_name ON user_profile TYPE string
  ASSERT string::len($value) > 0 AND string::len($value) <= 64;
DEFINE FIELD OVERWRITE language ON user_profile TYPE string ASSERT $value != NONE;
DEFINE FIELD OVERWRITE timezone ON user_profile TYPE string ASSERT $value != NONE;
DEFINE FIELD OVERWRITE created_at ON user_profile TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE updated_at ON user_profile TYPE datetime DEFAULT time::now();

-- Garantia 1:1 por usuário.
DEFINE INDEX OVERWRITE user_profile_user ON user_profile FIELDS user UNIQUE;

-- ── Aparência por workspace (membership = relação user -> tenant) ───────────────
DEFINE FIELD OVERWRITE theme ON membership TYPE string DEFAULT 'system'
  ASSERT $value IN ['light', 'dark', 'system'];

-- Backfill: memberships criadas antes desta migração não têm `theme` preenchido.
-- DEFAULT só aplica em CREATE, então atualizamos as existentes explicitamente.
UPDATE membership SET theme = 'system' WHERE theme IS NONE;
