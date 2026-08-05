# ADR-0052 — Parecer por item e resumo do dia: onde vivem, quem escreve

> Status: **proposto** · Data: 2026-08-04
> **Emenda o ADR-0028** (a premissa de independência total entre sweeps agendados)
> e o **ADR-0029** (os workers de digest passam a ler, e um deles a escrever, um
> artefato que não é mais só o `dispatch` de si mesmo). Depende do ADR-0050
> (janela de publicação) e do ADR-0051 (destilador vira portão). Resolve o mapa
> wayfinder KUBO-169 (KUBO-175, KUBO-182), ticket de build KUBO-195.

## Contexto

O digest ganha dois conteúdos editoriais novos: um **parecer opinativo** por
notícia (além do resumo factual que a destilação já produz) e um **resumo do
dia** como bloco de abertura. O ticket original que motivou este desenho (KUBO-175
do mapa) propunha uma "edição diária" única — seleção + nota + parecer + resumo,
computada uma vez e só renderizada pelos dois canais. Essa proposta não sobrevive
ao que os ADR-0050 e ADR-0051 já decidiram:

- A **nota** (ADR-0051) roda dentro do destilador, antes e independente de
  qualquer envio — não é um artefato "do dia", é um artefato "do item".
- O **parecer** precisa do conteúdo já destilado para ter substância — não pode
  sair da mesma chamada que a nota (que roda sobre título/URL, sem destilado
  ainda). São dois passos, em estágios diferentes do pipeline: circular pedir os
  dois de uma vez.
- A **janela elástica por destino** (ADR-0050 item II) significa que, num dia de
  recuperação, o Telegram pode cobrir 3 dias e o e-mail 1 — uma "edição do dia"
  única e compartilhada **não existe** nesse caso, por construção.

Este ADR decide onde cada um dos dois conteúdos vive, e resolve a única parte da
proposta original que sobrevive de fato: o **resumo do dia** precisa ser dito uma
vez só, igual nos dois canais — o que exige um artefato pequeno compartilhado
entre os dois sweeps independentes do ADR-0029.

## Decisão

### I. Parecer: pendura no par (item, tenant), computado no envio

O parecer é computado **no momento do digest**, para os itens selecionados pela
janela (ADR-0050), e persistido como uma relação `(item, tenant) → parecer` — sem
noção de dia. Compatível com a mesma granularidade da nota (ADR-0051): item,
tenant.

**Cláusula de contenção do ADR-0002:** a nota (ADR-0051) já re-armou a cláusula
pela 6ª vez. O parecer, sendo também `(item, tenant)`, é a **7ª tabela extra-spec**
— re-arma de novo (uma 8ª reabriria). O resumo do dia (item II abaixo), sendo
`(dia, tenant)`, é uma **8ª** tabela extra-spec distinta — re-arma mais uma vez
(uma 9ª reabriria). As duas reaberturas ficam registradas juntas aqui porque
nascem do mesmo ADR e da mesma sessão de build (KUBO-190).

**O compartilhamento entre canais sai de graça, sem acoplamento novo.** No dia
normal os 5 do Telegram são subconjunto dos 10 do e-mail (mesma ordenação por
nota, mesmo corte, o e-mail só pega mais do topo) — o parecer computado por
qualquer um dos dois serve ao outro, que só lê o que já existe. Na recuperação,
o que falta se computa incrementalmente. Nenhum sweep precisa saber que o outro
existe.

O parecer nasce sob o mesmo contrato de prosa limpa do ADR-0051 §III.

### II. Resumo do dia: registro pequeno por (dia, tenant) — o artefato compartilhado

O resumo cobre **o dia inteiro** que a janela normal representa (não só os itens
que couberam no envio) — "ontem saíram N publicações relevantes; o eixo foi X".
Precisa ser o **mesmo texto nos dois canais**: os dois sweeps do ADR-0029 rodam
de forma independente, e sem um registro compartilhado eles não têm como dizer a
mesma coisa sobre o mesmo dia.

Isto é a **emenda real ao ADR-0028**: a premissa de que schedules/sweeps são
inteiramente independentes uns dos outros (base do design do ADR-0010/0029)
ganha uma exceção pontual e nomeada — o destilador e os dois workers de digest
agora compartilham a leitura (e um deles a escrita) de um registro pequeno, por
(dia, tenant): texto do resumo e contagem de publicações daquele dia.

