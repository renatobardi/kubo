# ADR-0009 — Contrato de worker

> Status: **aceito** · Data: 2026-07-05

## Contexto

O contrato de worker (spec §3.3) é a fronteira que torna código gerado por agente **deployável por construção**: o runtime não confia em quem escreveu o worker — valida o contrato e persiste ele mesmo o resultado. Este ADR formaliza a decisão **D4** (runtime persiste, ctx read-only escopado, idempotência por chave natural, sem fila de retry, `schema_version` inteiro no manifest) e absorve as **regras anti-injection de D6** como obrigações do contrato. É a materialização do invariante 6 do CLAUDE.md.

O M4 implementa o contrato + o runner mínimo + o worker fake ponta a ponta. O M5 troca o fake pelo `feed` real; o M6 traz a destilação (LLM nas junções) e a mecânica de prompt. Este ADR decide a **forma do contrato**, não a mecânica de LLM (ver "O que este ADR não decide").

## Decisão

### I. O Protocol (o que o pyright vê)

```python
class Worker(Protocol):
    manifest: WorkerManifest
    def run(self, ctx: RunContext) -> RunResult: ...
```

O nome do contexto é `RunContext` (spec §3.3 é fonte de verdade; o plano 4.2.2 segue a spec). Todos os modelos do contrato usam `model_config = ConfigDict(extra="forbid", revalidate_instances="always")` — porque o default do pydantic é ignorar campo extra em silêncio, e numa fronteira de segurança isso fura a regra 2 de D6 (um worker que emite campo com nome errado teria o dado descartado sem erro; a "validação antes de persistir" viraria meia-verdade). `forbid` faz a validação ser um gate de verdade. Campos que mapeiam para colunas FLEXIBLE (`ItemPayload.metadata`, `ErrorInfo.detail`) continuam `dict[str, Any]` abertos — o `forbid` é na forma do payload, não no conteúdo do dict.

**`revalidate_instances="always"` fecha o bypass por instância pré-montada:** por default o pydantic NÃO revalida uma instância de modelo passada a `model_validate` (`revalidate_instances="never"`). Sem isso, um worker que devolve `RunResult.model_construct(...)` (que pula a validação) com `Stats` hostil atravessaria a "validação antes de persistir" intacto — a mesma classe de adversário do TOCTOU (item V) que o contrato já protege. Com `always`, o `RunResult.model_validate(raw_result)` do runner revalida a instância e seus modelos aninhados, rejeitando o bypass. `Stats` também o carrega (item IV) — permissivo nos nomes, mas revalidado.

O Protocol serve à checagem estática do pyright. **NÃO** é a validação de runtime — `@runtime_checkable`/`isinstance` só checam presença de membros, não a forma do manifest nem a assinatura de `run` (falsa validação). A validação de runtime é a função explícita `validate_worker` (item V).

### II. Manifest — `type[BaseModel]` para o schema de config, não JSON-schema

```python
class WorkerManifest(BaseModel):
    name: str
    version: str
    schema_version: Literal[1] = 1     # inteiro; a versão do CONTRATO, não do worker
    integrations: list[str] = []       # nomes declarados; least-privilege (item VII)
    config: type[BaseModel]            # o schema de config é uma CLASSE pydantic
```

O schema de config é uma **classe pydantic** (`type[BaseModel]`), não um dict JSON-schema. Motivo: o runtime valida a config instanciando a classe (`manifest.config.model_validate(data)`) e entrega ao worker uma **instância tipada** — sem camada de tradução JSON-schema→validação. Exportar para JSON-schema (para uma futura UI de fase 4) é `Model.model_json_schema()`, problema de quem construir a UI, não do contrato.

`schema_version` é a versão do **contrato** (esta ADR = 1). Um bump só acontece se a forma de `Worker`/`RunResult` mudar de modo incompatível — é o gatilho de migração de workers portados, não um número de release do worker (esse é `version`).

Armadilha assumida do `type[BaseModel]`: `WorkerManifest.model_dump_json()` **falha** (uma classe não é JSON-serializável) — quem for logar/persistir o manifest usa `exclude={"config"}`. E `ctx.config` é tipado `BaseModel` para o pyright; o worker concreto faz o narrowing (`assert isinstance(ctx.config, FeedConfig)`). É o custo de rejeitar generics, e é o custo certo.

