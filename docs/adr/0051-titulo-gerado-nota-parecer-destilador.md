# ADR-0051 — Destilador vira portão: nota de relevância, título gerado sob proveniência, prosa limpa

> Status: **proposto** · Data: 2026-08-04
> **Emenda o ADR-0013 §III** (contrato do destilador — acrescenta um passo antes
> da destilação e um caso de proveniência que o ADR original não previa).
> Resolve o mapa wayfinder KUBO-169 (KUBO-172, KUBO-174, KUBO-182), ticket de
> build KUBO-193.

## Contexto

Hoje **tudo que é coletado é destilado** — o `item_char_cap` e o lote pequeno por
run (ADR-0013 §III.7) controlam custo, não relevância. O mapa wayfinder KUBO-169
decidiu inverter isso: uma **nota de relevância** roda antes da destilação, e só o
que passa um corte mínimo é destilado. A maior parte do volume de coleta (releases
de repositório, ~80% do mix medido) nunca precisou de LLM para virar destilado —
a nota mede se vale a pena antes de gastar a chamada mais cara.

Separadamente, o destilador hoje deixa vazar markdown estrutural (`## SUMMARY`,
`## CONCEPTS`) na saída — a instrução sempre pediu prosa; é o modelo desobedecendo
dentro de um schema válido. E o Kubo precisa, pela primeira vez, permitir que o
LLM **produza** um dado que não estava no texto de origem (um título, quando a
fonte não trouxe um) — o que o ADR-0013 §III.6 e a disciplina geral de D6 nunca
prev(i)am: destilação sempre ecoa e nunca fabrica.

## Decisão

### I. A nota roda antes da destilação, dentro do mesmo worker — emenda ao §III

**Não nasce um worker novo.** O destilador (`DistillerWorker`) passa a, para cada
item pendente:

1. Pontuar o item **sobre título e URL** (nunca o conteúdo bruto — a nota não
   precisa dele) contra o **contexto de trabalho do tenant**
   (`user_profile.work_context` do dono do tenant — campo que já existe, ADR-0046
   — texto livre feito para prompt). **A nota é do tenant, não do usuário**: uma
   curadoria só por workspace. Ver Nota de compatibilidade abaixo sobre por que a
   granularidade é o tenant e não o usuário.
2. Persistir a nota como uma relação `(item, tenant) → nota`, com o momento em que
   foi atribuída. **Isto é a 6ª tabela extra-spec** (run: ADR-0002; chunk: ADR-0008;
   dispatch: ADR-0015; destination: ADR-0027; settings: ADR-0027/#119, que re-armou
   a cláusula de contenção do ADR-0002 pela última vez, contando 4ª e 5ª no mesmo
   ADR) — este ADR **re-arma de novo** (uma 7ª reabriria). A reabertura consciente:
   a nota é o mecanismo do funil invertido (item I), não cabe como campo em `item`
   porque é por `(item, tenant)`, não por `item` sozinho.
3. **Só destilar o que passa uma nota mínima de corte.** Quem não passa vira item
   cru: gravado (sempre — ver item II), buscável por texto literal, invisível
   para a busca semântica (sem destilado não há chunk nem `mentions`).
4. **Reprovação é definitiva.** Não há caminho de repontuação nem de destilação
   sob demanda — decisão consciente, não lacuna: destilar sob demanda ao abrir na
   UI criaria uma rota de LLM síncrona dentro de um request; repontuar quando o
   `work_context` muda exigiria um gatilho novo e uma janela de repontuação. Ambos
   recusados pelo custo de complexidade que introduziriam, não pelo custo de LLM.

**Por que dentro do mesmo worker, não um `scorer` separado:** a agenda não tem
vaga. A coleta RSS se estende até 08:50 e o destilador roda às 09:00 — um passo
próprio exigiria remanejar o horário do destilador e, por tabela, o horário do
digest (que o dono configura na UI, `settings.digest_cron`, ADR-0028). O preço,
nomeado e aceito: o destilador ganha duas responsabilidades, e **não há
isolamento de falha** — pontuação quebrada derruba a destilação do dia inteiro.
O `max_items` da config (ADR-0013 §III.7) passa a precisar de dois limites
distintos (quantos pontuar, quantos destilar) — desambiguação de build, não deste
ADR.

### II. O `item` é sempre gravado, passe ou não a nota — não é escolha

A chave de deduplicação de envio (ADR-0050 item III) vive no `item`. Se o
reprovado não virasse linha, o mesmo link voltaria amanhã, seria repontuado e
rejeitado de novo, todos os dias, para sempre — pagando LLM eternamente para
redescobrir a mesma rejeição. A gravação incondicional do item é consequência
estrutural dessa outra decisão, não uma preferência deste ADR.

