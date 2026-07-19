# ADR-0027 — `destination` vira Cadastro no DB: destinos geríveis pela UI, multi-canal, endereço no banco

> Status: **aceito** · Data: 2026-07-19 · Validado pelo advisor (Fable 5) antes do crave.
> **Substitui o núcleo do ADR-0015** (§I "destino não é tabela"; "sem UI de escrita") — ver
> "O que sobrevive do ADR-0015". Resolve o ticket wayfinder #118 (mapa #117).

## Contexto

Hoje um **destino** de distribuição vive no `destinations.yaml` da raiz (o "para-quem", ADR-0015
§I) + `env`: cada entrada tem `id`/`name`/`kind`/`channel`/`address_ref`, e o endereço real
(chat_id, e-mail) é só **referência** `env:VAR` — PII fora do repo por construção. A tabela
`dispatch` (fato de entrega: destination-string/channel/status/watermark/items/artifact) é a
única tabela; `dispatch.destination` é **string plana** = o id do YAML, não RELATION. Não há
caminho de criar/editar destino pela UI — a tela Destinos é read-only, e "desabilitar um destino"
= remover a entry do YAML (ADR-0015). Só existe um destino: `owner-telegram`.

O ADR-0015 **rejeitou explicitamente** destino-como-tabela (alternativa "a", premissa **E1**):
"tabela + migration + UI de escrita é custo sem retorno para um dono só". Essa premissa
**evaporou**: #105–#107 construíram a máquina de UI de escrita DB-backed para `source` (criar/
editar/pausar/arquivar/apagar, credencial `kubo_rw`, CSRF, guarda de staleness). O custo que
justificava a rejeição já foi pago e é reusável.

O pedido do dono — **cadastrar qualquer e-mail como destino pela UI, ter múltiplos destinos,
canal e-mail além do Telegram, tudo no banco** — reabre a modelagem. E os **gatilhos** que o
ADR-0015 registrou como reabrindo o desenho (múltiplos artefatos por destino; split
multi-mensagem; drill-down) **não** foram os que dispararam; o que disparou foi a queda da
premissa E1 + a **uniformidade** com o Cadastro de fontes. `destination` passa a ser a **4ª
tabela extra-spec** (run: ADR-0002; chunk: ADR-0008; dispatch: ADR-0015; destination: aqui). A
cláusula de contenção do ADR-0002 exige que uma tabela extra-spec seja uma reabertura de
planejamento consciente — esta é ela, e este ADR **re-arma** a cláusula (uma 5ª reabre de novo).

Contexto de dev que molda a decisão: **a app não foi lançada**. O histórico de `dispatch` é dado
de desenvolvimento — pode ser apagado. Isso libera o desenho da amarra de back-compat.

## Decisão