### III. Escrita híbrida — destilador eager, primeiro digest lazy como fallback

- **O destilador escreve o resumo ao terminar seu run** (uma vez por dia, antes
  de qualquer digest — ele é quem fecha o conjunto do dia, já que é ele quem
  decide, via a nota do ADR-0051, o que conta como "publicação relevante").
  Efeito colateral desejado: o resumo existe **mesmo que os dois envios de digest
  falhem** — dá à UI algo para mostrar num dia de distribuição quebrada.
- **Se o resumo não existir na hora do envio**, o **primeiro digest que rodar
  computa e grava.** Cobre o caso em que o destilador falhou, ou o dia chegou por
  um caminho diferente do usual.

**Preço nomeado, aceito:** o caminho de fallback é o único ponto de corrida entre
os dois sweeps do ADR-0029 — ambos podem ver "não existe" e computar/gravar
quase ao mesmo tempo. Aceito porque só ocorre num dia já degradado (destilador
falhou); a escrita precisa ser idempotente o bastante para que o pior caso seja
um texto sobrescrito por outro texto igualmente válido, **nunca um erro**.

### IV. Na recuperação, vai o resumo de um dia só

Quando a janela elástica do ADR-0050 cobre mais de um dia, o digest de
recuperação leva **só o resumo do dia mais recente do período**, não um por dia
coberto. Os resumos de dias anteriores falariam de itens que não estão no envio
(cada dia teve seus destaques; só 5/10 sobrevivem do período inteiro), e no
Telegram três blocos de resumo consumiriam cerca de um terço do orçamento de
4096 caracteres antes da primeira notícia. O cabeçalho de recuperação (ADR-0050
item VI, forma 4) já informa o período coberto — é ele que carrega a honestidade
que o resumo sozinho não teria.

## O que isto faz aos ADRs vizinhos

- **ADR-0028:** a premissa de independência total entre schedules/sweeps ganha
  uma exceção nomeada e pontual (item II acima) — não uma revogação geral.
  Nenhuma outra parte do ADR-0028 muda.
- **ADR-0029:** os workers de digest (`TelegramDigestWorker`, `EmailDigestWorker`)
  passam a **ler** o resumo do dia (e, no caminho de fallback, também escrever) —
  ortogonal ao isolamento por destino e ao despacho por `DEST_DISPATCH` que o
  ADR-0029 já decidiu; nenhuma dessas partes muda.

## Consequências

- **Positivo:** nenhum acoplamento novo entre os dois sweeps de digest no
  caminho normal — o compartilhamento do resumo sai da ordem natural (destilador
  antes de qualquer digest).
- **Trade-off aceito:** o caminho de fallback tem corrida real entre os dois
  canais (item III) — não corrigido porque só ocorre em dia já degradado.
- **Não existe mais** um objeto único "o que o Kubo achou do dia X" reconstituível
  como uma entidade — só o `dispatch` de cada destino, os pareceres soltos por
  item, e o resumo do dia. Aceito: a proposta original de "edição diária" foi
  descartada por não sobreviver à janela elástica (Contexto acima).

## Alternativas rejeitadas

- **"Edição diária" única (seleção + nota + parecer + resumo, computada uma
  vez)** — a proposta original do ticket KUBO-175. Rejeitada: a nota já roda
  antes e fora de qualquer digest (ADR-0051); o parecer não pode sair da mesma
  chamada que a nota (circular); e a janela elástica faz "o dia" não ser o mesmo
  intervalo para os dois canais num dia de recuperação — não há um "dia" único
  para uma edição representar.
- **Parecer computado dentro do destilador, junto com a nota** — rejeitada: a
  nota roda sobre título/URL, sem destilado ainda; o parecer precisa do
  conteúdo destilado para ter substância (Contexto acima).
- **Resumo computado sempre pelo primeiro digest a rodar (sem escrita eager do
  destilador)** — mais simples, rejeitada: perderia a propriedade de o resumo
  existir mesmo quando os dois envios de digest falham, que é o cenário
  degradado em que a UI mais precisa de algo para mostrar.
- **Resumo por dia coberto na recuperação (um bloco por dia)** — rejeitada:
  estoura o orçamento do Telegram e promete mais do que a lista de 5/10 entrega
  (item IV).
