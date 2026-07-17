# ADR-0023 — Renúncia: port de `scribe`/`harvest` e side-effect "criar repo na instanciação"

> Status: **aceito** · Data: 2026-07-17 · Validado pelo advisor (Fable 5) antes do crave.
> Estende ADR-0019 (D37, adiamento original do side-effect).

## Contexto

Duas promessas da spec funcional nunca foram cumpridas em 4 fases de uso real (0018–0021) e
ninguém sentiu falta:

1. **Spec §3.1** promete que instanciar um flow template materializa "side effects
   declarados (ex.: criar repo GitHub)". O ADR-0019 (D37) já havia adiado isso — "fica para
   quando um flow real precisar" — mas nenhum flow precisou. Na prática, o repo do worker
   nasce do próprio fluxo dev via PR (o dono cria o repo sandbox manualmente, ou o worker o
   cria como parte do seu próprio trabalho), nunca como efeito colateral automático da
   instanciação.
2. **Spec §5** (Fase 1) promete o porte de `scribe` (transcrição, whisper.cpp) e `harvest`
   (coleta de páginas/artigos) do RARA como templates de worker, junto com `feed`. Só `feed`
   foi portado — está em produção há meses. A Fase 1 foi declarada fechada (sessão 0021,
   2026-07-17) sem `scribe`/`harvest`, e o caso de uso real do dono até agora é RSS/GitHub,
   não transcrição de áudio nem scraping de artigos avulsos.

Uma promessa não cumprida sem decisão formal é dívida órfã — a spec mente por omissão. A
alternativa a "renunciar" seria "adiar de novo", mas isso já foi tentado uma vez (ADR-0019)
e o padrão se repetiu: sem sinal real de demanda, adiar vira eufemismo de nunca.

## Decisão

**Renunciar às duas promessas, formalmente, com gatilho de reabertura nomeado — não deixar
como dívida pendurada.**

1. **Side-effect "criar repo na instanciação":** remove-se o MECANISMO da spec (§3.1), não
   só o exemplo. "Side effects declarados" na instanciação de template era um mini-mecanismo
   de orquestração declarativa — mantê-lo como possibilidade abstrata reabriria a mesma porta
   que o invariante 3 e o escopo negativo (§1.2) fecham: templates são dados, sem lógica
   condicional embutida. Reabrir exige ADR próprio, nunca uma linha de código encaixada na
   instanciação.
2. **Port de `scribe`/`harvest`:** renunciado. `feed` permanece como o único worker RARA
   portado; a tese do grafo de conhecimento e o contrato de worker seguem provados por ele.
   O código legado do RARA (`rara-scribe`, `rara-harvest`) permanece referenciado em §7 da
   spec como ponto de partida, não apagado — só deixa de ser compromisso de roadmap.

**Gatilhos de reabertura nomeados** (decisão reversível, não permanente):
- `scribe`: demanda real de transcrição de áudio do dono (hoje nenhuma fonte de áudio entra
  no grafo).
- `harvest`: uma fonte que RSS/GitHub não cobrem (scraping de página avulsa sem feed).
- Side-effect de instanciação: um flow real que dependa de repo pré-existente criado fora do
  fluxo dev — verificado agora (`git grep` por `create_repo`/`side_effect`/`has_repo` fora de
  docs: zero ocorrências em `kubo/runtime`, `kubo/workers`, `catalogs/flow_templates`) que
  nada depende disso hoje.

Reabertura de qualquer um dos dois é **sessão nova com motivo novo**, não retomada da dívida
antiga.

## Consequências

- **Positivo:** a spec para de prometer o que não entrega; menos superfície pra manter
  coerente. Fecha uma porta de workflow-engine/DSL disfarçado (side effects declarados).
- **Trade-off aceito:** se a demanda de transcrição/scraping aparecer, o port começa do zero
  conceitualmente (o código RARA legado ajuda, mas não há trabalho de integração em
  andamento sendo preservado).
- **Neutro:** issues #78 e #79 fecham nesta ADR; issue de gatilho nomeada para o caso do
  side-effect não se aplica aqui (não há bug externo a monitorar, diferente do caso
  Custom/Ignore do D51).

## Alternativas rejeitadas

- **Adiar de novo (manter "fica para quando precisar"):** já tentado no ADR-0019; o padrão
  empírico (4 fases, zero demanda) mostra que "adiar" sem prazo é "nunca" com passo extra.
- **Portar `scribe`/`harvest` mesmo sem demanda ("a spec promete, então entrega"):** violaria
  a tese anti-fadiga do projeto — código sem caso de uso real é manutenção especulativa.
- **Manter o mecanismo de side effects declarados só sem exemplo:** rejeitado pelo advisor —
  esconderia a mesma promessa em forma abstrata, sem fechar a porta de fato.
