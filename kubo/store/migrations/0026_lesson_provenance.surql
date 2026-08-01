-- Migration 0026 — Proveniência do conceito na lição (KUBO-133, story 9).
-- Registra os capítulos do material que originaram cada lição, preenchido pelo job
-- da véspera a partir da entrada do plano.
DEFINE FIELD OVERWRITE provenance ON lesson TYPE array<record<material_chapter>> DEFAULT [];
