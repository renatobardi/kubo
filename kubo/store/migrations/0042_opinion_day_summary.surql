-- Migration 0042 — parecer por (item, tenant) e resumo do dia por (dia, tenant)
-- (KUBO-195, ADR-0052).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- Parecer: aresta opinion_for (item → tenant), espelha scored_for (ADR-0051).
-- Last-wins: DELETE + RELATE na escrita (como apply_score). Sem noção de dia.
DEFINE TABLE OVERWRITE opinion_for TYPE RELATION IN item OUT tenant ENFORCED SCHEMAFULL;
DEFINE FIELD OVERWRITE opinion ON opinion_for TYPE string ASSERT string::len($value) > 0 AND string::len($value) <= 500;
DEFINE FIELD OVERWRITE assigned_at ON opinion_for TYPE datetime DEFAULT time::now() READONLY;

-- Resumo do dia: tabela por (dia, tenant). Único registro por par — upsert
-- sobrescreve (fallback race-safe, ADR-0052 §III). `day` é o dia de calendário
-- no fuso do tenant, gravado como datetime na meia-noite UTC (SurrealDB não tem
-- tipo date) — a normalização mora em `upsert_day_summary`.
DEFINE TABLE OVERWRITE day_summary SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id ON day_summary TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE day ON day_summary TYPE datetime ASSERT $value != NONE;
DEFINE FIELD OVERWRITE summary ON day_summary TYPE string ASSERT string::len($value) > 0 AND string::len($value) <= 1000;
DEFINE FIELD OVERWRITE publication_count ON day_summary TYPE int ASSERT $value >= 0;
DEFINE FIELD OVERWRITE created_at ON day_summary TYPE datetime DEFAULT time::now() READONLY;
DEFINE INDEX OVERWRITE day_summary_tenant_day ON day_summary COLUMNS tenant_id, day UNIQUE;
