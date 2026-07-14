-- Migration 0006 — campos de decisão do gate na task (ADR-0018 §IV).
-- A task do GATE (segunda task, do Humano) registra a decisão humana no grafo:
-- `decision` (approved|rejected), `reason` (motivo obrigatório na rejeição) e o
-- carimbo `decided_at`. NENHUMA tabela nova — a decisão mora na task, não num
-- registro extra-spec (a contenção do ADR-0015 permanece; ADR-0018 §IV).
--
-- option<...>: a task é SCHEMAFULL e a esmagadora maioria (analista, não-gate) nunca
-- carrega decisão — um campo obrigatório violaria o schema das tasks existentes.
-- DDL idempotente via OVERWRITE; o runner envolve em BEGIN;...;COMMIT; (ADR-0007).
DEFINE FIELD OVERWRITE decision   ON task TYPE option<string>;
DEFINE FIELD OVERWRITE reason     ON task TYPE option<string>;
DEFINE FIELD OVERWRITE decided_at ON task TYPE option<datetime>;
