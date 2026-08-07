# ADR-0050 — Digest por janela de publicação substitui o watermark posicional

> Status: **aceito** · Data: 2026-08-04
> **Substitui o núcleo do ADR-0015 §III e §V** (mecânica de watermark; só-se-novidade) —
> ver "O que sobrevive do ADR-0015 / ADR-0027" abaixo. **§IV permanece intacto** exceto
> pela mudança de chave descrita em VI. Resolve o mapa wayfinder KUBO-169
> (12 decisões: KUBO-172 a KUBO-182), tickets de build KUBO-192 a KUBO-195.

## Contexto

O digest diário entregava arqueologia, não notícia: `items_without_distilled` ordenava
por `id` (sha256, sorteio) sobre uma fila de 6.436 itens com teto de 50/dia — um item
novo tinha ~0,8%/dia de chance de aparecer. O mapa wayfinder KUBO-169 (cartografado
2026-08-02/03, dono) diagnosticou a causa e decidiu o desenho novo em 12 tickets. Este
ADR formaliza as decisões que tocam a mecânica de entrega — o núcleo do ADR-0015 §III
(watermark) e §V (só-se-novidade), que o ADR-0027 já havia confirmado sobreviverem
intactos a essa altura.

O ADR-0015 §III é explícito: "não improvisar", e detalha até a reconciliação de
precisão μs/ns do SDK. Esta substituição segue o mesmo rigor.

**Backlog descartado por decisão do dono:** os 6.436 itens legados (seed de 05/07 e
07/07) nunca serão destilados — não há migração de dados aqui, o desenho novo parte
de fila zerada.

## Decisão

### I. A seleção passa a ser por janela de publicação, não por watermark posicional

`item` ganha um campo próprio `published_at: datetime`, gravado na coleta:

- **RSS:** a data da entrada do feed, hoje descartada (só usada dentro do hash do
  `external_id`) — passa a persistir.
- **`github-releases`:** já persiste a data, hoje dentro de `metadata`; migra para o
  campo próprio.
- **Sem data na fonte:** usa `collected_at`. Caminho dormente na prática — 100% das
  2.169 entries medidas nos 10 feeds RSS ativos trazem data — mas existe e é testado.
- **Data futura:** rejeitada, cai no mesmo fallback (`collected_at`). Fonte que
  anuncia amanhã não fura a janela de hoje.

A janela é o **dia de calendário anterior, no fuso do tenant** — não uma janela móvel
de 24 horas. `published_at` (ou o fallback) cai dentro de `[ontem 00:00, ontem 23:59:59]`
no fuso do tenant. Isso torna a seleção determinística e reprodutível: rodar a
consulta duas vezes no mesmo dia dá o mesmo resultado, o que o watermark posicional
(dependente de quando cada `insert_distilled` comitou) nunca garantiu.

### II. Janela elástica por destino — substitui o bootstrap 24h do §III.3

O ADR-0015 §III.3 fixava o bootstrap em `now - 24h` para um destino sem dispatch
anterior. Essa regra é substituída por uma mecânica única que cobre tanto a estreia
quanto a recuperação de falha:

**Janela = do dia seguinte ao dia coberto pelo último dispatch `ok` daquele destino,
até ontem — com teto de 7 dias.**

- **Caso normal:** o último dispatch `ok` cobriu o dia D; hoje a janela é só `D+1`
  (= ontem). Idêntico em efeito ao caso normal do watermark antigo.
- **Destino novo:** sem dispatch anterior, a janela é só ontem (equivalente ao
  bootstrap 24h, mas expresso na mesma mecânica — não é mais um caso especial).
