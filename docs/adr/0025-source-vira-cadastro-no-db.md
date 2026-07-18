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
   > **Modalidade — o sweep é design, ainda não vivo (dívida sequencial).** A decisão 4 ("o sweep
   > varre só os ativos") descreve o comportamento-alvo, **não o presente**. Até o sweep por Cadastro
   > existir (#108, passo 1 do rollout), `enabled`/`archived_at` são **estado registrado pela store e
   > exibido pela UI — ainda não honrado por coletor nenhum**: a coleta permanece dirigida pelo
   > `schedules.yaml` (6 feeds) + watch-list do GitHub, que o #107 não tocou. Logo pausar/arquivar
   > pela tela hoje muda o registro e a listagem, **não interrompe a coleta** — a interrupção chega
   > com o #108. A UI diz essa verdade num helper text (a ser removido quando o #108 fechar); a ordem
   > de entrega (schema→create→edit→lifecycle→sweep) inverteu a ordem de rollout deste ADR, o que é
   > seguro porque nenhuma dessas fatias encostou no caminho de coleta (achado do security-reviewer).
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
