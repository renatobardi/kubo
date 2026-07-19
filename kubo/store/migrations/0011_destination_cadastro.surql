-- Migration 0011 — `destination` vira Cadastro (ADR-0027, ticket KUBO-43).
-- Aditiva: cria a tabela e a unicidade composta, SEM alterar `dispatch`.
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente (OVERWRITE).

DEFINE TABLE OVERWRITE destination SCHEMAFULL;
DEFINE FIELD OVERWRITE name        ON destination TYPE string;
DEFINE FIELD OVERWRITE kind        ON destination TYPE string;
DEFINE FIELD OVERWRITE channel     ON destination TYPE string;
DEFINE FIELD OVERWRITE address     ON destination TYPE string;
DEFINE FIELD OVERWRITE enabled     ON destination TYPE bool DEFAULT true;
DEFINE FIELD OVERWRITE archived_at ON destination TYPE option<datetime>;
DEFINE FIELD OVERWRITE created_at  ON destination TYPE datetime DEFAULT time::now() READONLY;

-- Unicidade por (channel, address): mesmo endereço duas vezes no mesmo canal é rejeitado.
-- A mesma pessoa em canais diferentes são linhas legítimas.
DEFINE INDEX OVERWRITE destination_channel_address ON destination FIELDS channel, address UNIQUE;
