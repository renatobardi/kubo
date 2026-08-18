# ADR-0054 — Camada de configuração de LLM: três portas, registry, persona de sistema, BYOK

> Status: **aceito** · Data: 2026-08-17
> Emenda a especificação funcional §2.4; emenda os ADR-0010, ADR-0013, ADR-0016, ADR-0019, ADR-0039 §IV e ADR-0042; preserva o ADR-0006 e o ADR-0009.

## Contexto

Hoje cada parte do Kubo que fala com um modelo decide sozinha — ou não decide, e crava constantes. Os sintomas estão espalhados:

- O destilador usa `_DISTILLER_MODEL` e `_DISTILLER_MAX_TOKENS` em `kubo/scheduler/__init__.py`.
- Os consumidores de Estudos carregam `_SUMMARY_MAX_TOKENS`, `_MENTOR_MAX_TOKENS`, `_PLANNER_MAX_TOKENS`, `_SECTIONIZER_MAX_TOKENS`, `_TUTOR_MAX_TOKENS` em `kubo/api/routes/study.py` e `kubo/scheduler/`.
- O analista em `kubo/workers/analyst.py` traz seu próprio teto.
- O executor de API inspeciona prefixo do nome do modelo (`anthropic/...`) para decidir se manda `temperature`.
- O embedding em `kubo/embedding.py` é a tripla `(gemini-embedding-001, 768, SEMANTIC_SIMILARITY)` cravada em código, com `GEMINI_API_KEY` vindo só de env.
- A chave de API do executor LiteLLM (`api_key` em `ApiExecutorConfig`) aceita `None` e cai no env do processo — `tenant_credential` existe, é cifrada e testada, mas não é usada para LLM.

O `CONTEXT.md` já afiou o vocabulário da **Camada LLM** para esta mudança: **Ponto de contato LLM**, **porta de saída**, **Persona de sistema**, **Persona de cast**, **Registry de modelos** e **Campo travado**. Este ADR consolida as decisões que fazem esse vocabulário valer no banco e no código.

