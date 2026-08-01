-- Migration 0031 — Conversa persistida + campos estruturados do Tema (KUBO-163, ADR-0047 §6).
-- O chat do mentor (Fase 1, draft) é persistido em `study_chat` para que reabrir o Tema
-- continue a conversa. Campos `focus` e `depth` no Topic são inferidos pelo mentor.
-- DDL append-only (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Topic: campos estruturados opcionais (focus, depth) ──────────────────────
DEFINE FIELD OVERWRITE focus ON topic TYPE option<string>;
DEFINE FIELD OVERWRITE depth ON topic TYPE option<string>
  ASSERT $value = NONE OR $value IN ['superficial', 'intermediario', 'aprofundado'];

-- ── study_chat: mensagens da conversa com mentor/planner ─────────────────────
DEFINE TABLE OVERWRITE study_chat SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id  ON study_chat TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id    ON study_chat TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE topic      ON study_chat TYPE record<topic> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE phase      ON study_chat TYPE string
  ASSERT $value IN ['draft', 'planning'];
DEFINE FIELD OVERWRITE role       ON study_chat TYPE string
  ASSERT $value IN ['user', 'assistant'];
DEFINE FIELD OVERWRITE content    ON study_chat TYPE string;
DEFINE FIELD OVERWRITE created_at ON study_chat TYPE datetime DEFAULT time::now() READONLY;

DEFINE INDEX OVERWRITE study_chat_topic_phase ON study_chat FIELDS topic, phase;
DEFINE INDEX OVERWRITE study_chat_tenant_user ON study_chat FIELDS tenant_id, user_id;
