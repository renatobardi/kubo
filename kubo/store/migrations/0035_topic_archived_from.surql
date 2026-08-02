-- 0035 (KUBO-167): archived_from no topic — estado anterior ao arquivar.
-- Permite desarquivar retomando o estado pré-arquivo (draft/planning/scheduled/running).
DEFINE FIELD OVERWRITE archived_from ON topic TYPE option<string>
  ASSERT $value = NONE OR $value IN ['draft', 'planning', 'scheduled', 'running'];