### III. RunResult — união discriminada de payloads que espelham a store 1:1

O worker **devolve dados tipados**; o runtime persiste. Os payloads espelham 1:1 as assinaturas de escrita de `kubo/store/knowledge.py` — não há tradução, o runner faz match explícito tipo→função da store (item VI do runner, ADR do runtime).

```python
class SourcePayload(BaseModel):
    type: Literal["source"] = "source"
    kind: str                          # espelha upsert_source(kind=…)
    canonical: str
    title: str | None = None

class ItemPayload(BaseModel):
    type: Literal["item"] = "item"
    source: SourcePayload              # chave natural da source embutida inline
    external_id: str                   # chave natural do item = source + external_id (D4)
    content: str
    url: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None

Payload = Annotated[SourcePayload | ItemPayload, Field(discriminator="type")]

class RunResult(BaseModel):
    payloads: list[Payload] = []
    stats: Stats = Stats()
    error: ErrorInfo | None = None
```

**Por que a source vai embutida no item (não como payload separado obrigatório):** o runner faz upsert da source antes do item; como todo upsert da store é idempotente por chave natural, repetir a source em cada item é gratuito. Um worker pode emitir um `SourcePayload` avulso (registrar uma fonte sem itens novos) — por isso a union tem os dois membros — mas o caminho comum do `feed` (M5) é emitir N `ItemPayload`, cada um carregando sua source.

**Sem generics.** `RunResult[T]` é proibido — a união discriminada (`Field(discriminator="type")`) resolve o mesmo problema sem a complexidade de tipos paramétricos que brigaria com o pyright strict. O discriminador é o campo `type` (não `kind`, que já é o tipo-de-canal da source).

**Fatia M4:** só `SourcePayload` + `ItemPayload` (o que o `feed` do M5 consome). O payload de `distilled` é M6 (novo membro da união, aditivo).

### IV. Forma de `ErrorInfo` e `Stats` (alinha `run.error` / `run.stats`)

```python
class ErrorInfo(BaseModel):
    kind: str                          # categoria de domínio: "http", "parse", "worker_exception"
    message: str                       # legível; NUNCA conteúdo coletado (item VIII)
    detail: dict[str, Any] | None = None

class Stats(BaseModel):
    model_config = ConfigDict(extra="allow")   # contadores livres; envelope tipado sobre run.stats
    # model_validator: cada valor extra DEVE ser int|float — rejeita o resto.
```

`ErrorInfo.model_dump()` serializa para `run.error` (campo `option<object> FLEXIBLE` da migration 0001); `Stats.model_dump()` para `run.stats` (`object FLEXIBLE DEFAULT {}`). Nenhum dos dois exige migration — os campos já são flexíveis; o contrato só crava a forma que o modelo pydantic escreve dentro deles. `Stats` é um envelope tipado (pydantic na borda) permissivo nos **nomes** dos contadores, porque cada worker conta métricas próprias — mas um `model_validator` rejeita valor extra que não seja `int`/`float`. Isso não é prosa: torna `Stats` incapaz **por construção** de carregar conteúdo coletado (string/objeto) para dentro de `run.stats` ou do log, fechando por tipo a obrigação transversal do item VIII em vez de por disciplina.

### V. `validate_worker` — a validação de runtime é uma função explícita

```python
def validate_worker(obj: object) -> WorkerManifest:
    """Valida que obj honra o contrato; retorna o manifest ou levanta ContractError."""
```

Checa: (a) `obj.manifest` existe e é validável como `WorkerManifest` (pydantic); (b) `obj.run` é callable com a assinatura esperada (um parâmetro posicional além de `self`). Falha → `ContractError` (novo em `kubo/errors.py`). O runner chama `validate_worker` **antes** de abrir o `run` — worker inválido nunca executa.

**A validação retorna o manifest VALIDADO, e o runner usa o retorno — nunca relê `obj.manifest`.** Um objeto hostil pode expor `manifest` como property que devolve coisa diferente na segunda leitura (TOCTOU barato); o escopo de integrações (item VII, montagem do ctx) deriva da cópia validada, não de uma releitura do atributo.

### VI. Contexto read-only escopado (a forma; o concreto é do runtime)