Remodelar `destination` num Cadastro no DB, geríveis pela UI, **espelhando o molde do `source`**
(ADR-0025 / #107). Escolhas:

1. **Cadastro `destination` no DB com id surrogate** (gerado pelo SurrealDB). 4ª tabela
   extra-spec; ver Contexto sobre a cláusula do ADR-0002.

2. **Campos:** `id` (surrogate) · `name` string · `kind` string (`pessoa`|`sistema`, conceito
   D11) · `channel` string (`telegram`|`email`) · `address` string **PLAIN** (PII) · `enabled`
   bool DEFAULT true · `archived_at` option\<datetime\>. **Lifecycle de 3 estados por 2 campos**
   (não enum) — ativo (`enabled=true`, `archived_at=NONE`), pausado (`enabled=false`,
   `archived_at=NONE`), arquivado (`archived_at` set ⇒ `enabled=false`) — com o invariante
   unidirecional `archived_at IS NOT NONE ⟹ enabled=false` garantido pela store. Espelho exato do
   `source`. Validação de `kind`/`channel`/`address` na **borda pydantic**, não em ASSERT no
   schema (estilo da casa: `dispatch`/`source` não usam ASSERT).

3. **Endereço PLAIN no banco — é PII, não segredo.** A linha honesta: um **segredo** (invariante
   8) *autentica/autoriza* — bot token, SMTP password; vazou, um terceiro age em seu nome →
   env-only, inegociável. Um **endereço** (chat_id, e-mail) *roteia*; vazou, alguém sabe seu
   e-mail — dano incomparavelmente menor, e aqui é o e-mail **do próprio dono** no banco **do
   próprio dono**, atrás de Tailscale/VCN. Cifrar no DB com a chave em `env` no mesmo host é
   segurança de fachada (chave e dado atrás da mesma fronteira) — custo real, ganho nulo.
   Obrigações do plain-PII — **critérios de aceite test-enforced** que atravessam para os tickets
   de build (store/migration/#122), não promessas de prosa: nunca em log/`structlog`; `repr=False`
   nos modelos carregados (o `ResolvedDestination` legado é só o **precedente**, não a prova — o
   modelo novo persistido carrega o mesmo fechamento); nunca literal em seed/fixture/commit; nunca
   em traceback; nenhum valor PII em dump/erro estruturado. Cada obrigação nasce com um teste que a
   exerce, antes do cutover. **Credencial NÃO migra para o banco:** Telegram e SMTP
   seguem integrações de catálogo (`catalogs/integrations/*.yaml`, `secret_ref` env). A tabela
   `destination` guarda **endereço**, nunca token/senha.

4. **`UNIQUE(channel, address)`** — a mesma pessoa por Telegram **e** e-mail são 2 linhas
   legítimas; o mesmo endereço 2× no mesmo canal é o bug real (inbox recebe o digest duplicado).
   Duas semânticas que o modelo pina (não deixa como acidente):
   - **Normalização por canal num normalizer compartilhado, obrigatório em TODOS os caminhos de
     escrita** (create/edit/reativação/**seed**/**migration**), não só na borda da UI — senão a
     constraint mente: `Foo@Bar.com` e `foo@bar.com` seriam 2 linhas, e o seed gravando o `chat_id`
     cru do env furaria o índice. É **uma função única** (a borda pydantic a chama; seed e migration
     também a chamam **antes** da checagem de unicidade/persistência), com testes de contrato por
     caminho de escrita, inclusive reativação. E-mail = trim + lowercase; chat_id = dígitos com `-`
     opcional (grupos).
   - **Arquivado segura o slot.** O índice não conhece `archived_at` e o SurrealDB não tem partial
     unique index. Logo re-cadastrar um endereço arquivado = **reativar** o registro existente,
     nunca criar novo. Isso é semântica do modelo; o *como* na UI (lookup-first estilo #105 ou
     "reative o arquivado") é problema do #122.

5. **`dispatch.destination`: string → `record<destination>`** (link forte). É o estilo da casa
   (`dispatch.items` já é `array<record<distilled>>`) e o SCHEMAFULL valida o tipo/tabela do id na
   borda de graça. **Honestidade obrigatória:** `record<T>` valida **tipo, não existência** — não
   é FK. Um dangling ref após hard delete é tecnicamente possível; quem o impede é o guard "zero
   dispatches" na **store** (§12), não o schema. **Sem RELATION ENFORCED** (ADR-0008 §VI já a
   rejeitou; o guard na store basta para um mantenedor solo). Índice e watermark não mudam:
   `DEFINE INDEX ... FIELDS destination` e `WHERE destination = $d` funcionam igual com bind de
   RecordID.

6. **Cutover limpo do `dispatch` (dev, pré-lançamento):** a migration `0011` **apaga** os
   dispatch existentes. Ordem obrigatória na migration: `DELETE dispatch;` **antes** do
   `DEFINE FIELD OVERWRITE destination ON dispatch TYPE record<destination>` (deletar primeiro
   torna irrelevante qualquer dúvida sobre re-validação de linhas existentes no v3.1.5). Usar
   **só** `DEFINE FIELD OVERWRITE`, **nunca** `DEFINE TABLE OVERWRITE dispatch` (redefinir a
   tabela convida a re-declarar campos e esquecer o `artifact`). **Shape final de `dispatch`**
   (preservado, só `destination` muda de tipo): `destination record<destination>`, `channel`,
   `status`, `sent_at` READONLY, `watermark`, `item_count`, `error option<object> FLEXIBLE`,
   `items option<array<record<distilled>>>`, **`artifact string DEFAULT 'digest'`** (ADR-0016 §V —
   intocado; `last_dispatch_watermark` filtra `artifact='digest'` para um report não mover o
   watermark do digest).

7. **Seed once-per-env** do destino `owner-telegram`: um passo de deploy (irmão das migrations,
   precedente #108) cria o destino lendo **`KUBO_OWNER_TELEGRAM_CHAT_ID` do env** — nunca o
   chat_id literal no código do seed (é PII). Env ausente → falha alta (`ConfigError`, consistente
   com `_resolve_one`). Idempotência via **marker** (`seed_marker`, precedente #108), não
   lookup-first no UNIQUE — o marker resolve corretamente o caso "dono editou/pausou/arquivou o
   destino antes do re-run do seed" (não o ressuscita).

8. **Seleção de destinos ativos:** `active_destinations(db, *, channel)` na store
   (`WHERE enabled=true AND archived_at IS NONE` filtrado pelo canal servido), espelho de
   `active_sources`. Destino pausado ou arquivado ⇒ **fora** da seleção do digest.

9. **Watermark — mecânica intocada, só a chave muda.** O ADR-0015 §III permanece integral:
   por-destino, só `status='ok'` avança, bootstrap `now - 24h` na estreia, reconciliação de
   microssegundos (`time::floor(created_at, 1us)`). Só a **chave** de `dispatch.destination` muda
   de string-YAML para RecordID. **Re-enable não re-bootstrapa:** um destino pausado por semanas e
   reativado mantém o watermark antigo; o `limit` da query capa o flood do primeiro digest e o
   resto flui nos dias seguintes (semântica desejada e barata). Apagar os dispatch (§6) reseta o
   watermark globalmente → o **1º digest pós-migration cai no bootstrap 24h**: um destilado criado
   há >24h e nunca despachado é pulado. Consequência **aceita** — em dev com digest diário a janela
   de perda é ~zero.

10. **Contrato:** `DispatchPayload.destination` vira string `^destination:...$` na fronteira
    pydantic, convertida a RecordID na store — precedente exato do `items` (`^distilled:...$`).

11. **`resolve_base_url`/`KUBO_BASE_URL` não morre com o loader.** Hoje é resolvido por
    `kubo/distribution/destinations.py`, que será aposentado no cutover (#123). A função migra
    para um módulo de config da distribution; segue `env` (é base de link da UI, não per-destino).

12. **Hard delete** só quando **zero dispatches** apontam para o destino, senão **arquiva** (§8 do
    ADR-0025, espelho #107). A checagem "zero dispatches" e o `DELETE` são **atômicos** — mesma
    transação (o runner envolve em `BEGIN;...;COMMIT;`, ADR-0007) ou `DELETE ... WHERE
    count(dispatches)=0` num só statement. Como `record<destination>` valida tipo e **não**
    existência (§5), um insert de dispatch concorrente entre uma checagem e um delete separados
    deixaria o link dangling (TOCTOU) — a atomicidade fecha a janela, espelhando o arquivamento
    atômico do #107. Probabilidade real baixa (digest agendado 1×/dia vs delete manual do dono
    solo), mas o custo de fechar é nulo. Dupla verificação na UI só para o hard delete (a única
    ação irreversível); pausar/arquivar/reativar são reversíveis → POST simples com CSRF. Detalhe
    da UI = #122.

13. **Escrita pela UI** segue o molde ADR-0018 / ADR-0025 §10: credencial `kubo_rw` EDITOR
    por-request + CSRF + guarda 409 de staleness; todo acesso via `kubo/store/`. (A tela em si é
    #122.)

14. **Ordem do cutover — a migration não derruba o YAML.** `destinations.yaml`, o loader e os
    consumidores (digest worker; report on-demand em `kubo/api/routes/flows.py:47`, que hardcoda
    `owner-telegram` resolvido contra o YAML) só migram/morrem quando o **último** consumidor
    apontar para o DB (#121/#123). A migration `0011` cria a tabela + faz o seed, mas **não**
    remove o YAML — senão abre uma janela de digest quebrado. Vale mesmo em dev.

**Guardrails do invariante 7** (a fronteira real, não "APScheduler é o motor"):
- **Teste do PR:** *canal* novo de entrega = código + PR + teste; *destino* novo (+1 e-mail) =
  dado. Mesma linha do invariante 3 aplicada ao registro de destinos.
- **Teste da expressão:** nenhum campo do destino é valor *avaliado como lógica*.
- **Frase-sinal:** *"o banco diz para-quem, o código diz como entregar, o relógio diz quando."*
  (O "quando" — agenda global no DB — é decisão do #119, não deste ADR.)

## O que sobrevive do ADR-0015

Este ADR mata o **§I** (E1: "destino não é tabela"), a **alternativa rejeitada (a)** e o **"sem UI
de escrita"** das consequências. **Permanece intacto** (e o build não pode matar por engano):

- **§III integral** — mecânica do watermark + reconciliação de microssegundos (§9 acima).
- **§IV quase inteiro** — `DispatchPayload`, o seam `distilled_for_digest`, entrega at-least-once,
  falha parcial, e — crítico — **Telegram/SMTP continuam integrações de catálogo**; a credencial
  nunca migra pro banco (§3).
- **§V só-se-novidade** e a **nota de timezone** (regra permanente do projeto).
- **ADR-0016 §V** (`dispatch.artifact`, §6 acima).

## Consequências

- **Positivo:** a dor morre (gerir destino pela UI, múltiplos destinos, canal e-mail); o modelo
  fica **uniforme** com o Cadastro de fontes (destino é um registro DB-backed com ciclo de 3
  estados, igual `source`); e o endereço PII passa a ter um lar honesto (plain, com obrigações
  escritas), sem o meio-caminho do `env:ref` por destino.
- **Trade-off aceito — histórico de `dispatch` apagado:** dado de dev, pré-lançamento; a tela de
  execuções/dispatches começa vazia. Reset do watermark → 1º digest no bootstrap 24h (§9).
- **Trade-off — assinaturas da store mudam:** `insert_dispatch`/`last_dispatch_watermark`/
  `distilled_for_digest` passam de string para `RecordID` no parâmetro `destination`. Cutover, não
  compatibilidade.
- **Cutover ordenado (§14):** os consumidores migram para o DB **antes** de o YAML morrer; a
  migration só cria+semeia.
- **Neutro:** a tela Destinos deixa de ser read-only (2ª superfície de escrita da UI depois de
  Fontes).

## Alternativas rejeitadas

- **`dispatch.destination` como string surrogate simples (em vez de `record<destination>`):** menos
  mudança de assinatura na store, mas `record<T>` valida tipo/tabela de graça e é o estilo da casa;
  sem back-compat, o custo do link forte é zero.
- **RELATION ENFORCED / aresta `delivered`:** ADR-0008 §VI e ADR-0015 já a rejeitaram; o guard
  "zero dispatches" na store basta contra dangling para um mantenedor solo.
- **Cifrar o endereço no banco:** chave em `env` no mesmo host = fachada; custo (crypto + rotação)
  sem ganho. **Gatilho de reabertura:** PII de **convidados externos em volume** / obrigação LGPD.
- **Bot token / SMTP password no banco:** são **segredo**, não endereço — env-only (invariante 8).
- **Partial unique index para ignorar arquivados:** o SurrealDB não tem; "arquivado segura o slot"
  (§4) é a semântica, resolvida por reativação.
- **Agenda por-destino:** fora de escopo — a agenda é **global** (decisão do #119).
- **Manter o `destinations.yaml` como fonte-de-verdade (modelo dual):** duas fontes-de-verdade que
  divergem — o pior dos mundos (lição do #108).

## Follow-ups nomeados (outros tickets do mapa #117)

- **#119** — config operacional global (agenda + pausa global): a **5ª** tabela extra-spec
  (`settings` singleton) + reação do scheduler. Este ADR e o do #119 somam a 4ª e a 5ª tabela na
  mesma reabertura → **cravam juntos ou em sequência consciente**, prestação de contas única da
  cláusula do ADR-0002.
- **#121** — "sweep de destinos" (um run por destino ativo; manifest por canal isola falha
  SMTP↔Telegram) + corpo de e-mail (o builder de digest hoje é Telegram-específico) + o
  `unpause × watermark` (§9).
- **#122** — UI de escrita de destinos + a UX de reativar-arquivado (§4).
- **#123** — cutover: aposentar `destinations.yaml` + loader + consumidores (§14).
- **#124** — abrir/confirmar a porta de saída do canal e-mail (OCI).
