# ADR-0012 — Import do legado NeonDB (script one-off via store)

> Status: **aceito** · Data: 2026-07-06

## Contexto

O grafo do Kubo (SurrealDB) precisa herdar o acervo do sistema legado "RARA", que
vive num NeonDB (Postgres): ~6k itens, ~3k transcrições, ~1.250 destilados, mais
cadastros (feed_sources, target_channels, podcast_feeds, email_sources) e conteúdo
(items, news_items, channel_videos, podcast_episodes, emails).

FATO CRÍTICO externo: o dono fará um pg_dump completo do Neon e vai DESATIVÁ-LO
após a migração (motivo externo ao projeto). O import é a última chance com o Neon
vivo; a reconciliação completa é a condição de desligamento.

A camada store (kubo/store/knowledge.py) já existe e é a única porta ao banco
(invariante 2). Precedente de script one-off fora de kubo/: scripts/embedding_smoke.py.

## Decisão

### I. Import é script one-off, não worker sob contrato (D19 emendada)

O import roda como scripts/neon_import.py, NÃO como worker sob o contrato do
ADR-0009. Racional YAGNI: o mecanismo de worker existe para RECORRÊNCIA; este
import roda uma vez. As garantias que importam já vêm da store: idempotência por
record ID determinístico (sha256 da chave natural), proveniência via start_run/
finish_run + aresta collected_by/produced_by, e o invariante "acesso só via store"
respeitado.

Perda consciente e aceitável: o escopo de contexto (ctx) que o contrato de worker
daria — aceitável porque é código do próprio dono, revisado em PR, rodado uma vez
sob supervisão. Precedente: scripts/embedding_smoke.py.

### II. Estrutura: funções puras testadas + casca de I/O fina

A lógica de mapeamento legado→grafo vive em FUNÇÕES PURAS (feed_external_id,
item_args, join_transcript, distilled_args, render_feed_map, ReconReport) testadas
com fixtures (tests/scripts/test_neon_import.py), independentes do schema real do
Neon porque recebem valores/tipos de borda definidos por nós, nunca linhas cruas do
Neon.

A casca de I/O (SQL + adapter row→tipo) é fina e é a única peça que depende do
schema legado — preenchida no checkpoint da sessão quando o dono entrega o
pg_dump --schema-only. Um corpus por invocação (--corpus).

### III. distilled_for: novo método de leitura da store (não é especulação)

insert_distilled NÃO é idempotente por design (cada chamada cria um evento de
destilação novo — usa record ID fresco, não determinístico). Re-rodar o corpus de
1.250 destilados duplicaria tudo.

Solução: adicionamos à store o método distilled_for(db, item) -> list[RecordID],
travessia item <-derived_from<- distilled (espelha provenance). O script pula
itens que já têm destilado. NÃO é superfície especulativa: o M6 (backfill de
embeddings) precisa exatamente dessa leitura. Adicionado por TDD, teste de
integração, validado linha a linha pela thread principal (é código de store).

### IV. Marca de legado = proveniência, não campo de schema (D19a)

O schema é SCHEMAFULL e não tem campo para "isto é legado". Em vez de mudar o
schema, a marca de legado é a PROVENIÊNCIA: o run do import tem
worker="neon_import"; itens e destilados apontam para ele (collected_by /
produced_by). É queryável, zero mudança de schema.

Consequência (duas coisas distintas, não confundir): a marca de legado é
PERMANENTE — um destilado legado é o que tem produced_by→run(worker="neon_import"),
e continua legado para sempre. O candidato a backfill do M6 é uma condição
separada e TRANSITÓRIA: destilado sem chunk (chunk_of ausente). Hoje todo legado é
candidato (o import não gera chunks); depois do backfill do M6 os legados terão
chunks e continuarão legados.

### V. Política de timestamps

item.collected_at e distilled.created_at são DEFAULT time::now() READONLY no
schema: o valor é fixado no primeiro CREATE e NÃO muda em re-gravação. Um registro
novo do import recebe a data do import; um item da sobreposição já criado pela
coleta viva MANTÉM o collected_at da coleta viva (o upsert do import não re-data — e
re-runs também não). O timestamp ORIGINAL do legado, em nenhum caso, cabe nesses
campos.

Para itens: o timestamp ORIGINAL e as tags vão para o namespace `legacy` de
item.metadata (option<object> FLEXIBLE), custo zero.

Para destilados: o created_at original é PERDA CONSCIENTE — coberta pelo pg_dump
do dono, registrada aqui e não descoberta depois.

### VI. Política de caps do import ≠ do feed worker

O teto de conteúdo do import é de SANIDADE, com reject+log (conta em
skipped_invalid, com motivo), NUNCA truncamento. Truncar transcrição na última
chance viva do Neon = perda de dado. (O cap de 64KiB do feed worker é do worker,
não da store, e não se aplica aqui.) O valor numérico do teto é fixado no checkpoint
após o probe do maior transcript (plano §7.1.5).

