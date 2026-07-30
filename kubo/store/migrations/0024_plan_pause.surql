-- Migration 0024 — Pausa do plano de estudo (ADR-0043, KUBO-138).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Pausa: os dois campos que congelam a régua ──────────────────────────────
-- `paused_at` marca DESDE QUANDO o plano está parado (NONE enquanto ativo); o retomar
-- converte esse intervalo em dias de estudo e soma ao acumulador abaixo.
DEFINE FIELD OVERWRITE paused_at   ON study_plan TYPE option<datetime>;
-- ACUMULADOR de dias de estudo congelados por TODAS as pausas já retomadas. É o que
-- impede o atraso de crescer enquanto o dono não está estudando — sem ele, voltar de
-- duas semanas paradas chegaria com dez lições de atraso fabricadas. Cálculo é do
-- domínio (`kubo.study.progress`); o banco só guarda o total. DEFAULT 0: plano
-- anterior a esta migração nunca pausou.
DEFINE FIELD OVERWRITE paused_days ON study_plan TYPE int DEFAULT 0;

-- (Os estados 'paused' e 'completed' já estão no ASSERT de `status` na 0022.)
