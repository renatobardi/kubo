-- Migration 0019 — tenant_id nos domínios centrais (KUBO-117).
-- Aditiva: campos nullable por ora; a contract (ASSERT != NONE) vem depois do
-- backfill, em migração posterior. DDL idempotente via OVERWRITE.

-- Tabelas de execução (kubo/store/flows.py)
DEFINE FIELD OVERWRITE tenant_id ON flow       TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON task       TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON deliverable TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON persona    TYPE option<record<tenant>>;

-- Tabelas de conhecimento (kubo/store/knowledge.py)
DEFINE FIELD OVERWRITE tenant_id ON distilled TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON entity    TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON chunk     TYPE option<record<tenant>>;

-- Unicidade de entity passa a ser por (tenant_id, normalized), não global.
DEFINE INDEX OVERWRITE entity_normalized ON entity FIELDS tenant_id, normalized UNIQUE;

-- Arestas que ligam registros tenant-scoped
DEFINE FIELD OVERWRITE tenant_id ON belongs_to  TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON assigned_to TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON produces    TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON consults    TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON derived_from TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON mentions    TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON chunk_of    TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON produced_by TYPE option<record<tenant>>;
DEFINE FIELD OVERWRITE tenant_id ON relates_to  TYPE option<record<tenant>>;
