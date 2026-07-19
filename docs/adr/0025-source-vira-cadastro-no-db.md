# ADR-0025 — `source` vira Cadastro no DB: registro gerível pela UI que dirige a coleta

> Status: **aceito** · Data: 2026-07-17 · Validado pelo advisor (Fable 5) antes do crave.
> Estende o modelo de `source` da spec §2.3; aposenta a lista de feeds do `schedules.yaml` (ADR-0010).

## Contexto

Hoje uma "fonte" é um registro de **runtime derivado da coleta**: a tabela `source`
(`kind`, `canonical` UNIQUE, `title`, `created_at` READONLY), com id `sha256(canonical)` e
upsert idempotente por `canonical`. Ela nasce como efeito colateral quando o worker `feed`
coleta o primeiro item de uma origem. **Quais** feeds coletar vive no `schedules.yaml` (6 URLs
RSS + cron); repos do GitHub são **auto-descobertos** da watch list do dono via `viewer.watching`
(github-releases, D57). Não existe caminho de criar/editar/deletar fonte — a tela Fontes é
read-only ("backend inexistente").

A dor: adicionar/renomear/remover uma fonte exige **editar YAML + deploy**. Num ateliê pessoal,
é papercut real. O pedido ("editar e deletar fontes na tela, com dupla verificação, cuidando
das associações") evoluiu, em grilling, para uma remodelagem: **a fonte deixa de ser efeito
colateral e vira um registro (Cadastro) que o dono gere pela UI e que dirige a própria coleta.**

Isso encosta no **escopo negativo (§1.2 / invariante 7)** — "sem workflow engine, sem
orquestrador pesado, sem autonomia total". O advisor foi taxativo: a versão certa **cabe dentro**
do invariante, não o estende; este ADR registra onde a linha está e prova que ficamos do lado
de dentro. Se o design *precisasse* estender o escopo negativo, o design estaria errado.

## Decisão

Remodelar `source` no Cadastro de fonte, com as escolhas abaixo. **Fatia contida (Fase A);**
ambições maiores adiadas com gatilho (ver Alternativas rejeitadas).

1. **Cadastro no DB com id próprio (surrogate)**, desacoplado da URL — editar a URL de origem
   não perde o histórico coletado.
2. **`canonical` único por `kind`** — sem fonte repetida. A unicidade é constraint da store
   (invariante 2), nunca "checagem soft na view". A migração **substitui** a `UNIQUE(canonical)`
   global atual por `UNIQUE(kind, canonical)`; como a composta é estritamente **menos**
   restritiva, nenhum registro existente conflita (não há resolução de conflito a fazer).
3. **O Cadastro dirige a coleta.** A **lista de fontes** sai do `schedules.yaml`; os jobs que
   não são fonte (distiller, digest, pipeline) permanecem no `schedules.yaml` enxuto — sempre
   foram operação (quando roda), não artefato (o quê existe).
4. **Sweep fixo:** um job de coleta em horário fixo varre os Cadastros **ativos** (`enabled=true`
   e não arquivados). O Cadastro carrega só `enabled`; **sem cron por-cadastro**. Preserva "um
   run = um feed" (ADR-0009): o sweep é um loop que dispara um run por Cadastro.
5. **Descoberta automática do GitHub: aposentada.** Repo vira **Cadastro comum** (`kind`
   `github-repo`), cadastrado à mão. O pipeline 07:00 deixa de descobrir e passa a só coletar
   releases dos Cadastros existentes. A lógica de `viewer.watching` renasce **depois**, num
   **worker de seed em massa** (produtor de Cadastros) — nomeado e adiado.
6. **Cadastro uniforme** — sem flag de proveniência (manual vs descoberto). Fontes diferem só
   pelo `kind`.
7. **Despacho por `kind` = mapa fixo em código** (`rss`→`feed`, `github-repo`→coletor de
   releases). Nunca nome-de-worker como campo livre do Cadastro.
8. **Deletar = soft archive** (`enabled=false` **e** `archived_at`), nunca cascade — proveniência
   é "o produto". Arquivar e restaurar atualizam os dois campos **atomicamente** — nunca deixam o
   Cadastro num estado divergente `archived_at` preenchido **com** `enabled=true` (arquivado-mas-ativo
   é nonsense); a store garante. Hard delete só quando **zero itens** apontam para o Cadastro
   (checagem na store): a store hoje não deleta em lugar nenhum; este hard delete estreito,
   condicionado a zero itens, é a primeira e única exceção. Dupla verificação na UI para a ação
   destrutiva.

   > **Emenda #107 (2026-07-18) — invariante de estado vira unidirecional.** A redação original
   > deste §8 declarava também `enabled=false` **sem** `archived_at` como divergente/inválido. Isso
   > foi escrito quando *desabilitar* e *arquivar* eram a mesma operação. O #107 (e a spec #103, US#7/
   > US#16) separam os dois eixos: o dono **pausa** um Cadastro (`enabled=false`) sem arquivá-lo. Logo
   > o modelo tem **três estados válidos** — ativo (`enabled=true`, `archived_at=NONE`), pausado
   > (`enabled=false`, `archived_at=NONE`) e arquivado (`enabled=false`, `archived_at` set) — e o
   > invariante que a store garante é só a direção `archived_at IS NOT NONE ⟹ enabled=false`.
   >
   > **Dupla verificação = só o hard delete** (a "ação destrutiva" singular deste §8). Pausar,
   > arquivar e restaurar são **reversíveis** (o par oposto desfaz cada um) → POST simples com CSRF.
   > Só o hard delete é irreversível e apaga o registro → tela interstitial de confirmação. Isto
   > estreita a redação do snapshot #103 **US#12** ("dupla verificação antes de arquivar/apagar"):
   > US#12 listava arquivar como destrutivo quando arquivar ainda ERA o delete; separado o eixo,
   > arquivar virou reversível e dispensa o 2º passo. O ADR é canônico sobre o snapshot (#103,
   > cabeçalho), então esta leitura vale.
   >
   > **Modalidade — SUPERADA pelo #108 (2026-07-18).** Esta emenda #107 dizia que o sweep era
   > "design, ainda não vivo" e que pausar/arquivar mudava o registro **sem interromper a coleta**
   > (com um helper text na UI dizendo essa verdade provisória). O #108 fechou o corte RSS: o sweep
   > está **vivo** e pausar/arquivar de fato para a coleta — a modalidade caducou, o helper foi
   > removido. Detalhes na **Emenda #108** ao fim deste ADR.
9. **Uma tabela só** — `source` *vira* Cadastro; sem coexistência/sincronização.
10. **Escrita pela UI** segue o molde ADR-0018: credencial `kubo_rw` EDITOR por-request + CSRF
    + guarda 409 de staleness. Todo acesso via `kubo/store/`.

**Guardrails do invariante 7** (testes binários; a fronteira real, não "APScheduler é o motor"):
- **Teste do PR:** *tipo* novo de coleta/descoberta = código + PR + teste; *instância* nova
  (+1 feed, +1 repo) = dado. Mesma linha do invariante 3 aplicada ao registro de fontes.
- **Teste da expressão:** nenhum campo do Cadastro é valor *parseado como lógica* (`stars>100
  AND lang==python`). Parâmetro tipado validado por pydantic = dado; expressão avaliada = DSL,
  proibido.
- **Teste do DAG:** nenhuma dependência entre jobs mora em dado ("roda X depois de Y"). A
  ordenação por horário fixo escolhido por humano permanece.
- **Frase-sinal de sucesso:** *"o banco diz o quê coletar, o código diz como, o relógio fixo
  diz quando."* No dia em que essa frase precisar de uma oração subordinada, passou da linha.

## Consequências

- **Positivo:** a dor original morre (gerir fonte pela UI, sem YAML+deploy); o modelo fica
  uniforme (RSS e GitHub são Cadastros que diferem só por `kind`); some a complexidade de
  proveniência/reconciliação; e tudo fica do lado de dentro do invariante 7, com guardrails
  escritos.
- **Trade-off aceito — dupla entrada temporária:** sem a auto-descoberta, um repo novo exige
  "watch no GitHub" **e** "cadastrar no Kubo", até o worker de seed em massa existir.
- **Trade-off aceito — pipeline 07:00 vira só-coleta:** a lógica `viewer.watching` sai agora e
  renasce no worker de seed (nada se perde, muda de casa).
- **Custo real — migração de dados de produção:** os 6 feeds + os `source` do GitHub já
  coletados + as arestas `from_source` migram para o modelo novo (mecânica ADR-0007). É o
  pedaço mais delicado do build. **Nota de escopo:** os ids `sha256(canonical)` existentes são
  opacos e podem ser preservados; "surrogate" exige apenas que ids *novos* não derivem da URL e
  que nada trate o id como hash dela — reescrever ids e re-apontar arestas `from_source` **não**
  é exigência deste ADR. **Ordem de rollout** (não interromper a coleta): (1) criar e validar o
  job de sweep varrendo os Cadastros ativos; (2) migrar as 6 entradas de feed + os `source` do
  GitHub já coletados para Cadastros; (3) só então remover os feeds do `schedules.yaml` e validar
  que o scheduler não depende mais do formato antigo — os jobs operacionais não-fonte permanecem.
  **RSS: colapsado num corte só pelo #108 (ver Emenda #108)** — os 3 passos permanecem a ordem
  correta em geral; a precondição que legitimou o colapso (os 6 já eram Cadastros ativos) foi
  verificada empiricamente, não presumida. GitHub segue os 3 passos no #110.
- **Neutro:** a tela Fontes deixa de ser read-only; primeira superfície de escrita da UI além
  dos gates de fluxo.

## Alternativas rejeitadas

- **`canonical` não-único (fonte repetida com histórico separado):** custo real (coleta e LLM
  2×, "a qual cópia pertence este item?", integridade na view) sem caso presente. O benefício
  que o dono queria (editar URL sem perder histórico) já vem do id surrogate. **Adiado**, gatilho:
  necessidade *presente* de duas fontes no mesmo `canonical` com históricos separados.
- **Modelo genérico de "regra de descoberta":** generalizar de N=1 (só o github-releases existe)
  produz a abstração errada e ressuscita a tensão de DSL. **Adiado**, gatilho: uma segunda regra
  de descoberta concreta na mesa.
- **Cron por-cadastro (cadência individual):** agendamento dinâmico dirigido por dado é o cheiro
  de engine; nenhum feed hoje pede ritmo diferente. **Adiado**, gatilho: uma fonte que precise
  de cadência comprovadamente distinta.
- **Manter o `schedules.yaml` como fonte-de-verdade dos feeds (modelo dual):** duas
  fontes-de-verdade que divergem — o pior dos mundos.
- **Estender o invariante 7 para uma camada de orquestração declarativa:** rejeitado — a versão
  certa cabe dentro do escopo negativo; estender seria sinal de design errado.

## Follow-ups nomeados (dívida com gatilho)

- Worker de seed em massa de Cadastros (renasce o `viewer.watching`). Sem ele, dupla entrada
  manual para repos do GitHub.
- Fase B (as três alternativas adiadas acima), cada uma com seu gatilho empírico.

## Emenda #108 (2026-07-18) — corte RSS num só passo; sweep vivo

O #108 entregou o **sweep dirigido por Cadastro para RSS** e, ao fazê-lo, **absorveu o corte RSS
do #109** (migração das 6 fontes + remoção da lista estática do `schedules.yaml`) num único PR. O
que mudou concretamente:

- **Sweep vivo:** uma `SweepEntry` (`sweep: rss`, cron `0 8 * * *`) no `schedules.yaml` varre os
  Cadastros rss ativos (`enabled=true` E `archived_at IS NONE`, via `active_sources`) e dispara um
  run por Cadastro (`SWEEP_DISPATCH` fixo em código, `rss`→`feed`; despacho por kind, decisão 7).
  Preserva "um run = um feed" (ADR-0009): o sweep é `query → loop → run_worker`, isolado por
  Cadastro. Não é engine (guardrails da decisão 4 intactos: sem cron/worker por-Cadastro no banco,
  sem retry/estado/DAG).
- **Lista de feeds sai do YAML:** as 6 entradas `worker: feed` foram removidas; o `schedules.yaml`
  fica só com operação (sweep + distiller + digest + pipeline). A migração dos dados é o seed
  `python -m kubo.store.seed` (passo de deploy irmão das migrations), idempotente e **não-destrutivo**
  (coalesce: `title ?? $title`, `enabled ?? true`, `tags` só se vazio) — completa `tags`/`title`
  nos 6 records legados sem reverter edições/pausas do dono (#106/#107 estão vivos).
- **Fim do caveat de honestidade:** o helper text da tela Fontes e a "Modalidade" desta emenda #107
  foram removidos — pausar/arquivar de fato para a coleta.

**Por que colapsar os 3 passos do rollout num só (a decisão desta emenda):** a ordem original
existe para *não interromper a coleta* provando o sweep antes de remover a rede estática. A
precondição que a torna desnecessária — os 6 feeds **já serem Cadastros rss ativos com canonical
e título corretos** — foi **verificada ao vivo no kubo-test** (não presumida). Com o dado já
migrado de fato (efeito colateral da coleta histórica), a "rede" das entradas estáticas é
substituída por duas garantias mais fortes: o **seed** garante o DADO (6 Cadastros completos)
antes do 1º run; o **smoke físico owner-gated** garante o CÓDIGO (sweep coleta + pausar tira da
coleta) antes de confiar no cron, e o corte só se declara concluído após a **1ª run por cron**
(verificação D+1: 6 runs em Execuções). Em ambiente **sem** essa verificação empírica, a ordem de
3 passos continua sendo a correta.

**Alternativa rejeitada (nesta fatia):** *estado misto* — ativar o sweep **junto** com as 6
entradas estáticas vivas (coleta dupla como rede provisória). Rejeitada pelo dono: cria um estado
em que pausar um feed legado não o interrompe (a entrada estática ignora o estado) enquanto pausar
um feed novo da UI interrompe — um helper text não descreve honestamente esse meio-termo. O dono
pediu explicitamente "uma solução limpa e única". O corte único entrega isso: **uma
fonte-de-verdade (o DB), um coletor (o sweep), um estado honesto.**

**Consequências de tickets:** o **#109** (migrar feeds + aposentar a lista) fecha como **absorvido**
por este corte; o **#110** (github-repo → coletor de releases) segue **intacto** e adiciona a chave
`github-repo` ao `SWEEP_DISPATCH` reconciliando com o pipeline 07:00 — o `github-repo` continua
fora do sweep até lá (validação eager de `_add_sweep_job` barra uma `SweepEntry` de kind não
mapeado).

---

## Emenda #110 (2026-07-18) — GitHub vira Cadastro; pipeline 07:00 vira sweep `github-repo`

Fecha o §5 desta ADR (descoberta automática do GitHub aposentada) e o §7 (despacho por `kind`).
Validada pelo advisor (Fable 5) em duas consultas antes do código.

1. **Auto-descoberta `viewer.watching` APOSENTADA.** O worker `github-releases` era MULTI-repo:
   descobria ~261 repos da watch list do dono via GraphQL (`viewer.watching`, D57) e coletava
   releases de cada um num único run. Todo o cliente GraphQL, a paginação por cursor, o teto de
   páginas e o `_RUN_DEADLINE` (que existiam só pra processar centenas de repos num run) se
   aposentaram. O worker virou **single-repo** (`GithubReleasesConfig = {repo, since}`), simétrico
   ao `FeedWorker` (`feed_url`).

2. **Despacho por `kind` = sweep.** `SWEEP_DISPATCH` ganha a chave `github-repo` →
   `GithubReleasesWorker` (o mapa fixo kind→worker+config-builder do #108). O `schedules.yaml`
   troca `flow: pipeline` (07:00) por `sweep: github-repo` (07:00): o sweep varre os Cadastros
   `github-repo` ativos e dispara **um run por repo** — "um run = um Cadastro" (§4), igual ao RSS.

3. **`since` = `created_at` do Cadastro (D2).** O RSS não tem config operacional (`SweepEntry` sem
   `config`). O GitHub precisa de um piso temporal, mas colocá-lo na entry devolveria config
   operacional ao `schedules.yaml` (o que o #108 removeu). A escolha: o sweep deriva
   `since = created_at` de cada Cadastro (`ActiveSource` ganha o campo). Consequências: (a) a dívida
   "since congelado PARA SEMPRE" do `schedules.yaml` **morre** — o piso é per-repo e nasce do
   cadastro; (b) honra o D52 (sem backfill na estreia) naturalmente — repo cadastrado hoje só coleta
   releases publicadas a partir de hoje; (c) `since` deixa de ser watermark — quem avança de run
   pra run é a idempotência do `upsert_item` por `external_id`. Caveat de produto aceito: um release
   publicado ANTES do cadastro (mesmo o que motivou o cadastro) não entra.

4. **Migração 0010 — reconciliação, não flip cego.** A UI (#105) já cria Cadastros
   `kind="github-repo"` (canonical `https://github.com/owner/name`), enquanto o worker gravava
   `kind="github-releases"` na MESMA canonical como efeito colateral da coleta — **dois records
   distintos** por repo sob `UNIQUE(kind, canonical)`. A migração reconcilia: o **sobrevivente é o
   record `github-releases`** (é ele que tem as arestas `from_source`/`collected_by`); o twin
   `github-repo` da UI é edge-less por construção. Onde há twin: copia `title/tags/enabled/
   archived_at` do twin → sobrevivente (não perde pausa/arquivamento que o dono fez pela UI),
   `DELETE` do twin, flip `kind='github-repo'` no sobrevivente. Onde não há twin: só flip. **Zero
   manipulação de aresta** (o id do sobrevivente não muda), `created_at` READONLY preservado
   (nenhum re-backfill na 1ª varredura). Idempotente (`WHERE kind='github-releases'` esvazia).
   Cadastros `github-repo` SEM twin de coleta (repo cadastrado e nunca coletado) são poupados.

5. **Integração: `github-watch` → `github-readonly`.** Sem descoberta, o worker só lê
   `/repos/{owner}/{repo}/releases` (público, leitura pura). O PAT dedicado `github-watch`
   (escopo `notifications`, criado no D54 só para `viewer.watching`) perdeu a justificativa de
   least-privilege que o criou; o worker volta ao `github-readonly`/`GITHUB_TOKEN_READONLY` (o
   mesmo do rito de promoção, também leitura pura — **sem** alargamento de escopo, ao contrário do
   que o D54 evitava, porque a descoberta que exigia o escopo extra não existe mais).
   `catalogs/integrations/github-watch.yaml` e o env `GITHUB_TOKEN_WATCH` foram aposentados.

6. **Rollout (dono):** a migração 0010 converte **todo** `source` `github-releases` já coletado em
   Cadastro `github-repo` **ativo** — cada um vira 1 run/dia no sweep (aparece em Execuções). O dono
   poda os indesejados pela UI (#107 pausar/arquivar). Daqui pra frente, repo novo só à mão (#105).
