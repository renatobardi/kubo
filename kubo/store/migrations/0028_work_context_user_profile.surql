-- Migration 0028 — Move work_context de `user` para `user_profile` (KUBO-152, ADR-0046).
-- DDL idempotente via OVERWRITE. Dados antigos em `user` são descartados, não migrados.

-- Limpa o campo em `user` antes de removê-lo; `REMOVE FIELD` não apaga dado sozinho.
UPDATE user SET work_context = NONE;

-- Remove o campo de `user`.
REMOVE FIELD work_context ON user;

-- Adiciona `work_context` a `user_profile` (texto livre, opcional, máx 4000 caracteres
-- validado na aplicação). `user_profile` já é SCHEMAFULL, então a declaração é necessária.
DEFINE FIELD OVERWRITE work_context ON user_profile TYPE option<string>;
