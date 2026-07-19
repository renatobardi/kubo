# ADR-0030 — Destino padrão no `settings` + fechamento do cutover: aposenta o `destinations.yaml`

> Status: **aceito** · Data: 2026-07-19 · Validado pelo advisor (Fable 5) antes do crave.
> **Emenda o ADR-0028 §1** (settings ganha `default_destination`); **executa o ADR-0027 §14/§6/§11**
> (ordem do cutover, local do retype, relocação do base URL). Resolve o ticket wayfinder #123 (mapa
> #117) — a **última peça estrutural** do épico.

## Contexto

O `destinations.yaml` + o módulo loader (`kubo/distribution/destinations.py`) + a entry
`worker: digest` do `schedules.yaml` são o último resquício YAML do eixo "para-quem". Os ADR-0027
(destino no DB), 0028 (settings/pausa) e 0029 (digest vira sweep) moveram destino, config e entrega
para o banco. Este ticket **fecha o corte**: o YAML morre.

Dois consumidores ainda hardcodam `owner-telegram` resolvido do YAML — o **report on-demand**
(`kubo/api/routes/flows.py` → `flow_runner.py` → `analyst.py`, escreve `dispatch artifact='report'`)
e o **CLI** (`kubo/__main__.py`). Precisam de um lar no DB. Essa é a decisão **nova**; o cutover em
si é execução do ADR-0027 §14. Mas o cutover está **acoplado à onda de build inteira**: a migration
0011 (ADR-0027 §6) apaga os `dispatch` e retipa `dispatch.destination` para `record<destination>` —
no instante em que roda, o código velho (que escreve `destination` como string) quebra. Logo **corte
único = um DEPLOY**, não um PR.

## Decisão