O worker recebe um `RunContext` **somente-leitura**: `config` (instância validada do schema do manifest), `integrations` (só as declaradas ∩ catálogo, com segredo já resolvido pelo runtime), `knowledge` (seam de leitura do grafo — **vazio** na fase 1: o `feed` não lê o grafo, a idempotência elimina o "já coletei?"), `logger` (bound com `run_id`/`worker`). **O worker nunca recebe handle de `db`** — persistir é do runtime. Expor `db.query` "somente leitura" é proibido: não há como escopar leitura arbitrária com segurança.

O seam `knowledge` vazio **não é campo morto** (que o ADR recusa para o slot de LLM) — ele materializa em código a alternativa rejeitada (d): a declaração de que leitura do grafo passa por AQUI e nunca por handle de `db`. Métodos entram quando um worker exigir leitura, com teste que o justifique — aditivo a um Protocol vazio.

### VII. Idempotência e ausência de retry (D4)

Idempotência é por **chave natural** (source: canonical; item: source+external_id), via UPSERT determinístico da store — não há get-or-create com corrida. Consequência direta: **sem fila de retry na fase 1**. Uma execução parcial que falhou no meio deixa os itens já gravados no lugar; a re-execução é idempotente e "cura" o resto. Persistência é **por-item**, cada upsert já atômico — o runner não envolve o run inteiro numa mega-transação (detalhe no ADR do runtime).

**Semântica de `RunResult` com `payloads` E `error`:** o runtime persiste os payloads entregues e **depois** fecha o run como erro (`fail_run`) — coerente com "falha parcial deixa itens gravados, re-execução cura". Na fronteira, **exceção não capturada pelo worker e `error` retornado são equivalentes**: ambos viram `run.error` e fecham o run em `error`; um `RunResult` sem `error` e sem exceção fecha em `ok` (`finish_run(stats=…)`).

**Repetição de `canonical` na mesma run é last-write-wins, sem cross-check:** dois `ItemPayload` com o mesmo `canonical` mas `title`/`kind` divergentes → o upsert grava o último. Para o `feed` (uma run = um feed = source consistente) isso não ocorre na prática, então NÃO se adiciona detecção de conflito (complexidade sem consumidor) — mas fica declarado para deixar de ser ambiguidade.

### VIII. Regras anti-injection (D6) como obrigações do contrato

Conteúdo coletado é hostil por padrão (prompt injection via conteúdo coletado é ameaça de primeira classe do projeto). As quatro regras de D6 são **obrigações do contrato**; parte é enforce agora, parte é mecânica de prompt adiada para o M6:

1. **LLM sobre conteúdo coletado nunca tem tools.** — Mecânica de M6 (não há LLM no ctx ainda). Registrada como obrigação.
2. **Saída estruturada validada por schema antes de persistir.** — **Enforce agora:** o runtime valida o `RunResult` (pydantic) antes de qualquer escrita; payload malformado é rejeitado, não persistido.
3. **Conteúdo coletado demarcado como untrusted no prompt.** — Mecânica de M6.
4. **Conteúdo coletado nunca flui para executor `cli` sem gate humano.** — Executor `cli` é fase 3; registrada como obrigação a honrar lá.

Obrigação transversal, **enforce agora**: o **logger de worker NUNCA loga payload coletado** — nem `content`, nem `metadata`, nem `ErrorInfo.message`/`Stats` derivados de conteúdo. O logger carrega `run_id`/`worker`/contadores, não corpo coletado.

**O runtime honra isso no próprio código de captura (item 4.2.3):** ao construir `ErrorInfo` a partir de uma exceção do worker, `message` é **truncado** (teto 500 chars) e nunca embute repr de payload. Motivo concreto: `str(exc)` de um erro de parse costuma conter o trecho de conteúdo que quebrou o parser — o caminho de exceção é justamente por onde conteúdo coletado hostil vazaria para `run.error` e para o log. O `detail` estruturado existe para o diagnóstico que não cabe na mensagem.

