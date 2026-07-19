-- Migration 0012 — singleton `settings` (ADR-0028 + ADR-0030 §1, ticket KUBO-44).
-- Aditiva: cria a tabela e os campos, NÃO altera `dispatch` nem `destination`.
-- O seed é quem cria a linha `settings:global` (dado, não schema).

DEFINE TABLE OVERWRITE settings SCHEMAFULL;
DEFINE FIELD OVERWRITE digest_cron         ON settings TYPE string  ASSERT $value != NONE;
DEFINE FIELD OVERWRITE distribution_paused ON settings TYPE bool    DEFAULT false;
DEFINE FIELD OVERWRITE default_destination ON settings TYPE option<record<destination>>;
