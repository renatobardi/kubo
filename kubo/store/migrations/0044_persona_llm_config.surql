-- Migration 0044 — LLM call parameters on catalog_persona (ADR-0054 §II, KUBO-221).
-- Additive: adds call-parameter fields to catalog_persona.

-- LLM call parameters, with defaults that preserve previous behavior for
-- personas that have not been edited yet.
DEFINE FIELD OVERWRITE max_tokens       ON catalog_persona TYPE int    DEFAULT 1024;
DEFINE FIELD OVERWRITE temperature      ON catalog_persona TYPE float  DEFAULT 0.0;
DEFINE FIELD OVERWRITE reasoning_effort ON catalog_persona TYPE option<string>;
DEFINE FIELD OVERWRITE timeout          ON catalog_persona TYPE float  DEFAULT 60.0;
DEFINE FIELD OVERWRITE max_turns        ON catalog_persona TYPE option<int>;

-- Backfill: existing rows without the new fields get the same defaults.
-- SurrealDB DEFAULT only applies on CREATE, so we UPDATE existing rows
-- explicitly (same pattern as migration 0038).
UPDATE catalog_persona SET
    max_tokens  = 1024,
    temperature = 0.0,
    timeout     = 60.0
  WHERE max_tokens IS NONE;
