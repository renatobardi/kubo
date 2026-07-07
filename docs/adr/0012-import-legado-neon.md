# ADR-0012 — Import do legado NeonDB (script one-off via store)

> Status: **aceito** · Data: 2026-07-06

> **Revisado dentro da sessão 0007** após a exploração do schema real do Neon: o
> desenho inicial deste ADR assumia um modelo que não existia (uma tabela `items`
> de conteúdo e concatenação de ~790k segments de transcrição). O checkpoint com o
> dono existiu exatamente para isso — os fatos abaixo são o modelo verificado.

## Contexto

O grafo do Kubo (SurrealDB) precisa herdar o acervo do sistema legado "RARA", que
vive num NeonDB (Postgres). Contagens reais (verificadas no schema, não estimadas):

- **Conteúdo por tipo:** `news_items` (741), `channel_videos` (1.212) +
  `playlist_videos` (715), `podcast_episodes` (614), `emails` (226),
  `linkedin_posts` (7).
- **Texto extraído:** `transcripts` (3.203; youtube 2.273, news 507, podcast 296,
  email 122, linkedin 5) — o texto completo mora na coluna `transcripts.transcript`
  (o maior tem 482 KB); `transcript_segments` (816k, com timestamps) é detalhe do
  legado que NÃO é importado.
- **Destilados:** `distillations` (1.267; youtube 1.048, podcast 85, email 77,
  news 52, linkedin 5).
- **NÃO é conteúdo:** `items` (3.507) é a esteira de processamento do RARA (lane,
  flow_id, status — sem texto/título/url); `LiteLLM_*` é do proxy;
  `feedback`/`gate_decisions`/`interest_profile` ficam no pg_dump (fase 3).

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

A lógica de mapeamento legado→grafo vive em FUNÇÕES PURAS (mapeamento de linha para
args da store, prioridade de conteúdo, `ReconReport`) testadas com fixtures
(tests/scripts/test_neon_import.py), independentes do schema real do Neon porque
recebem valores/tipos de borda definidos por nós, nunca linhas cruas do Neon. A
casca de I/O (as query strings SQL contra o schema legado + o adapter `row → tipo`)
é fina. Um corpus por invocação (--corpus).

O conteúdo textual vem da coluna `transcripts.transcript` (texto completo já pronto)
— **não** há concatenação em streaming dos 816k `transcript_segments`. Onde a
coluna não tem texto, cai-se para o corpo da tabela-tipo (§VII). A leitura do Neon
usa cursor server-side por segurança de memória, mas sem o join gigante de segments.

### III. distilled_for: novo método de leitura da store (não é especulação)

insert_distilled NÃO é idempotente por design (cada chamada cria um evento de
destilação novo — usa record ID fresco, não determinístico). Re-rodar o corpus de
1.267 destilados duplicaria tudo.

Solução: adicionamos à store o método distilled_for(db, item) -> list[RecordID],
travessia item <-derived_from<- distilled (espelha provenance). O script pula
itens que já têm destilado. Não há item com >1 destilação legada (verificado:
`source_key` não se repete), então o skip por-item é seguro. NÃO é superfície
especulativa: o M6 (backfill de embeddings) precisa exatamente dessa leitura.
Adicionado por TDD, teste de integração, validado linha a linha pela thread
principal (é código de store).

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
não da store, e não se aplica aqui.) O maior transcript real é 482 KB, então o teto
é fixado em **1 MiB** — folgado, pega só outlier patológico, rejeita+loga sem
truncar. Isso responde o probe do plano §7.1.5 (não é mais "valor a fixar").

Conteúdo legado veio da web = hostil: a fronteira é a tipagem (dataclasses
congeladas) + persistência por bind param na store, como o resto do projeto — o
script NÃO usa pydantic. Sanitização anti-prompt-injection pertence ao consumo
(destilação/leitura por agente), não ao import, que só preserva o conteúdo bruto.

### VII. Modelo de mapeamento legado → grafo

O modelo real (verificado no schema) difere do que o plano assumiu. As regras:

**Sources** (grafo `source`, upsert por canonical):
- `feed_sources` → kind=rss, canonical=`endpoint`; `podcast_feeds` → kind=podcast,
  canonical=`feed_url`; `target_channels` → kind=youtube; `playlists` →
  kind=youtube-playlist.