**Reforços cravados na revisão de segurança do M4 (fechar por construção, não por disciplina):**
- **`ErrorInfo.message` tem `Field(max_length=500)`** — o teto vale também quando o worker RETORNA o erro (não só quando o runtime o constrói do exception): conteúdo coletado longo no `message` é rejeitado por tipo.
- **`ResolvedIntegration.secret` é `field(repr=False)`** — o segredo resolvido nunca aparece em `repr`/`str`/traceback. Fecha o canal em que um worker faz `raise RuntimeError(ctx.integrations[x])` e o valor cairia em `run.error` via `str(exc)`.
- **Mensagens de `ValidationError` nunca são propagadas com `str(exc)`** através da fronteira (loader, runner, `validate_worker`): usa-se `errors(include_input=False)`, porque `str(ValidationError)` embute o `input_value` inteiro (que carregaria conteúdo coletado ou um segredo colado por engano). E o validador de `IntegrationAuth` **não ecoa** o `secret_ref` candidato na mensagem.
- **`_persist` roda DENTRO da fronteira try/except do runner** — uma falha de store no meio da persistência fecha o run em erro estruturado (não o deixa travado em `running` nem propaga exceção crua).

## O que este ADR não decide

- **Mecânica de LLM no ctx** (onde entra o cliente LiteLLM): M6. **Não se cria campo morto agora** — o slot é esta frase, não um atributo `llm=None` no ctx. Adicionar o campo quando o primeiro worker destilador exigir é trivial.
- **Mecânica das regras 1 e 3 de D6** (no-tools, demarcação untrusted no prompt): ADR do M6, que emenda este se precisar (padrão de emenda do ADR-0006).
- **Payload de `distilled`** e a extração de entidades/chunks: M6 (novo membro da união).
- **Métodos do seam `knowledge`**: entram quando um worker exigir leitura do grafo, com teste que o justifique — não especular agora.
- **Registro de worker no catálogo e rito de promoção** (spec §3.4): fase posterior; aqui só o contrato que a promoção valida.

## Consequências

- **Código gerado por agente é validável mecanicamente:** `validate_worker` + validação de `RunResult` são o portão que o gate humano (invariante 5) complementa, não substitui.
- **A store é a única superfície de escrita:** o worker não conhece SurrealQL nem RecordID; o runner traduz payload→função da store. Isso mantém o invariante 2 (todo acesso a banco por `kubo/store/`) mesmo com workers de terceiros.
- **Evolução é aditiva:** novos tipos de payload entram como membros da união discriminada; `schema_version` só muda em quebra incompatível da forma do contrato.
- **`finish_run` ganha `stats` no M4** (carry-over do 0003): o worker fake com métricas é o primeiro consumidor. Sem migration (campo FLEXIBLE).

## Limites conhecidos do v1

- **O contrato valida forma, não intenção.** `validate_worker` + validação de `RunResult` garantem que o worker honra a **forma** do contrato — não que ele seja benigno. O worker roda **in-process, não em sandbox**: contra código *malicioso* (vs. apenas *desleixado*), os controles são o **gate humano na promoção** (invariante 5) + review, não `validate_worker`. O ADR vende "deployável por construção" no sentido de *forma validável mecanicamente*, não de *confiança automática*.
- **O contrato não escopa ESCRITA.** Permissões de integração escopam o que o worker *lê/acessa* (least-privilege, item VII), mas qualquer worker validado pode upsertar qualquer `source`/`item` — o upsert por chave natural permite um worker sobrescrever registros que outro criou. Na fase 1 (workers built-in + gate humano na promoção) é aceitável; escopar escrita por worker é decisão de fase posterior, se um consumidor a exigir.

## Alternativas rejeitadas

- **(a) Schema de config como dict JSON-schema** — rejeitada: exigiria uma camada de validação JSON-schema no runtime quando o pydantic já valida instanciando a classe. JSON-schema é problema de export (fase 4), não do contrato.
- **(b) `RunResult[T]` genérico** — rejeitada: união discriminada resolve o mesmo sem tipos paramétricos que brigam com pyright strict.
- **(c) `isinstance`/`@runtime_checkable` como validação** — rejeitada: só checa presença de membros, não a forma do manifest nem a assinatura de `run`. Falsa validação de uma fronteira de segurança.
- **(d) Worker recebe handle de `db` (leitura)** — rejeitada: não há como escopar `db.query` arbitrário com segurança; leitura do grafo é o seam `knowledge`, vazio até um worker exigir.
- **(e) Persistir o run inteiro numa mega-transação** — rejeitada: cada upsert já é atômico; falha parcial + idempotência é mais simples e é justamente o que dispensa a fila de retry.
