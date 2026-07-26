-- Migration 0017 — convite de equipe (team_invite, ADR-0041, KUBO-120).
-- Tabela distinta do `invite` de onboarding Telegram (ADR-0033).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

DEFINE TABLE OVERWRITE team_invite SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON team_invite TYPE record<tenant>;
DEFINE FIELD OVERWRITE token      ON team_invite TYPE string;
DEFINE FIELD OVERWRITE role       ON team_invite TYPE string ASSERT $value IN ['member'];
DEFINE FIELD OVERWRITE created_by ON team_invite TYPE record<user>;
DEFINE FIELD OVERWRITE expires_at ON team_invite TYPE datetime;
DEFINE FIELD OVERWRITE status     ON team_invite TYPE string ASSERT $value IN ['pending', 'accepted', 'expired', 'revoked'];
DEFINE FIELD OVERWRITE created_at ON team_invite TYPE datetime DEFAULT time::now() READONLY;

-- Token é roteador do convite: UNIQUE e indexado.
DEFINE INDEX OVERWRITE team_invite_token ON team_invite FIELDS token UNIQUE;