### III. Prosa limpa — a defesa é na gravação, não só no prompt

A `_INSTRUCTION` do destilador (ADR-0013 §III) sempre pediu prosa ("Responda
SOMENTE no schema pedido"); o vazamento de markdown estrutural é o modelo
desobedecendo *dentro* de uma saída que já passa na validação pydantic — o
schema não impede texto com `##` dentro de um campo `str`. A defesa passa a ser
**na gravação pelo worker**: instrução reforçada mais uma limpeza determinística
antes de persistir (não depende do modelo obedecer). O parecer (ADR-0052) nasce
com o mesmo contrato desde o primeiro dia.

### IV. Título gerado por LLM — emenda ao §III.6, sob três cercas de proveniência

Item sem título na fonte (comum em releases sem nome, ou feeds mal formados)
passa a receber um **título gerado pelo LLM**, no mesmo passo que pontua. Isto é
uma exceção nomeada à disciplina de D6 ("destilação nunca fabrica o que não está
no conteúdo") — o título é a única exceção, sob três cercas:

1. **Campo próprio.** O título gerado nunca sobrescreve `item.title` nem qualquer
   título vindo da fonte — vive num campo separado, sempre.
2. **Marcação visível.** UI e e-mail marcam explicitamente que aquele título é
   gerado pelo Kubo, nunca o apresentam como se viesse da fonte.
3. **O piso de proveniência do ADR-0013 permanece de pé.** Item com `content`
   vazio continua fora do funil por completo (§III.1/§III.7 do ADR-0013,
   `items_to_distill` já filtra por `content != ""`) — gerar título não abre
   exceção para gerar destilado de conteúdo inexistente. As duas coisas são
   independentes: título ausente não implica conteúdo ausente.

Conteúdo coletado continua hostil por default (ADR-0013 §IV): o título gerado é
produzido pela mesma chamada que já trata o `content` como dado nunca-instrução —
nenhuma superfície nova de injection é aberta, é a mesma defesa já em produção.

## Nota de compatibilidade — nota por tenant, não por usuário (revisão de escopo)

Achado durante a redação da spec (KUBO-188), não deste ADR: a nota foi
inicialmente decidida por usuário (KUBO-174) e revisada para **por tenant** ao
mapear os seams de implementação — `destination` (ADR-0027) não tem nenhuma
ligação com `user`, então o worker de digest não teria como responder "a nota de
quem?" na hora de montar o envio. Nota por pessoa também furaria o gate deste
ADR (item I.3): bastaria **um** usuário do tenant aprovar um item para ele ser
destilado, e a economia do funil invertido vazaria pelo tamanho do workspace. A
âncora continua sendo `user_profile.work_context` — muda de quem: o do dono do
tenant, não o de cada membro.

## Consequências

- **Positivo:** a fila de destilação para de crescer — a demanda escala com o que
  passa a nota, não com o volume bruto de coleta (cadastrar 100 fontes novas
  multiplica a coleta, não a destilação). Medido: vazão cai de ~93 itens/dia
  destilados para ~20–30/dia aprovados, abaixo do teto já configurado.
- **Trade-off aceito:** ~63 itens/dia (~23 mil/ano) ficam cru — buscáveis só por
  texto literal, fora da busca semântica. O acervo semântico do Kubo passa a ser
  "o que algum dia foi relevante para alguém", não "tudo que foi coletado".
- **Trade-off aceito:** sem isolamento de falha entre pontuação e destilação
  (item I).
- **Gatilho que reabriria a nota por tenant:** um segundo workspace ativo com
  membros de contextos de trabalho genuinamente diferentes — nesse ponto a
  ligação `destination ↔ user` (hoje inexistente) teria que ser construída junto.

## Alternativas rejeitadas

- **Worker `scorer` separado** — rejeitada: sem vaga na agenda sem remanejar o
  destilador e o digest (item I).
- **Destilação sob demanda ao abrir na UI** — rejeitada: rota de LLM síncrona
  dentro de request, latência da ordem de segundos (item I.4).
- **Repontuação em lote quando `work_context` muda** — rejeitada: exige gatilho
  novo e janela de repontuação, custo de complexidade maior que o problema que
  resolve (item I.4).
- **Não gravar o item reprovado** — rejeitada: quebra o dedup de envio, que
  depende do `item` existir (item II).
- **Nota por usuário** (decisão original, revisada) — rejeitada após o achado do
  seam: `destination` não sabe "de quem" é o envio; furaria o gate de destilação
  (Nota de compatibilidade acima).
