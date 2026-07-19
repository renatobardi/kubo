# ADR-0029 — Digest vira sweep de destinos: 1 run por destino, worker por canal, reativação escolhe backlog ou recente

> Status: **aceito** · Data: 2026-07-19 · Validado pelo advisor (Fable 5) antes do crave.
> **Emenda o ADR-0015 §IV** (entrega vira sweep) e o **ADR-0028 §5** (efeito da pausa +
> escolha backlog/recente); **refina o ADR-0027 §8/§9**. Resolve o ticket wayfinder #121 (mapa #117).

## Contexto

Hoje o worker `digest` (`kubo/workers/digest.py`) é **monolítico**: uma instância segura a lista de
todos os destinos, `run()` itera todos, e uma falha de envio vira `ErrorInfo(dispatch_partial)` com
o run fechando parcial. O `manifest` declara `integrations=["telegram"]` **estaticamente**; o branch
de e-mail é um `raise SenderError("email = 12.9")`.

Com destinos no DB (ADR-0027), múltiplos, e o canal e-mail chegando (#120), esse desenho bate em
duas paredes: **(1)** adicionar e-mail força o manifest para `["telegram", "smtp"]`, e o runtime
resolve as integrações **antes** de `run()` — então uma credencial SMTP ausente derrubaria o digest
**inteiro**, telegram incluso (a "armadilha da resolução eager", ADR-0015 §IV). **(2)** um digest é
uma entrega, e a camada de coleta já resolveu o mesmo problema de "N itens independentes, isolados"
com o **sweep dirigido por Cadastro** (#108, ADR-0025 §4): `query→loop→run_worker`, um run por item,
isolado, mapa fixo `kind→worker` em código.

Este ticket é a **metade de entrega** do épico (mapa #117), depois das duas raízes já cravadas:
ADR-0027 (destino no DB) e ADR-0028 (settings/pausa). Espelha a coleta: o digest vira um **sweep de
destinos**.

## Decisão

1. **O digest vira um "sweep de destinos".** O job (montado a partir de `settings.digest_cron`,
   ADR-0028; id estável `"digest"` preservado — o poll do §4 depende dele) consulta os destinos
   ativos, checa `distribution_paused` (ADR-0028 §5) **uma vez antes do loop** (curto-circuito →
   zero runs), e dispara **um run por destino**. Isolado por destino (conexão própria, `try/continue`,
   como `execute_sweep_job`). **Aposenta o `dispatch_partial`** — cada destino é seu próprio run com
   seu próprio `ErrorInfo`; o agregado "N de M falharam" migra do run para o log `digest_sweep_done`
   (mesmo trade do #108, mesmo invariante 7: sem run pai, sem estado de orquestração).

2. **Workers por canal, com manifest próprio.** `TelegramDigestWorker` (manifest
   `integrations=["telegram"]`) e `EmailDigestWorker` (`integrations=["smtp"]`), **single-destino**
   cada. Uma credencial SMTP ausente vira `SenderError` → `dispatch(error)` **só** nos runs de
   e-mail; os de telegram entregam. O `DigestWorker` monolítico, o `if channel == "telegram" ... else`
   e o mapa `senders` **morrem** — cada worker de canal possui o seu sender (injetável só para teste).
   Isso **reconcilia** o conselho anterior do advisor (`digest.py:12-13`, "despacho por canal
   explícito, sem registry de senders"): o `DEST_DISPATCH` abaixo não é um registry injetável em
   runtime — é um mapa fixo em código, chave nova = PR, o precedente `SWEEP_DISPATCH` já assentou.

3. **Despacho por um mapa fixo `DEST_DISPATCH {channel → factory}` — e o endereço (PII) viaja pelo
   CONSTRUTOR, não pela config.** Aqui o espelho do `SWEEP_DISPATCH` **quebra de propósito**: lá a
   factory é `Callable[[], Worker]` e o dado do Cadastro vira `config` (`build_config(source)`).
   Repetir isso colocaria o `address` (PII) dentro do `config` validado pelo manifest. Em vez disso,
   a factory é `Callable[[ActiveDestination], Worker]` (o precedente já existe — o `DigestWorker`
   atual recebe destinos injetados em `_instantiate`); o `config` do run carrega **só** `max_items`
   (constante pinada, ADR-0028 §2). **A config do run nunca carrega PII; o endereço viaja pelo
   construtor** (obrigação test-enforced, ADR-0027 §3). Observabilidade: um run que falha **antes** do
   dispatch registra só `worker` — o logger é bound com `destination=<id surrogate>` (não-PII) para o
   destino aparecer no erro.

4. **Renderer por canal; o conteúdo é o mesmo.** O seam `distilled_for_digest` (seleção de views por
   watermark) é compartilhado. `build_telegram_digest` (HTML ≤4096) fica; `build_email_digest` é
   **névoa** — o template do e-mail gradua com o canal e-mail (#120). Este ADR decide só a **forma**
   (renderer por canal, um por worker de canal), não o HTML do e-mail.

5. **Drain oldest-first (confirmado no código).** `distilled_for_digest` é
   `ORDER BY created_at LIMIT {max_items}` (ascendente) com `watermark = max(created_at)` da página =
   o piso do dia seguinte. O backlog drena **cronologicamente, sem pular**, capado por `max_items`/dia.
   **Dívida aceita, nomeada:** dois distilled no mesmo microssegundo (pós `time::floor(created_at,
   1us)`) cortados pelo `LIMIT` no meio → o gêmeo de fora é pulado pelo `>` estrito no dia seguinte.
   Probabilidade ~zero com o distiller serial, mas o **modo backlog multiplica** quantas vezes o corte
   do `LIMIT` acontece (todo dia durante o dreno). Consertar exigiria watermark composto `(time, id)`
   — over-engineering agora. Reabrir se observado.

6. **Reativação e unpause global escolhem backlog OU recente.**
   - **Backlog (default, sem escolha):** o dreno natural do item 5 — desde o watermark antigo,
     oldest-first, capado. Confirma o defer-não-discard do ADR-0027 §9 / ADR-0028 §5 **como default**.
   - **Recente:** avança o watermark do destino para o **`time::now()` do banco** (evita skew
     app↔DB), via uma **função de store nomeada `reset_destination_watermark`** que envolve
     `insert_dispatch` num dispatch **`ok` de zero-item** (`item_count=0`, `items=[]`,
     `watermark=now`). Reusa a máquina de watermark (sem campo novo), é **auditável** (aparece em
     Envios como "reset"), e a exceção ao só-se-novidade (§V) fica **localizada** a essa escrita
     administrativa por ação explícita do dono — o §V continua governando o job agendado, intacto.
   - **Vale para a reativação por-destino E para o unpause global** (o caso *férias* — a motivação
     mais forte do "recente"). Unpause global com "recente" = um reset zero-item **por destino ativo**.
   - **Rejeita `watermark_floor` no destino:** criaria uma 2ª fonte de watermark (`max(floor,
     dispatch)`), um campo permanente para um evento transiente. O zero-item mantém uma máquina só.
   - **Edge:** destino **novo** não tem dispatch → cai no bootstrap 24h, que já **é** "recente" — o
     prompt só faz sentido na **reativação**, não na criação ("backlog na criação" não existe pelo
     mecanismo; ticket próprio se um dia se quiser "e-mail novo recebe o acervo").
   - O **prompt** (UI) é #122; o **mecanismo** decide aqui.

7. **`distribution_paused` → ZERO runs** (supersede do efeito nomeado no ADR-0028 §5). O ADR-0028 §5
   dizia "30 dias de pausa = 30 runs `ok` na tela Execuções" — mas o curto-circuito antes do loop
   (item 1) produz **zero runs**. Este ADR supersede: **dia pausado = zero runs**, visível no log
   (`digest_sweep_skipped reason=paused`) e no toggle da tela de settings, não na tela Execuções.
   Os **três casos de "0 runs"** — distinguíveis no log, não na tela:

   | Caso | Log | Execuções |
   |---|---|---|
   | Pausado | `digest_sweep_skipped reason=paused` | nada |
   | 0 destinos ativos | `digest_sweep_done total=0` | nada |
   | Dia sem novidade | `digest_sweep_done` com runs ok | runs ok, sem dispatch |

8. **Canal sem worker no `DEST_DISPATCH`.** Ao contrário do `SweepEntry.kind` (validado eager no boot),
   o canal vem de **dado em tempo de fire** — não há validação eager possível. A borda pydantic limita
   a `telegram|email`, mas edição manual do DB fura. O loop tem o guard defensivo espelho do sweep:
   canal fora do mapa → **log + skip + `failed++`, sem abrir run** (não há worker).

9. **`active_destinations(db)` retorna TODOS os ativos** (todos os canais), e o sweep despacha por
   canal de cada linha. Isso **refina o ADR-0027 §8** (que a definiu `(db, *, channel)`): o filtro por
   canal vira opcional (default = todos), porque o sweep quer uma query só e roteia por linha.

## Consequências

- **Positivo:** multi-destino e multi-canal com **isolamento real** (SMTP frágil não arrasta o
  telegram); o e-mail entra sem tocar no telegram; o modelo fica **uniforme** com a coleta (sweep de
  destinos = sweep de fontes). O `dispatch_partial` some.
- **Trade-off — backlog longo drena em dias:** 1 mês de pausa em modo backlog = ~vários dias de
  digests até zerar (o "recente" é a saída para quem não quer isso).
- **Trade-off — visibilidade da pausa muda:** dia pausado = zero runs na tela Execuções (só no log);
  o diagnóstico "cadê o digest?" se resolve no log/toggle, não na tela (tabela do item 7).
- **Dívida nomeada:** empate de microssegundo na fronteira do `LIMIT` (item 5).
- **Neutro:** o job `"digest"` continua existindo com id estável; muda a *função* (vira sweep), o poll
  do ADR-0028 §4 é ortogonal (só troca o trigger).

## Alternativas rejeitadas

- **Um worker que ramifica por canal** (menor refactor): a armadilha da resolução eager — SMTP ausente
  derruba o run inteiro, telegram incluso. O isolamento exige worker (logo manifest) por canal.
- **`watermark_floor` no destino** (em vez do zero-item reset): 2ª fonte de watermark, campo
  permanente para evento transiente. Reabrir só se surgir um 2º escritor de reset.
- **UI fabricando o dispatch de reset direto:** confunde "fato de entrega" com escrita administrativa.
  A função de store nomeada (`reset_destination_watermark`) mantém a exceção ao §V localizada e
  gritante.
- **Política de drenagem fixa** (sempre backlog OU sempre recente): o dono escolhe no ato da
  reativação — férias longas querem "recente", pausa curta quer "backlog".

## O que estas emendas fazem aos ADRs vizinhos

- **ADR-0015 §IV:** a entrega deixa de ser "um worker, N destinos" e vira **sweep de destinos** (1 run
  por destino, worker por canal). At-least-once, `DispatchPayload`, seam `distilled_for_digest` e
  só-se-novidade (§V) **permanecem** — muda a topologia dos runs, não a semântica de entrega.
- **ADR-0028 §5:** (a) o efeito "pausa = N runs ok na tela" é **superseeded** por "pausa = zero runs"
  (item 7); (b) o defer-não-discard deixa de ser sem-escolha e ganha a opção **recente/discard** por
  ação explícita do dono (item 6). O critério de teste do 0028 ("pausa não move watermark")
  **permanece** — quem move é o reset explícito, nunca a pausa.
- **ADR-0027 §8:** `active_destinations` ganha o filtro de canal opcional (item 9). §9 (re-enable não
  re-bootstrapa) é o "backlog" default, agora um dos dois ramos da escolha.

## Fronteiras e follow-ups (mapa #117)

- **#121 NÃO mata o loader do `destinations.yaml`.** Aposentar o `DigestWorker` remove **um**
  consumidor, mas o **report on-demand** (`kubo/api/routes/flows.py:47`) ainda resolve `owner-telegram`
  contra o YAML — só o **#123** mata o loader (ADR-0027 §14). A sessão de execução não pode "limpar" o
  loader por zelo.
- **Pré-requisito de execução:** o sweep lê `destination` do DB → pressupõe as migrations 0011/0012 +
  seeds (ADR-0027/0028) **construídos e deployados**. É pré-requisito de build, não deste ADR.
- **#122** — o prompt backlog-vs-recente na reativação/unpause + o toggle de pausa. **#120** — o
  template do corpo de e-mail. **#124** — a porta de e-mail.