- `emails` e `linkedin_posts` não têm fk para uma source de cadastro: cada um
  pendura numa **source sintética única** de canonical estável — `legacy:email` e
  `legacy:linkedin` (kind=email/linkedin). O cadastro `email_sources` NÃO é
  importado como sources vazias (corte registrado, pg_dump cobre). O `sender` do
  email vai para `metadata.legacy.sender` — se a fase 3 quiser sources por
  remetente, o dado está lá.
- Os 6 feeds que a coleta viva já mantém são reconciliados por **mapa manual do
  dono** (o dono aponta à mão qual `feed_source` legado é qual source existente);
  match automático por URL é SUGESTÃO, nunca decisão, porque o canonical da source
  viva é o do schedules.yaml byte a byte e um canonical diferente duplicaria a
  source e seus itens.

**Itens** (grafo `item`, external_id = chave natural; prioridade de conteúdo
`transcript → corpo/descrição/excerpt → ""`):
- **news** ← `news_items`: external_id=`url`; content=transcript(news) senão `body`
  senão `excerpt`; title, published_at.
- **youtube** ← **dirigido por `transcripts` WHERE source_type='youtube'** (2.273;
  é o que ancora os 1.048 destilados youtube — dirigir por channel/playlist_videos
  orfanaria destilados por construção): external_id=`youtube_video_id`;
  content=`transcripts.transcript`; LEFT JOIN channel_videos/playlist_videos por
  youtube_video_id para título/data/source (coalescendo quando o vídeo está nos
  dois). Vídeos SEM transcrição não viram item — **corte contado** no relatório,
  recuperável via §IX.
- **podcast** ← `podcast_episodes`: external_id=`guid`; content=transcript(podcast)
  senão `description`; title, enclosure_url.
- **email** ← `emails`: external_id=`message_id`; content=transcript(email) senão
  `body`; title=`subject`.
- **linkedin** ← `linkedin_posts`: external_id=url; content=transcript(linkedin).

**derived_from (distilled → item):** `distillations.source_key` é a chave natural
da origem em TODO tipo (youtube→youtube_video_id, news→url, email→message_id,
podcast→guid, linkedin→url). A resolução é por um **dict em memória** montado com
uma única query `SELECT external_id, id FROM item` (poucos milhares de linhas) —
não 1.267 table scans; o build do dict detecta colisão de external_id entre sources
(loga e decide, em vez de casar silenciosamente). Destilado cujo source_key não
resolve a um item vira `skipped_invalid` contado (órfão), nunca crash.

**Ordem obrigatória entre corpora:** sources → itens → distillations por último
(`derived_from` é ENFORCED).

**Dedup com a coleta viva (trade-off aceito):** `news_items` não tem guid; o
external_id legado de news é a `url`. O feed worker vivo usa `guid → link → hash`
(feed.py). Onde o feed publica guid ≠ url, o mesmo artigo do período de
sobreposição vira DOIS itens no grafo, silenciosamente. A janela é pequena (a
coleta viva é recente) e o risco é aceito; se barato, o relatório conta colisões
por `item.url`. (Isso substitui a premissa anterior de "mesma cadeia guid→link" —
nenhum corpus legado usa a cadeia do worker.)

### VIII. Reconciliação e definição de "no-op"

A prova de no-op (condição de desligamento) NÃO é "o banco fica byte-idêntico":
upsert_item reescreve content/metadata e, com run, repointa collected_by
(last-wins, by design). O critério correto de no-op é: contagens estáveis, ZERO
registros novos em source/item/distilled, e IDs estáveis na re-execução. Para o
corpus de distillations, distilled_for garante o no-op (item já destilado é pulado).

A reconciliação (condição de desligamento do Neon) é um contrato de TRÊS categorias
por corpus (`ReconReport`): cada linha de origem cai em `imported`, `preexisting`
(já-presente — nenhum registro novo) ou `skipped_invalid` (rejeitada, com motivo).
Reconciliado se e só se imported + preexisting + skipped_invalid == contagem de
origem — nada some em silêncio. `preexisting` é detectado por **point-read do record
ID determinístico antes do upsert** (a escala one-off permite). Item importado sem
nenhum texto (`content=""`) é uma **sub-contagem `sem_conteudo` dentro de
`imported`** (não uma 4ª categoria — senão a soma quebra); ele é importado mesmo
assim porque é a âncora do `derived_from` de um destilado real.

