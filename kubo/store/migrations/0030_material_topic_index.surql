-- Migration 0030 — Índice material_topic (KUBO-162).
-- O índice `material_topic` acelera `list_materials_by_topic` e `count_materials_by_topic`,
-- que filtram por `topic = $topic`. O campo `topic` foi definido como `option<record<topic>>`
-- na migration 0029; aqui só adicionamos o índice (DDL append-only, ADR-0007).
DEFINE INDEX OVERWRITE material_topic ON material FIELDS topic;
