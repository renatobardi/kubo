-- Migration 0010 — GitHub vira Cadastro: source `github-releases` → `github-repo` (#110, ADR-0025 §5).
-- Data-migration (não schema): reconcilia os `source` de GitHub já coletados (kind='github-releases',
-- nascidos como efeito colateral do worker) com o modelo Cadastro (kind='github-repo', dirigido pela
-- UI/sweep). O runner envolve em BEGIN;...;COMMIT; (ADR-0007). Idempotente: WHERE kind='github-releases'
-- esvazia após a 1ª execução, então re-rodar a cada deploy é no-op seguro (mesmo padrão do backfill 0009).
--
-- SOBREVIVENTE = o record 'github-releases': é ele que carrega as arestas from_source/collected_by
-- (foi coletado de verdade). O twin 'github-repo' (o dono pode ter cadastrado o MESMO repo pela UI
-- #105, kind diferente = record diferente sob UNIQUE(kind, canonical)) é EDGE-LESS por construção —
-- nunca foi coletado, então nada aponta pra ele. Reconciliar mantendo o github-releases evita QUALQUER
-- manipulação de aresta: o id do sobrevivente não muda, toda proveniência fica intacta. `kind` NÃO é
-- READONLY no schema (só a política de app de `edit_source` o trava) — a migração pode flipá-lo.

-- ── (1) Onde há twin github-repo (mesma canonical), transferir o ESTADO do Cadastro do twin pro
-- sobrevivente ANTES de apagá-lo: title/tags e — crucial — enabled/archived_at. Se o dono pausou ou
-- arquivou o twin pela UI, essa intenção não pode se perder no flip. Os dois campos de estado JUNTOS
-- (o invariante `archived_at IS NOT NONE ⟹ enabled=false` é do twin e transfere coerente de uma vez).
-- `LET` primeiro, FOR sobre a variável: `FOR $x IN (SELECT ...)` direto quebra no v3.1.5 (uma
-- subquery vazia vira NONE, não `[]`, e o FOR levanta "Cannot execute statement using value: NONE").
-- Ligar a SELECT a `$twins` dá um array de verdade — itera limpo, inclusive vazio (env sem twin).
LET $twins = (SELECT canonical, title, tags, enabled, archived_at FROM source WHERE kind = 'github-repo');
FOR $twin IN $twins {
    UPDATE source
        SET title = $twin.title, tags = $twin.tags, enabled = $twin.enabled, archived_at = $twin.archived_at
        WHERE kind = 'github-releases' AND canonical = $twin.canonical;
};

-- ── (2) Apagar os twins github-repo que TÊM um sobrevivente github-releases (o estado deles já foi
-- transferido em (1)). Cadastros github-repo SEM twin de coleta — um repo que o dono cadastrou pela UI
-- e o worker ainda não coletou — NÃO são apagados: a condição `canonical IN (...)` os poupa. Sem isso,
-- um `DELETE ... WHERE kind='github-repo'` cru apagaria Cadastros legítimos.
DELETE source
    WHERE kind = 'github-repo'
    AND canonical IN (SELECT VALUE canonical FROM source WHERE kind = 'github-releases');

-- ── (3) Flipar o kind do sobrevivente: github-releases → github-repo. Os twins colidentes já saíram
-- em (2), então o UNIQUE(kind, canonical) não barra. `created_at` (READONLY) fica intacto — o piso
-- `since` do sweep (#110/D2 = created_at do Cadastro) herda o valor antigo (~o `since` congelado do
-- pipeline), sem re-backfill na 1ª varredura (o upsert_item por external_id já dedupa o que voltar).
UPDATE source SET kind = 'github-repo' WHERE kind = 'github-releases';
