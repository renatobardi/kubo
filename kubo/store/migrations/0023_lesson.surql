-- Migration 0023 — Lição do dia e Registro de estudo (ADR-0043, KUBO-137).
-- O runner envolve em BEGIN;...;COMMIT; (ADR-0007); DDL idempotente via OVERWRITE.

-- ── Lição: o que o job da véspera gera para um dia de estudo ────────────────
-- Dado PESSOAL como o resto de Estudos: escopo `user` DENTRO do tenant, repetido na
-- própria lição para que a leitura corte por usuário sem join.
DEFINE TABLE OVERWRITE lesson SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id     ON lesson TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id       ON lesson TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE study_plan    ON lesson TYPE record<study_plan>;
DEFINE FIELD OVERWRITE plan_entry    ON lesson TYPE record<plan_entry>;
-- Dia de estudo: meia-noite na tz do dono já convertida a UTC (o storage é UTC).
DEFINE FIELD OVERWRITE scheduled_for ON lesson TYPE datetime;
DEFINE FIELD OVERWRITE concept       ON lesson TYPE string;
DEFINE FIELD OVERWRITE scenario      ON lesson TYPE string;
DEFINE FIELD OVERWRITE application   ON lesson TYPE string;
DEFINE FIELD OVERWRITE recap         ON lesson TYPE option<string>;
-- SNAPSHOT do quiz gerado: a correção de uma lição antiga usa o quiz COMO ELE FOI
-- gerado, mesmo que a persona mude depois. FLEXIBLE pela mesma razão de `run.stats`:
-- forma variável de payload, validada na borda (pydantic `QuizItem`), não aqui.
DEFINE FIELD OVERWRITE quiz          ON lesson TYPE array<object>;
DEFINE FIELD OVERWRITE quiz.*        ON lesson TYPE object FLEXIBLE;
DEFINE FIELD OVERWRITE created_at    ON lesson TYPE datetime DEFAULT time::now() READONLY;

-- Idempotência do job da véspera: várias janelas de retry no mesmo dia não podem
-- render duas lições para o mesmo dia de estudo.
DEFINE INDEX OVERWRITE lesson_plan_day ON lesson FIELDS study_plan, scheduled_for UNIQUE;
DEFINE INDEX OVERWRITE lesson_tenant_user ON lesson FIELDS tenant_id, user_id;

-- ── Registro de estudo: o que o dono respondeu ──────────────────────────────
DEFINE TABLE OVERWRITE study_log SCHEMAFULL;
DEFINE FIELD OVERWRITE tenant_id     ON study_log TYPE record<tenant> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE user_id       ON study_log TYPE record<user> ASSERT $value != NONE;
DEFINE FIELD OVERWRITE lesson        ON study_log TYPE record<lesson>;
DEFINE FIELD OVERWRITE answers       ON study_log TYPE array<int>;
DEFINE FIELD OVERWRITE correct_count ON study_log TYPE int;
DEFINE FIELD OVERWRITE reaction      ON study_log TYPE option<string>
  ASSERT $value = NONE OR $value IN ['facil', 'ok', 'dificil'];
DEFINE FIELD OVERWRITE completed_at  ON study_log TYPE datetime DEFAULT time::now() READONLY;

-- Um registro POR LIÇÃO: o segundo envio não reescreve o desempenho que já alimentou
-- a recapitulação da lição seguinte.
DEFINE INDEX OVERWRITE study_log_lesson ON study_log FIELDS lesson UNIQUE;
DEFINE INDEX OVERWRITE study_log_tenant_user ON study_log FIELDS tenant_id, user_id;