Este ADR é a base do épico [KUBO-218](https://oute.atlassian.net/browse/KUBO-218). Os tickets-filho aprovam as fatias de build a partir daqui.

## Decisão

### I. Unificação de configuração, não de roteamento

Existem três **portas de saída** e continuam três:

| Porta | Mecanismo | Onde vive hoje |
|---|---|---|
| `api` | LiteLLM / completion | `kubo/executors/api.py` |
| `cli` | Claude Agent SDK / subprocess agêntico | `kubo/executors/cli.py` |
| `embedding` | REST direto ao provider de vetores | `kubo/embedding.py` |

A diferença é mecânica, não cosmética: um loop de agente com tools não é uma completion, e vetor não é texto. Forçar as três por LiteLLM reabriria o ADR-0013 §I e o ADR-0019 §I; o ganho não paga o custo. O que se unifica é a **fonte da configuração**: as três portas leem do mesmo resolvedor (`kubo/llm/resolver.py`), nenhuma escolhe modelo ou parâmetro por conta própria.

**Emenda à especificação funcional §2.4:** a tabela de executores passa a listar três portas (`api`, `cli`, `embedding`), não duas. A terceira porta é o caminho de vetores, configurável como persona de sistema (§IV).

### II. Persona é a unidade de configuração

Todo consumidor de LLM é uma **persona de cast** (papel a quem tasks são atribuídas) ou uma **persona de sistema** (ação do sistema, fora de cast). A persona carrega o modelo e os parâmetros de chamada:

- `model` — nome no formato LiteLLM (ex.: `anthropic/claude-opus-4`, `groq/llama-...`, `gemini-embedding-001`).
- `max_tokens` — teto de tokens de saída.
- `temperature` — amostragem, quando suportado.
- `reasoning_effort` — esforço de raciocínio, quando suportado.
- `timeout` — teto de espera da chamada.
- `max_turns` — só para `cli`, limite de turnos do agente.
- `api_key_ref` — referência a `tenant_credential:<provider>` (BYOK, §V).

A tabela `catalog_persona` (ADR-0042) ganha esses campos. Nenhum call site de LLM passa a ter constantes de modelo/tokens; o resolvedor entrega a config congelada para a request ou run (§VII).

### III. Registry de modelos é fato sobre o provedor, não catálogo

O **Registry de modelos** vive em código (`kubo/llm/registry.py`), idêntico em todo tenant e ambiente. Ele responde: "este `model` aceita `temperature`? `reasoning_effort`? `top_p`?".

- Não é um quarto catálogo — a escolha de capacidade é sobre o mundo (o que o provedor/modelo suporta), não sobre preferência de tenant. Colocar isso no catálogo permitiria que dois tenants dissessem coisas contraditórias sobre o mesmo modelo e quebrasse a chamada de um deles.
- A função pública é `get_capabilities(model: str) -> ModelCapabilities`. Modelo ausente do registry recebe o **mínimo seguro** (`supports_temperature=False`, `supports_reasoning_effort=False`, ...), nunca falha. Chamadas a modelos novos funcionam com `model` + `max_tokens` + `timeout`, sem depender de PR.
- O executor de API deixa de inspecionar prefixo (`_supports_sampling` em `kubo/executors/api.py`) e passa a consultar o registry.
- Quando um parâmetro configurado é omitido por não ser suportado, o resolvedor loga estruturado (`parameter`, `model`, `reason`), para depuração de "configurei e não mudou nada".

### IV. Persona de sistema e campos travados

A **persona de sistema** é uma linha obrigatória de `catalog_persona`, sem `cast` e sem task atribuída. Hoje a única é a do embedder. Ela pode declarar **campos travados**: o valor é visível, mas não editável, porque outra camada pinou a decisão.

- `embedder.model` é travado porque a dimensão do vetor está pinada no schema do banco (ADR-0006, tripla `(gemini-embedding-001, 768, SEMANTIC_SIMILARITY)`). Trocar o modelo sem re-embedar tornaria incomparáveis os vetores já gravados. A UI exibe o campo com uma explicação de por que está travado.
- O campo travado não é `read-only` genérico: a UI toda é read-only por default; "travado" é uma decisão de dado, não um estado de interface.
- Campos configuráveis que não se aplicam ao modelo escolhido não são escondidos — são mostrados com aviso de que não terão efeito, para evitar que o dono acredite numa configuração inerte.

### V. BYOK obrigatório

Toda chamada de LLM (todas as três portas) usa chave do **tenant**. A chave vem de `tenant_credential` (ADR-0039 §IV), resolvida pelo prefixo do provedor no nome do modelo (ex.: `anthropic/claude-opus-4` → provider `anthropic` → `tenant_credential:anthropic`).

- Não há fallback para a chave de ambiente do processo. O `ApiExecutorConfig.api_key` continua existindo, mas é preenchido sempre com a credencial do tenant; `None` deixa de ser aceito em produção.
- No `cli`, a chave do provider passa pelo `options.env` da whitelist (ADR-0019 §IV) como `ANTHROPIC_API_KEY=<tenant>`, sem cair de `os.environ` do pai. O provider no nome do modelo decide qual `tenant_credential` levar.
- No embedding, o `x-goog-api-key` passa a ser a chave do tenant para `google`/`gemini`.
- Uma chave cadastrada nunca volta legível para a interface: `tenant_credential` armazena valor cifrado; a UI vê só a existência do provider, nunca o segredo.
- Erro quando falta a credencial: mensagem dizendo qual `provider` falta, para o dono saber o que cadastrar.
- Tenant novo é obrigado a cadastrar ao menos uma credencial de provedor no onboarding, senão nasce inutilizável.

**A única mudança de comportamento em produção é a obrigatoriedade do BYOK.** Toda a mecânica de chamada (timeout, retries, tratamento de erro, chunking, embedding) preserva comportamento exato; o que muda é de onde a chave vem.

### VI. Sem eixo "por ambiente" no schema

DEV e PRD já têm bancos SurrealDB fisicamente separados. As linhas de `catalog_persona` de cada ambiente já são objetos distintos. Não se adiciona campo `environment` no catálogo, nem condicional de ambiente no código.

### VII. Configuração congelada por request/run

O resolvedor entrega um objeto **imutável/frozen** (Pydantic `frozen=True`) uma vez por request HTTP ou uma vez no início de um run do scheduler. Todas as chamadas daquele request/run usam a mesma config. Editar `catalog_persona` durante um run em andamento não muda o comportamento daquele run — coerente com o invariante 4 (template versionado, instância snapshot) e com o snapshot de flow (ADR-0016 §II).

### VIII. Relação com ADRs anteriores

- **ADR-0010 (agendamento fase 1):** esclarece que `schedules.yaml` continua sendo configuração de *quando* (cron/timezone); a escolha de modelo do distilador passa a ser dado de `catalog_persona`, não operacional. O gate humano para nova config de *agendamento* (novo worker/cron) permanece; o gate para troca de modelo vira changelog de catálogo com escrita restrita ao owner (ADR-0042 §I).

- **ADR-0013 (destilação e grafo buscável):** emende a §IV/§V — o `ApiExecutor` continua sem tools e com demarcação untrusted, mas o modelo e `max_tokens` passam a vir da persona, não de constantes do scheduler. O embedding continua por REST direto (§I deste ADR).
- **ADR-0016 (persona + flow mínimo):** emende a modelagem da `persona` — a config da persona ganha campos de modelo/parâmetros e continua congelada por flow. O `budget_usd` continua no template de flow, não na persona.
- **ADR-0019 (executor `cli` + GitHub):** emende a §IV/§V — a chave do provider no subprocess passa a vir de `tenant_credential`, e `max_turns`/`timeout` vêm da persona. A whitelist de ambiente e o scrub de `os.environ` continuam.
- **ADR-0039 (tenancy):** emende a §IV — `tenant_credential` passa a ser a fonte obrigatória de chave para toda chamada de LLM, não opcional.
- **ADR-0042 (catálogo por-tenant):** emende a §I — a tabela `catalog_persona` ganha os campos de configuração de LLM. Changelog de alteração continua em `catalog_changelog`.
- **ADR-0006 (embeddings):** preserva a tripla pinada `(gemini-embedding-001, 768, SEMANTIC_SIMILARITY)` e o caminho REST. A persona de sistema do embedder pode expor a tripla, mas `model` é campo travado (§IV).
- **ADR-0009 (contrato de worker):** preserva a fronteira do worker com o runner; o worker recebe a config pronta pelo `RunContext`, nunca monta a chamada de LLM sozinho.

### IX. Módulo autorizado e gate de CI

A lógica de configuração, registry e resolução de credencial vive em `kubo/llm/`, o único módulo autorizado a decidir o que chega a uma chamada de LLM. Para evitar que call sites voltem a cravar constantes, o CI ganha um gate: PR que introduz referência direta a `litellm.completion`, nome de modelo literal, `GEMINI_API_KEY`/`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` fora de `kubo/llm/` ou `tenant_credential` sem passar pelo resolvedor falha na review. O gate é complementar, não substituto do design (módulo autorizado).

## Consequências

- **Positivo:** o dono troca de modelo e ajusta parâmetros pela interface, sem PR.
- **Positivo:** um só lugar (`kubo/llm/registry.py`) declara o que cada modelo aceita; não há `if` de prefixo espalhado.
- **Positivo:** cada tenant paga seu próprio consumo de LLM; a chave do dono deixa de ser subsídio silencioso.
- **Positivo:** modelos desconhecidos funcionam com mínimo seguro — estabilidade acima de completude.
- **Positivo:** a UI distingue campos travados (embedder) de campos editáveis, com explicação.
- **Trade-off:** o `embedding` fica dependente de credencial de tenant. Hoje o embedder usa `GEMINI_API_KEY` do env e funciona mesmo sem tenant configurado; com BYOK, toda operação de embedding exige `tenant_credential` resolvida. Migrar os dados legados exige que o tenant zero tenha sua credencial cadastrada.
- **Trade-off:** a persona de sistema obrigatória adiciona uma linha a cada tenant novo. Se a tripla do ADR-0006 mudar, é preciso re-embedar o corpus antes de destravar o campo.
- **Trade-off:** o gate de CI é lista negativa e envelhece — novos padrões de hardcode só são pegos se a regex/lista for mantida. O módulo autorizado (`kubo/llm/`) é a defesa estrutural; o CI é rede de contenção.
- **Neutro:** nenhuma tabela extra além das que já existem (`catalog_persona`, `tenant_credential`, `catalog_changelog`); é mudança de schema nas tabelas do ADR-0042.

## Alternativas rejeitadas

- **Migrar o `cli` para LiteLLM.** Rejeitada: o contrato do `cli` é "prompt in → stream de eventos out" com tools, filesystem e bash; LiteLLM não fornece isso. Forçar equivalência reabriria o ADR-0019 §I e criaria um orquestrador (escopo negativo §1.2).
- **Migrar o `embedding` para LiteLLM.** Rejeitida: o passthrough de `taskType`/`outputDimensionality` pela LiteLLM é inverificável e seu modo de falha (vetor dimensionalmente válido porém incomparável) é silencioso, exatamente o risco que o ADR-0013 §I e ADR-0006 cravam.
- **Dicionário genérico de parâmetros de passthrough (`params: dict[str, Any]`).** Rejeitada: empurraria a decisão de "o que enviar" para o dado do catálogo, reabriria a DSL (invariante 3 proíbe templates de virarem DSL) e permitiria enviar parâmetros que o modelo não aceita. O registry decide capacidade; os campos são tipados e bem conhecidos.
- **Campo `environment` no schema de `catalog_persona`.** Rejeitida: DEV e PRD já são bancos separados; o campo seria sempre constante num dado banco e mentiria sobre a realidade se alguém copiasse linhas entre ambientes.
- **Capacidade de modelo como campo de persona.** Rejeitida: duplicaria "este modelo aceita X" em N personas, permitiria que dois tenants dissessem coisas contraditórias sobre o mesmo modelo e espalharia `if` por todo o executor. A capacidade é fato sobre o provedor, vive no registry.
- **Cache no resolver (carregar catálogo uma vez no startup).** Rejeitada: congelamento é por request/run, não por processo. Cachear no startup violaria o §VII e criaria estado compartilhado entre requests.
- **Fallback para chave de ambiente quando `tenant_credential` falta.** Rejeitada: manteria o subsídio silencioso do dono e mascararia a falta de BYOK. Erro claro força o cadastro.
