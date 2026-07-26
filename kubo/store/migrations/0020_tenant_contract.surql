-- Migration 0020 — contrato de tenancy obrigatória (KUBO-123).
-- Torna tenant_id REQUIRED nas tabelas/arestas já migradas e cria as tabelas
-- pendentes da spec (memory, board_state, git_repo) com tenant_id obrigatório.
-- DDL idempotente via OVERWRITE.

-- ── Contrato: tenant_id obrigatório nos domínios centrais ────────────────────
DEFINE FIELD OVERWRITE tenant_id ON flow       TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON task       TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON deliverable TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON persona    TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON distilled  TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON entity     TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON chunk      TYPE record<tenant> ASSERT $value != NONE;

-- Arestas que ligam registros tenant-scoped
DEFINE FIELD OVERWRITE tenant_id ON belongs_to  TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON assigned_to TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON produces    TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON consults    TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON derived_from TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON mentions    TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON chunk_of    TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON produced_by TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE tenant_id ON relates_to  TYPE record<tenant> ASSERT $value != NONE;

-- ── Tabelas pendentes da spec §2.3 ───────────────────────────────────────────
-- Schema mínimo; campos específicos (embedding, states, etc.) entram quando
-- as funcionalidades forem implementadas (KUBO-123 cobre o contrato de tenant).

DEFINE TABLE OVERWRITE memory SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON memory TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE category   ON memory TYPE string ASSERT $value IN ['episodic', 'semantic', 'procedural', 'working'];
DEFINE FIELD OVERWRITE content    ON memory TYPE string;
DEFINE FIELD OVERWRITE embedding  ON memory TYPE option<array<number>>;
DEFINE FIELD OVERWRITE created_at ON memory TYPE datetime DEFAULT time::now() READONLY;

DEFINE TABLE OVERWRITE board_state SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON board_state TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE name       ON board_state TYPE string;
DEFINE FIELD OVERWRITE created_at ON board_state TYPE datetime DEFAULT time::now() READONLY;

DEFINE TABLE OVERWRITE git_repo SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON git_repo TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE owner      ON git_repo TYPE string;
DEFINE FIELD OVERWRITE name       ON git_repo TYPE string;
DEFINE FIELD OVERWRITE url        ON git_repo TYPE string;
DEFINE FIELD OVERWRITE created_at ON git_repo TYPE datetime DEFAULT time::now() READONLY;

-- Arestas pendentes da spec (ainda não usadas, mas com tenant_id contratado)
DEFINE TABLE OVERWRITE in_state TYPE RELATION IN task OUT board_state ENFORCED SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id ON in_state TYPE record<tenant> ASSERT $value != NONE;

DEFINE TABLE OVERWRITE has_repo TYPE RELATION IN flow OUT git_repo ENFORCED SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id ON has_repo TYPE record<tenant> ASSERT $value != NONE;
