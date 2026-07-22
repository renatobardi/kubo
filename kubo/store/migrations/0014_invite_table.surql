-- Migration 0014 — tabela `invite` para onboarding de destino Telegram por convite
-- (ADR-0033, tickets KUBO-58/68/69/70).
-- Aditiva: cria tabela separada; schema de `destination` permanece inalterado.

DEFINE TABLE OVERWRITE invite SCHEMAFULL;
DEFINE FIELD OVERWRITE name        ON invite TYPE string;
DEFINE FIELD OVERWRITE email       ON invite TYPE option<string>;
DEFINE FIELD OVERWRITE token       ON invite TYPE string;
DEFINE FIELD OVERWRITE expires_at   ON invite TYPE datetime;
DEFINE FIELD OVERWRITE accepted_at  ON invite TYPE option<datetime>;
DEFINE FIELD OVERWRITE created_at   ON invite TYPE datetime DEFAULT time::now() READONLY;

-- Token é o identificador público do convite (no link t.me); UNIQUE evita colisão.
DEFINE INDEX OVERWRITE invite_token ON invite FIELDS token UNIQUE;