Conteúdo legado veio da web = hostil: a fronteira é a tipagem (dataclasses
congeladas) + persistência por bind param na store, como o resto do projeto — o
script NÃO usa pydantic. Sanitização anti-prompt-injection pertence ao consumo
(destilação/leitura por agente), não ao import, que só preserva o conteúdo bruto.

### VII. Reconciliação por mapa manual + URL canônica

Os 6 feeds que a coleta viva já mantém são reconciliados por MAPA MANUAL do dono:
o dono aponta à mão qual feed_source legado corresponde a qual source existente.
Match automático por URL é SUGESTÃO, nunca decisão — porque a source existente foi
gravada pelo feed worker com o canonical exato do schedules.yaml (https vs http,
barra final), e um upsert com canonical diferente criaria source duplicada e
duplicaria os itens do período de sobreposição.

Itens legados de feed reproduzem o PREFIXO da cadeia de external_id do feed worker
(guid→link) para deduplicar a sobreposição — não a cadeia inteira. O worker tem
mais 2 degraus (sha256 de title+published, depois de content) que NÃO são
reproduzidos de propósito: hasheiam strings brutas do feedparser irrecuperáveis das
colunas do Neon, e reproduzi-los criaria falsa confiança de dedup. Item legado sem
guid nem link vira skipped_invalid contado — nunca se inventa id; se essa contagem
for material na execução, a decisão volta ao dono no checkpoint.

Ordem obrigatória entre corpora: sources → itens → distillations por último
(derived_from é ENFORCED; uma distillation órfã falha e vira skipped_invalid
contada, não crash).

### VIII. Definição precisa de "no-op" (prova de re-execução)

A prova de no-op (condição de desligamento) NÃO é "o banco fica byte-idêntico":
upsert_item reescreve content/metadata e, com run, repointa collected_by
(last-wins, by design).

O critério correto de no-op é: contagens estáveis, ZERO registros novos em
source/item/distilled, e IDs estáveis na re-execução. Para o corpus de
distillations, distilled_for é o que garante o no-op (item já destilado é
pulado).

A reconciliação (a condição de desligamento do Neon) é um contrato de TRÊS
categorias por corpus (ReconReport): cada linha de origem cai em `imported`,
`preexisting` (já-presente: re-run idempotente ou sobreposição — nenhum registro
novo) ou `skipped_invalid` (rejeitada, com motivo). Reconciliado se e só se
imported + preexisting + skipped_invalid == contagem de origem — nada some em
silêncio, toda divergência é acusada.

### IX. Rota de fallback via pg_dump restaurado

O script lê uma NEON_DATABASE_URL genérica (Postgres), não amarrada ao Neon. Se o
Neon for desativado antes de uma 2ª rodada de cortes, o script roda igual contra
um Postgres LOCAL restaurado do pg_dump — cortes adiados (emails, podcast_episodes,
target_channels) são recuperáveis mesmo com o Neon morto.

### X. Escopo de corpora da rodada 1

Rodada 1 (núcleo, nunca cortável): **sources, items, videos** (com transcripts
concatenados no content) **e distillations**. Adiáveis: **emails,
podcast_episodes e target_channels** — o `_CORPUS_ORDER` do script oferece
`podcasts`/`emails` como choices válidas, mas rodá-los na rodada 1 é opcional:
são recuperáveis via §IX (fallback do pg_dump restaurado), logo adiar não perde
prazo nem dado. A inclusão dos adiáveis na rodada 1 é confirmada com o dono no
checkpoint; o §IX é o que torna adiar seguro.

## Consequências

**Positivas:** grafo herda o acervo com proveniência completa; zero mudança de
schema; distilled_for já entrega o que o M6 precisa; reconciliação auditável (nada
some em silêncio); fallback via pg_dump remove a pressa da última-chance.

**Trade-offs/negativas:** perda consciente do created_at original dos destilados
(coberta pelo pg_dump); a casca de I/O (SQL) é validada só contra o Neon vivo, não
em teste automatizado; psycopg entra como dependência (dependency-group `import`,
fora da imagem de produção).

**Neutras:** import roda manualmente, um corpus por vez; feedback/gate_decisions/
interest_profile NÃO são importados na fase 1 (vivem no pg_dump; fase 3 importa se
quiser).

## Alternativas rejeitadas

- **Import como worker sob contrato:** overhead de recorrência para algo que roda
  uma vez (YAGNI).
- **Campo "legacy" no schema:** mudança de schema desnecessária quando a proveniência
  já responde (item IV).
- **insert_distilled idempotente / single-shot com verificação de contagem:**
  distilled_for é mais barato e o M6 precisa dele de qualquer forma.
- **Match automático de feeds por URL como decisão:** risco de source/itens
  duplicados por divergência de canonical (item VII).
- **Truncar conteúdo grande:** perda de dado irreversível na última chance viva
  (item VI).
- **Gravar timestamp original em collected_at/created_at:** campos READONLY por
  design do schema (item V).
