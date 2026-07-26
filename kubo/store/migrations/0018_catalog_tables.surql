-- Migration 0018 — catálogo por-tenant + changelog de auditoria (ADR-0042, KUBO-119).
-- Aditiva: cria catalog_persona, catalog_integration, catalog_flow_template e catalog_changelog.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── catalog_persona (tenant-scoped, nome estável) ────────────────────────────
DEFINE TABLE OVERWRITE catalog_persona SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON catalog_persona TYPE record<tenant>;
DEFINE FIELD OVERWRITE name       ON catalog_persona TYPE string;
DEFINE FIELD OVERWRITE executor   ON catalog_persona TYPE string;
DEFINE FIELD OVERWRITE model      ON catalog_persona TYPE option<string>;
DEFINE FIELD OVERWRITE prompt     ON catalog_persona TYPE string DEFAULT "";
DEFINE FIELD OVERWRITE permissions ON catalog_persona TYPE array<string> DEFAULT [];
DEFINE FIELD OVERWRITE created_at ON catalog_persona TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE updated_at ON catalog_persona TYPE datetime DEFAULT time::now();

-- Identidade do catálogo: (tenant_id, name) único por tabela.
DEFINE INDEX OVERWRITE catalog_persona_tenant_name
    ON catalog_persona FIELDS tenant_id, name UNIQUE;

-- ── catalog_integration (tenant-scoped, nome estável) ────────────────────────
DEFINE TABLE OVERWRITE catalog_integration SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON catalog_integration TYPE record<tenant>;
DEFINE FIELD OVERWRITE name       ON catalog_integration TYPE string;
DEFINE FIELD OVERWRITE kind       ON catalog_integration TYPE string;
DEFINE FIELD OVERWRITE auth       ON catalog_integration TYPE object FLEXIBLE;
DEFINE FIELD OVERWRITE rate_limit ON catalog_integration TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE base_url   ON catalog_integration TYPE option<string>;
DEFINE FIELD OVERWRITE created_at ON catalog_integration TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE updated_at ON catalog_integration TYPE datetime DEFAULT time::now();

DEFINE INDEX OVERWRITE catalog_integration_tenant_name
    ON catalog_integration FIELDS tenant_id, name UNIQUE;

-- ── catalog_flow_template (tenant-scoped, nome estável) ──────────────────────
DEFINE TABLE OVERWRITE catalog_flow_template SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id    ON catalog_flow_template TYPE record<tenant>;
DEFINE FIELD OVERWRITE name         ON catalog_flow_template TYPE string;
DEFINE FIELD OVERWRITE version      ON catalog_flow_template TYPE int;
DEFINE FIELD OVERWRITE board        ON catalog_flow_template TYPE object FLEXIBLE;
DEFINE FIELD OVERWRITE cast         ON catalog_flow_template TYPE array<string>;
DEFINE FIELD OVERWRITE deliverable  ON catalog_flow_template TYPE string;
DEFINE FIELD OVERWRITE triggers     ON catalog_flow_template TYPE array<string>;
DEFINE FIELD OVERWRITE budget_usd   ON catalog_flow_template TYPE option<number>;
DEFINE FIELD OVERWRITE created_at   ON catalog_flow_template TYPE datetime DEFAULT time::now() READONLY;
DEFINE FIELD OVERWRITE updated_at   ON catalog_flow_template TYPE datetime DEFAULT time::now();

DEFINE INDEX OVERWRITE catalog_flow_template_tenant_name
    ON catalog_flow_template FIELDS tenant_id, name UNIQUE;

-- ── catalog_changelog (auditoria genérica dos 3 tipos) ────────────────────────
DEFINE TABLE OVERWRITE catalog_changelog SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON catalog_changelog TYPE record<tenant>;
DEFINE FIELD OVERWRITE kind       ON catalog_changelog TYPE string ASSERT $value IN ['persona', 'integration', 'flow_template'];
DEFINE FIELD OVERWRITE item_name  ON catalog_changelog TYPE string;
DEFINE FIELD OVERWRITE before     ON catalog_changelog TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE after      ON catalog_changelog TYPE option<object> FLEXIBLE;
DEFINE FIELD OVERWRITE changed_by ON catalog_changelog TYPE record<user>;
DEFINE FIELD OVERWRITE changed_at ON catalog_changelog TYPE datetime DEFAULT time::now() READONLY;