- **Recuperação de falha (substitui o §III.2 "retry de graça"):** se `telegram-digest`
  falhou em 31/07, 01/08 e 02/08, o próximo dispatch `ok` em 03/08 cobre a janela
  `31/07–02/08` inteira — até o teto de 7 dias. **Isto é uma mudança de propriedade
  em relação ao watermark antigo:** o watermark posicional (`created_at > max`) nunca
  perdia conteúdo — item publicado é sempre `>` algum watermark passado, eventualmente
  entra. A janela por publicação é absoluta: sem a elasticidade deste item, um dia de
  falha faria aquele dia de publicação nunca mais ser elegível (o "ontem" de hoje já é
  outro). A elasticidade por destino é o que preserva, sob o novo regime, a mesma
  garantia que o watermark dava de graça sob o regime antigo.
- **Teto de 7 dias:** evita que uma falha de semanas produza uma janela de recuperação
  do tamanho do histórico inteiro. Item fora do teto nunca mais é elegível — perda
  aceita, nomeada.

**Consequência assumida, nomeada:** feed atrasado, fonte que volta de pausa, ou uma
falha de *coleta* (não de envio) fazem o item perder a janela de publicação
permanentemente. A elasticidade deste item cobre falha de **envio**, nunca de
**coleta** — são fronteiras distintas e a segunda não tem rede de segurança.

### III. Exclusão por já-enviado — chave é o `item`, não o `distilled`

Além da janela, a seleção exclui itens já enviados àquele destino nos últimos **7
dias** (mesmo número do teto da janela elástica, de propósito — um item não pode
reentrar por recuperação depois de já ter sido enviado dentro do próprio período que
a recuperação cobre).

**A chave de identidade é o `item`, não o `distilled`.** Um item pode ser re-destilado
(conteúdo atualizado, reprocessamento) e o `distilled` resultante ganha um id novo —
usar o id do destilado como chave faria esse item reentrar no digest como se fosse
inédito. O `item` é a identidade estável do conteúdo coletado.

**Deduplicação por URL** entre fontes diferentes (mesma notícia publicada em dois
lugares), com normalização antes de comparar (trailing slash, http/https, parâmetros
de rastreamento). Limite honesto: não pega republicação com URL genuinamente
diferente — aceito, é o caso fácil que resolve a maioria (mesmo raciocínio do
artefato de referência que o dono trouxe).

**Listas de já-enviados permanecem independentes por destino** — propriedade do
ADR-0015 §III.2 preservada. Só dispatch `status='ok'` conta.

### IV. `dispatch.watermark` é reaproveitado, não removido — muda o que ele mede

O campo `dispatch.watermark: datetime` (ADR-0015 §II, validado como obrigatório
para `artifact='digest'` pelo ADR-0016 §V) **continua existindo e obrigatório para
digest**, mas seu **valor** muda de significado:

- **Antes:** `max(distilled.created_at)` do conjunto selecionado — uma marca
  posicional no fluxo de inserção.
- **Depois:** o **último dia de calendário coberto pela janela**, derivado da
  fronteira da janela (item II), não do conteúdo selecionado. Em operação normal
  isso é sempre "ontem"; em recuperação, o dia mais recente do período recuperado.
  **Independente de a janela ter ou não item aprovado** — é por isso que a forma
  2 do item VI (nada publicado, zero itens na janela) ainda produz um watermark
  válido: o valor vem da janela em si, nunca de `max(published_at)` dos itens
  nela, que inexistiria no caso vazio.

