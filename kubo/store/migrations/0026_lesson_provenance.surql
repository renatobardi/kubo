-- Migration 0026 — Proveniência do conceito na lição (KUBO-133, story 9).
-- SNAPSHOT da provenância gerada pelo tutor: para cada conceito destilado, o tutor
-- emite o `chapter_seq` (seq do capítulo no conteúdo que ele recebeu) e uma citação
-- curta do trecho que originou o conceito. FLEXIBLE pela mesma razão de `quiz`:
-- a provenância é congelada no momento da geração e não muda depois.
DEFINE FIELD OVERWRITE provenance     ON lesson TYPE array<object>;
DEFINE FIELD OVERWRITE provenance.*   ON lesson TYPE object FLEXIBLE;
