-- 0034: study_plan.status += 'running' (KUBO-166)
--
-- O scheduler transiciona scheduled→running e congela o plano (status='running').
-- Isso permite que deactivate_plan faça CAS em status='active' (só reverte se o
-- plano ainda não foi congelado). Sem 'running' no enum, o UPDATE falha.

DEFINE FIELD OVERWRITE status ON study_plan TYPE string
  ASSERT $value IN ['proposed', 'active', 'running', 'paused', 'completed'];