A leitura do próximo dispatch (item II: "o dia seguinte ao coberto pelo último
`ok`") consome este campo exatamente como antes consumia o watermark posicional —
mesmo papel na mecânica ("até onde já fomos"), valor com semântica nova. Isto
**não é um campo vestigial**: ele é o mecanismo pelo qual a janela elástica sabe
onde recomeçar. `DispatchPayload.watermark` continua `datetime | None` com o
validador do ADR-0016 §V intacto (digest exige, report não tem).

### V. `DispatchPayload.items` muda de `distilled` para `item`

Consequência direta do item III: o payload de envio passa a registrar ids de
**item**, não de destilado. `DispatchPayload.items: list[str]` muda o pattern de
validação da fronteira pydantic de `^distilled:...$` para `^item:...$`; a store
converte para `RecordID("item", ...)` no `insert_dispatch`, espelhando o mecanismo
existente. A mesma exceção nomeada à disciplina de ref opaco do ADR-0013 continua
valendo (worker de digest é mecânico, sem LLM no circuito).

**A migração inclui a DDL, não só o código.** `dispatch.items` é
`option<array<record<distilled>>>` (SCHEMAFULL) — grava `record<item>` sob esse
tipo falha na escrita, não só na validação pydantic. A migração troca o tipo do
campo via `DEFINE FIELD OVERWRITE items ON dispatch TYPE option<array<record>>`
— o tipo genérico `record` (em vez de `record<item>`) acomoda o caso do
`artifact="report"` (ADR-0016 §V), onde `items` continua registrando ids de
`distilled` (fontes consultadas pelo analyst para auditoria). A validação
pydantic na borda discrimina por `artifact`: digest exige `item:<hex>`, report
exige `distilled:<hex>`. Nunca `DEFINE TABLE OVERWRITE dispatch` (ADR-0027 §6
já avisa contra isso, perderia o resto do schema da tabela).

### VI. Revogação do §V — o Kubo nunca fica calado

O ADR-0015 §V ("dia sem novidade → run fecha ok, zero mensagem") é **revogado**.
Silêncio é indistinguível de falha, e o dono descreveu exatamente esse sintoma como
parte do problema original. A partir deste ADR, todo run de digest produz uma
mensagem, em uma de quatro formas:

1. **Normal** — houve conteúdo aprovado, o digest sai com as notícias.
2. **Nada foi publicado** — a janela de publicação estava vazia na origem.
3. **N publicações, nenhuma passou o corte** — com o **número** de publicações da
   janela. É o único sinal disponível para saber se o corte de relevância
   (ancorado no `work_context` do tenant, KUBO-174) está calibrado longe demais.
4. **Recuperação** — o dispatch cobre mais de um dia (janela elástica ativa);
   identifica-se como tal e informa o período coberto. Leva só os itens do dia
   mais recente do período (KUBO-179): a janela elástica recupera o watermark,
   mas o digest só mostra o dia mais recente — os dias intermediários ficam
   visíveis na UI, não no canal de entrega.

**O aviso das formas 2 e 3 conta como dispatch `status='ok'`, `item_count=0`**,
com `watermark` = o dia mais recente da janela que estava vazia/sem aprovados.
Isto é necessário, não cosmético: se o aviso não avançasse o watermark, a janela
elástica (item II) nunca resetaria, e o dia seguinte tentaria "recuperar" um
período que na verdade já foi endereçado (com um aviso, não com silêncio).

**O que não muda:** o dia vazio nunca degrada para "manda qualquer coisa abaixo do
corte" — se nada passou, nada de notícia é enviado, só o aviso. O corte continua
significando alguma coisa.

## O que sobrevive do ADR-0015 / ADR-0027

Confirmando e revisando o que o ADR-0027 já havia certificado sobrevivente:

- **§III.1 (watermark = marca do conjunto selecionado, nunca `sent_at`)** — o
  *princípio* sobrevive (o campo mede "até onde fomos", nunca o instante do envio),
  mas o *cálculo* muda inteiro (item IV acima).
- **§III.2 (retry de graça por-destino)** — a **propriedade** sobrevive (falha de
  envio não perde conteúdo), mas o **mecanismo** muda de watermark-que-nunca-perde
  para janela-elástica-com-teto (item II acima) — porque a janela por publicação,
  ao contrário do watermark posicional, tem um "hoje" que passa e não volta.
- **§III.3 (reconciliação μs/ns do SDK)** — **morre com o mecanismo que corrigia.**
  Essa reconciliação existia para o `created_at > watermark` estrito; a seleção
  nova é por dia de calendário, sem comparação de instante — o problema que ela
  resolvia deixa de existir.
- **§IV (DispatchPayload na união, seam `distilled_for_digest`, Telegram/SMTP como
  integração de catálogo, entrega at-least-once, falha parcial)** — **permanece
  intacto**, exceto a mudança de chave do item V acima. `distilled_for_digest`
  muda de assinatura/implementação (seleciona por janela+exclusão, não por
  watermark), mas continua sendo o único método do seam que a seleção usa.
- **ADR-0016 §V (`dispatch.artifact` filtra o watermark do digest)** — **intacto**,
  reforçado pelo item IV: o watermark de report continua não-existente
  (`None`), o de digest continua obrigatório, só o cálculo do segundo mudou.
- **ADR-0027 §9 (watermark por-destino, reconciliação μs, re-enable não
  re-bootstrapa)** — a reconciliação μs morre (ver acima); "por-destino" e
  "re-enable não re-bootstrapa" sobrevivem sob a nova mecânica (reativar um destino
  pausado não recalcula nada especial — ele só tem uma janela elástica potencialmente
  grande, capada pelo mesmo teto de 7 dias do item II).
- **ADR-0029 (sweep de destinos, worker por canal, `drain oldest-first`)** — o
  **item 5** desse ADR (drenar cronologicamente por `created_at`, capado por
  `max_items`/dia) é **substituído**: não há mais "drenar backlog" — a seleção é
  sempre a janela+exclusão deste ADR, ordenada por nota (KUBO-172/174), cortada em
  5 ou 10 do período inteiro (KUBO-179), nunca por dia. O resto do ADR-0029
  (isolamento por destino, worker por canal, `DEST_DISPATCH`) é ortogonal e
  permanece intacto. **O item 6** (escolha do dono entre reativar um destino
  pausado "por backlog" ou "recente") **colapsa**: sob a janela absoluta com
  teto de 7 dias, não existe mais backlog para escolher — reativar um destino
  pausado é só voltar a rodar o sweep, e a janela elástica (item II) resolve
  sozinha até onde recuperar, capada pelo mesmo teto de sempre. Não há mais
  escolha a apresentar ao dono nesse ponto; `reset_destination_watermark`
  (o mecanismo do dispatch `ok`/zero-item de ADR-0029 item 6) sobrevive só
  como o caso geral do aviso de forma 2/3 do item VI abaixo — deixa de ser
  uma ação administrativa distinta, e a framing de "exceção ao §V" que o
  justificava morre junto com a revogação do §V (item VI).

## Consequências

- **Positivo:** a seleção fica determinística (dia de calendário, não instante de
  inserção); a reconciliação μs/ns desaparece (fonte de complexidade a menos); o
  Kubo nunca mais fica em silêncio indistinguível de quebrado.
- **Trade-off aceito:** a janela por publicação é implacável com falha de
  *coleta* — não há mecanismo de recuperação para isso, só para falha de *envio*.
- **Trade-off aceito:** dedup por URL não pega republicação com URL genuinamente
  diferente.
- **Gatilho que reabriria esta modelagem:** se a fronteira coleta/envio deixar de
  ser suficiente (ex.: quiser recuperar também falha de coleta), a janela elástica
  precisaria de uma segunda dimensão — não construir sem esse gatilho aparecer.

## Alternativas rejeitadas

- **Manter o watermark posicional, só trocar o campo de comparação para
  `published_at`** — rejeitada: o watermark posicional pressupõe monotonicidade
  entre inserção e o campo comparado (`created_at` é monotônico com a inserção;
  `published_at` não é — um item publicado ontem pode ser coletado depois de um
  publicado hoje). Comparar `published_at > watermark` sob essa premissa quebrada
  reintroduziria exatamente o bug que a mudança pretende resolver.
- **Chave de dedup no `distilled`** — rejeitada: um item redestilado reentraria
  como notícia inédita (item III).
- **Sem teto na janela elástica** — rejeitada: uma falha de semanas despejaria um
  histórico inteiro de uma vez; 7 dias equilibra recuperação real contra volume.
- **Degradar o dia vazio para enviar o que houver, mesmo abaixo do corte** —
  rejeitada: esvaziaria o sentido da nota mínima de corte (KUBO-174).
