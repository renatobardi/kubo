-- Migration 0015 — schema de tenancy (tenant/user/membership, ADR-0039, KUBO-113).
-- Aditiva: cria as três entidades de identidade/autorização; não altera tabelas existentes.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── tenant (equipe/workspace) ───────────────────────────────────────────────
DEFINE TABLE OVERWRITE tenant SCHEMAFULL;
DEFINE FIELD OVERWRITE name       ON tenant TYPE string;
DEFINE FIELD OVERWRITE created_at ON tenant TYPE datetime DEFAULT time::now() READONLY;

-- ── user (identidade humana, distinta de persona) ────────────────────────────
DEFINE TABLE OVERWRITE user SCHEMAFULL;
DEFINE FIELD OVERWRITE firebase_uid ON user TYPE string;
DEFINE FIELD OVERWRITE email        ON user TYPE option<string>;
DEFINE FIELD OVERWRITE created_at   ON user TYPE datetime DEFAULT time::now() READONLY;
-- Firebase UID é identidade estável externa; UNIQUE evita duplicar user por login.
DEFINE INDEX OVERWRITE user_firebase_uid ON user FIELDS firebase_uid UNIQUE;

-- ── membership (relação N:N user -> tenant, com papel) ───────────────────────
DEFINE TABLE OVERWRITE membership TYPE RELATION IN user OUT tenant ENFORCED SCHEMAFULL;
-- `role` é "owner" ou "member". Exatamente 1 owner por tenant é verificado em código
-- (ADR-0039 §I), não por índice parcial — suporte não confirmado na v3.1.5 pinada.
DEFINE FIELD OVERWRITE role ON membership TYPE string ASSERT $value IN ['owner', 'member'];
DEFINE FIELD OVERWRITE created_at ON membership TYPE datetime DEFAULT time::now() READONLY;
