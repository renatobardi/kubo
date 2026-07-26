-- Migration 0016 — credenciais de provedor cifradas por tenant (ADR-0039 §IV, KUBO-115).
-- Aditiva: cria a tabela de tenant_credential; não altera tabelas existentes.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── tenant_credential (segredo cifrado de provedor por tenant) ───────────────
DEFINE TABLE OVERWRITE tenant_credential SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON tenant_credential TYPE record<tenant>;
DEFINE FIELD OVERWRITE provider   ON tenant_credential TYPE string;
DEFINE FIELD OVERWRITE value      ON tenant_credential TYPE string;
DEFINE FIELD OVERWRITE created_at ON tenant_credential TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE updated_at ON tenant_credential TYPE datetime DEFAULT time::now();

-- Um tenant só pode ter uma credencial por provider.
DEFINE INDEX OVERWRITE tenant_credential_tenant_provider
    ON tenant_credential FIELDS tenant_id, provider UNIQUE;