### IX. Rota de fallback via pg_dump restaurado

O script lê uma NEON_DATABASE_URL genérica (Postgres), não amarrada ao Neon. Se o
Neon for desativado antes de uma 2ª rodada, o script roda igual contra um Postgres
LOCAL restaurado do pg_dump — cortes adiados (vídeos sem transcrição, corpora não
rodados na 1ª passada) são recuperáveis mesmo com o Neon morto.

### X. Escopo de corpora da rodada 1

Rodada 1 importa **tudo que ancora um destilado** (a missão existe para preservar
os 1.267 destilados): sources, news, videos (via transcripts), podcasts, emails e
linkedin, depois distillations. Cortar podcasts/emails/linkedin orfanaria seus
destilados — por isso entram, apesar de "stretch" no plano original. O que fica de
fora: `transcript_segments`, o cadastro `email_sources`, vídeos sem transcrição, e
`feedback`/`gate_decisions`/`interest_profile` — todos cobertos pelo §IX/pg_dump.

### XI. Perdas conscientes na distillation

`distilled` é SCHEMAFULL com só `summary` + `claims` (+ created_at). Mapeamento:
`distillations.content` → `summary`; `distillations.structured.claims[].text` →
`claims` (verificado: `structured` traz uma lista de claims-like `{text, evidence,
ts_start}` — o texto é valor real preservado de graça). PERDAS CONSCIENTES
(cobertas pelo pg_dump, sem estender o schema — "zero mudança de schema" é um
positivo declarado): `evidence` e `ts_start` de cada claim, o `title` do destilado,
o `pattern`/receita, e o `created_at` original (§V).

## Consequências

**Positivas:** grafo herda o acervo com proveniência completa; zero mudança de
schema; `structured.claims` preservado como `claims`; sem streaming de 816k segments
(leitura direta da coluna); distilled_for já entrega o que o M6 precisa;
reconciliação auditável (nada some em silêncio); fallback via pg_dump remove a
pressa da última-chance.

**Trade-offs/negativas:** perdas conscientes na distillation (§XI) e do created_at
(§V), cobertas pelo pg_dump; possível duplicação de news do período de sobreposição
(§VII); vídeos sem transcrição não entram como item (corte contado, §IX); a casca
de I/O (SQL) é validada só contra o Neon vivo, não em teste automatizado; psycopg
entra como dependência (dependency-group `import`, fora da imagem de produção).

**Neutras:** import roda manualmente, um corpus por vez;
feedback/gate_decisions/interest_profile e `transcript_segments` NÃO são importados.

## Alternativas rejeitadas

- **Import como worker sob contrato:** overhead de recorrência para algo que roda
  uma vez (YAGNI).
- **Importar a tabela `items`:** é a esteira de processamento, não tem conteúdo.
- **Concatenar os 816k `transcript_segments`:** o texto completo já está na coluna
  `transcripts.transcript` — streaming de segments seria complexidade sem ganho.
- **Dirigir youtube por `channel_videos`:** orfanaria destilados de vídeos que têm
  transcrição/destilado mas não têm linha de cadastro (2.273 > 1.927).
- **Campo "legacy" no schema / estender distilled para title/pattern/evidence:**
  mudança de schema desnecessária; a proveniência marca o legado e as perdas são
  conscientes (§XI).
- **Source de email por remetente:** parsing de `"Nome <addr>"` gerando dezenas de
  sources agora é scope creep; o sender fica no metadata para a fase 3 re-ligar.
- **1.267 table scans para resolver derived_from:** um dict em memória é mais
  simples, mais rápido e detecta colisões (§VII).
- **Match automático de feeds por URL como decisão / truncar conteúdo / gravar
  timestamp original em campos READONLY:** duplicação silenciosa, perda de dado,
  campos READONLY por design — respectivamente §VII, §VI, §V.
