-- Migration 0037 — plan_entry.chapters → plan_entry.sections (KUBO-185, ADR-0048).
-- Destrutivo: recomeço limpo (ADR-0048 §7). Nenhum plan_entry em `running` existe
-- para preservar. O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente.

-- Limpa dependentes do plano antigo (ordem: folhas → raiz).
DELETE FROM study_log;
DELETE FROM lesson;
DELETE FROM plan_entry;
DELETE FROM study_plan;

-- Reseta topic.state para draft: temas em planning/scheduled/running ficam sem
-- plano após o wipe acima; recomeço limpo exige estado consistente.
UPDATE topic SET state = 'draft';

-- Backfill: materiais pré-0036 não têm material_section. Cria 1 seção por
-- capítulo existente (content = chapter.content, title = chapter.title,
-- summary = chapter.title) para que o planner tenha seções ao agrupar.
-- Materiais pós-0036 já têm seções e não são afetados (WHERE NOT IN).
-- Guard IF: FOR loop do SurrealDB v3 falha com subquery vazia (retorna NONE).
IF (count((SELECT * FROM material_chapter)) > 0) {
    FOR $chapter IN (SELECT * FROM material_chapter WHERE id NOT IN (SELECT VALUE material_chapter FROM material_section)) {
        CREATE material_section SET
            material_chapter = $chapter.id,
            material = $chapter.material,
            tenant_id = $chapter.tenant_id,
            user_id = $chapter.user_id,
            seq = 1,
            title = $chapter.title,
            anchor_text = '',
            content = $chapter.content,
            summary = $chapter.title
        ;
    };
};

-- Remove o campo `chapters` e adiciona `sections: array<record<material_section>>`.
DEFINE FIELD OVERWRITE sections ON plan_entry TYPE array<record<material_section>>;
REMOVE FIELD IF EXISTS chapters ON plan_entry;