1. **`settings.default_destination`** (`record<destination>`, aceita NONE). O report on-demand e o
   CLI resolvem o alvo por uma função de store `default_destination(db)` — lê o `settings`, busca o
   registro, devolve o modelo do destino **ativo**, ou `ConfigError` nos casos NONE / dangling /
   arquivado. Um só ponto de verdade para report e CLI. O dono edita na tela de settings (#122).
   **É config operacional** ("pra quem vão meus one-offs"), não fato do destino — a mesma distinção
   que justificou a `settings` (ADR-0028 §1). Campo novo no singleton tipado = código + PR.

2. **Política de dangling em três pontas:**
   - **Hard delete do default:** limpa o ponteiro (`SET default_destination = NONE`) **dentro da
     transação atômica** que o ADR-0027 §12 já exige para o hard delete — um statement a mais, zero
     TOCTOU novo. Não bloqueia o delete (bloquear cria um 2º invariante a manter, sem ganho).
   - **Arquivar o default:** **mantém** o ponteiro, rejeita na resolução. Arquivar é reversível
     (#107); limpar o ponteiro perderia a escolha do dono — reativar o destino cura o default sozinho.
   - **Default pausado + report on-demand:** **envia** (decisão nova). Simetria exata com o ADR-0028
     §6: report on-demand é ação explícita do dono, "não me apite" não barra o que ele acabou de
     pedir. Pausado sai da *seleção do sweep* (ADR-0027 §8), não do alvo de uma ação manual. Só
     **arquivado** rejeita.

3. **O seed seta `default_destination = owner-telegram`** (obrigatório, não opcional). A tela de
   settings (#122) pode não existir no momento do cutover; um default nascendo NONE deixaria o report
   quebrado sem superfície de conserto além de edição manual do DB. Paridade out-of-the-box com o
   comportamento atual é o critério. **Ordem do seed:** a linha `settings:global` (#119) precisa
   existir **antes** de o seed do destino (#118) escrever `default_destination` — ordem interna
   consciente no `kubo/store/seed.py` (settings primeiro). O marker do seed já evita clobberar
   edição posterior do dono.

4. **Default irresolvível → `ConfigError` no clique** (503, reusando o handling que o
   `_owner_delivery`/report já tem hoje — sem semântica de status nova), com mensagem acionável
   ("defina o destino padrão em Configurações"). Falhar alto numa ação explícita é aceitável e
   visível.

5. **Cutover = UM DEPLOY** (a atomicidade do `deploy.sh`: rsync → build → migrations → seed → up).
   A precondição "0011/0012 rodados" só é segura quando "rodados" = **no mesmo deploy do código novo**
   — o retype destrutivo da 0011 quebra os escritores velhos no ato. **Forma: PRs empilhados, um
   deploy** (recomendação do advisor, pela fadiga de complexidade + precedente da casa de ADRs
   pequenos encadeados):
   - Os **aditivos primeiro** (tabela `destination` + store + seed **sem** o retype de dispatch;
     `settings` + boot + poll) — cada PR verde, revisável, deployável-aditivo.
   - O **PR de cutover** carrega a parte **destrutiva**: a migration que apaga+retipa `dispatch`, o
     rewire dos consumidores, e a morte dos YAML. Deploya-se **uma vez, no fim**.
   - **Reconcilia o ADR-0027 §6:** o retype de `dispatch.destination` sai da 0011 e vai para a
     migration do cutover (desvio de *build*, não de decisão; o ADR-0028 §1 já previu reconciliação
     de numeração). Não deployar às 09:30 (o scheduler velho roda enquanto a migration executa).
   - Gatilho para reconsiderar a forma 1 (PR único, fiel à letra do §6): se o diff total da onda
     couber em ~1–1.5k linhas medidas no 1º spike.

6. **Consumidores migram antes do YAML morrer** (ADR-0027 §14), no PR de cutover:
   - `scheduler/__init__.py` → sweep de destinos (ADR-0029).
   - **`flow_runner.py` + `analyst.py`** (o caminho do report): o modelo do DB (`ActiveDestination`)
     substitui `ResolvedDestination` através do runner. As **obrigações de PII test-enforced**
     (ADR-0027 §3; endereço pelo construtor, nunca em config/log — ADR-0029 §3) valem para **este**
     caminho também. O report on-demand **ignora `distribution_paused`** (ADR-0028 §6) — preservar no
     rewire, não "unificar" por engano com o curto-circuito do sweep.
   - `contracts/models.py` (`DispatchPayload.destination` → `^destination:...$`, ADR-0027 §10) e
     `store/knowledge.py` (`last_dispatch_watermark`/`distilled_for_digest`/`insert_dispatch`
     string → RecordID).
   - `api/routes/destinations.py`: (a) migra para leitura do DB **neste corte** (senão 500
     pós-cutover); (b) **regressão silenciosa a evitar** — o card "Digest" hoje deriva da entry
     `worker: digest` do `schedules.yaml`; com ela morta, o card **some sem erro**. Passa a ler
     `settings.digest_cron` (o `_humanize_cron` sobrevive, apontado para settings); a lista de
     destinos vira "destinos **ativos**" (ADR-0027 §8).
   - `flows.py` + `__main__.py` → resolvem via `default_destination(db)`.
   - **Loader morre:** `Destination`/`ResolvedDestination`/`load_destinations`/`resolve_destinations`
     apagados. `resolve_base_url`/`KUBO_BASE_URL` **relocaliza** para um módulo de config da
     distribution (ADR-0027 §11) — seus testes migram junto.

7. **CLI `--destination` morto** (`kubo/__main__.py:228`): com id surrogate, a string deixa de ser
   chave natural; lookup-por-nome é máquina nova sem demanda (YAGNI, pré-lançamento). O envio resolve
   só do `default_destination`. A resolução continua **lazy** (flows dev pulam — `__main__.py:174`
   evita de propósito; não dar dependência de settings ao `flow run dev-kubo`).

8. **Mudança semântica de env (documentar):** hoje editar `KUBO_OWNER_TELEGRAM_CHAT_ID` muda a
   entrega; pós-cutover a env é **só input do seed** — rotação de chat_id vira edição no DB/UI.
   Atualizar `docs/runbook-deploy.md` e `.env.example`.

## Consequências

- **Positivo:** o `destinations.yaml` morre, o eixo "para-quem" fica 100% no DB, e o report ganha um
  lar honesto (`default_destination`). Uma fonte-de-verdade, um estado honesto (o que o #108 pediu).
- **Trade-off — acoplamento da onda:** o cutover não é isolável; deploya junto com os builds de
  0027/0028/0029. Disciplina de **deployar uma vez, no fim** (já é o hábito manual do dono).
- **Trade-off — default pausado envia:** pode surpreender ("pausei, por que chegou?"); mitigado por
  ser ação explícita. Se incomodar no uso real, é ajuste de uma condição, não de arquitetura.
- **Trade-off — capacidade removida:** o `--destination` do CLI sai (hoje sem uso — só existe um
  destino). Pré-lançamento, aceitável.
- **Blast radius de teste (~15 arquivos):** `tests/distribution/test_destinations.py` morre (mas os
  testes de `resolve_base_url` **migram** com a função), `tests/api/test_destinations.py`,
  `tests/test_cli.py`, `tests/contracts/test_models.py`, `tests/store/test_dispatch.py`
  (string → RecordID) e os verticais que montam `ResolvedDestination` na mão passam a semear
  `destination`/`settings` por fixture (uma fixture única).

## Alternativas rejeitadas

- **`is_default` bool por destino:** exige o invariante cross-row "no máximo um default" que o
  SurrealDB não expressa (sem partial unique index — o ADR-0027 §4 já tropeçou nisso) → flip atômico
  set-novo+unset-velho, máquina nova. No singleton, "um default" é um campo, de graça.
- **Rollout em passos com estado misto:** o pior dos mundos (lição do #108); o dono pediu solução
  única.
- **PR-monstro (forma 1):** fiel à letra do ADR-0027 §6, mas o diff da onda inteira degrada o review
  (CodeRabbit já rate-limita em PRs menores). Reconsiderar só se o spike medir < ~1.5k linhas.
- **Default nascendo NONE:** deixaria o report quebrado sem superfície de conserto (a tela é #122,
  pode não existir no cutover). O seed setar owner-telegram é paridade out-of-the-box.

## Fechamento do mapa

Este é o último ticket de decisão do mapa #117. Resolvido, **a fase de decisão acaba** — o que resta
é o **build** (a onda 0027/0028/0029/0030: migrations, store, seeds, sweep + workers por canal, poll,
report/CLI/rotas rewired, morte do YAML), deployada num corte único.
