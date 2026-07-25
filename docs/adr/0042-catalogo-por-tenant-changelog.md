# ADR-0042 — Catálogo por-tenant no banco + changelog de auditoria

> Status: **aceito** · Data: 2026-07-25 · **Emenda o invariante 3** do CLAUDE.md (catálogos deixam de ser YAML global do repo).

## Contexto

Resolve o ticket [KUBO-109](https://oute.atlassian.net/browse/KUBO-109) do mapa wayfinder [KUBO-104](https://oute.atlassian.net/browse/KUBO-104) — o último ticket do mapa; com ele fechado, resta só [KUBO-110](https://oute.atlassian.net/browse/KUBO-110) (reescrita da spec funcional), que é a própria conclusão do destino do mapa.

Levantamento do loader atual: `load_personas`/`load_integrations`/`load_flow_templates` (`kubo/runtime/{personas,integrations,flow_templates}.py`) leem do disco **a cada chamada** — sem cache, sem preload no startup — validando com Pydantic `extra="forbid"` (mantém o espírito do invariante 3: catálogo é dado, não DSL). A identidade estável referenciada em todo o sistema é o campo **`name`** de cada item — não existe `id` hoje; `flow.template_name`, `cast` e `permissions` de flow_template/persona guardam `name` diretamente. Least-privilege (persona × integration) é checado à parte, em `_assert_permissions` (`flow_runner.py:562`), comparando `manifest.integrations` do worker contra `persona.permissions`.

Decisão já fechada no charting: cada tenant tem catálogo próprio, no banco desde o início, sem seed de YAML.

## Decisão

### I. Três tabelas, espelhando os Pydantic models atuais

`catalog_persona`, `catalog_integration`, `catalog_flow_template` — uma por tipo, cada uma com `tenant_id` obrigatório (tenant-scoped, regra do ADR-0039) e os mesmos campos que os models Pydantic já validam hoje (persona: `executor`/`model`/`prompt`/`permissions`; integration: `kind`/`auth`/`rate_limit`/`base_url`; flow_template: `version`/`board`/`cast`/`deliverable`/`triggers`/`budget_usd`). Não um blob genérico — a estrutura de cada tipo já é bem diferente no código de hoje; achatar num JSON esconderia isso sem ganho.

**Identidade: `(tenant_id, name)` único por tabela.** `name` continua sendo a chave — não filename, não um surrogate novo. Migrar precisa preservar `name` **verbatim**: é a única coisa que quebra flows existentes (`flow.template_name`, `cast`, `permissions`) se fumbled.

### II. Changelog genérico

**`catalog_changelog`**: `tenant_id`, `kind` (`persona`/`integration`/`flow_template`), `item_name`, `before`, `after`, `changed_by` (user), `changed_at`. Uma tabela só para os 3 tipos — "quem mudou o quê, quando" é a mesma pergunta nos 3 casos; triplicar a estrutura não ganharia nada.

### III. Defaults do tenant novo — código, não YAML, não tabela

Ao criar um tenant, o sistema clona um conjunto **default** (as personas/integrations/flow_templates curadas de hoje: analista, dev, finder, humano, etc.) para as tabelas do tenant novo — sem isso, self-signup vira tela vazia sem caminho de uso.

O default vive como **dado em código Python** (`kubo/runtime/`, ex.: `DEFAULT_PERSONAS`/`DEFAULT_INTEGRATIONS`/`DEFAULT_FLOW_TEMPLATES`), com o conteúdo transcrito uma vez a partir dos `catalogs/*.yaml` atuais — **não** lido de arquivo YAML em runtime. Isso não reabre a decisão "sem seed de YAML": o mecanismo de tenant-creation nunca faz glob em `catalogs/`, só instancia objetos Python já validados e grava como linhas novas no catálogo do tenant. É a terceira variante da regra dos dois caminhos do ADR-0039 — o default não é tabela nem tem `tenant_id` nulável, é código; cada clone gerado a partir dele é uma linha tenant-scoped comum.

### IV. Loader passa a ler do banco, com `tenant_id` — comportamento preservado

`load_personas`/`load_integrations`/`load_flow_templates` ganham parâmetro `tenant_id` (`load_personas(tenant_id)`, etc.) e passam a consultar `catalog_*` em vez de fazer glob em `catalogs/*.yaml`. **Continuam sem cache** — leitura direta do banco a cada chamada, preservando deliberadamente o comportamento atual (não é omissão: introduzir cache exigiria invalidação amarrada ao `catalog_changelog`, que ninguém pediu). Essa leitura só é legítima após a checagem de `membership` do ADR-0039 §II — o loader não é um ponto de acesso paralelo ao `kubo/store/`.

`_assert_permissions` (`flow_runner.py:562`) **não muda** — compara `manifest.integrations` contra `persona.permissions` independente de onde a persona veio (YAML antes, banco agora).

### V. `Integration.auth.secret_ref` ganha forma `tenant_credential:`

Hoje `secret_ref` só aceita `env:VAR`. Com credenciais de tenant cifradas em `tenant_credential` (ADR-0039 §IV), uma integração por-tenant que usa API key do próprio tenant referencia `tenant_credential:<nome>` em vez de `env:VAR`. `env:VAR` continua válido para integrações que dependem de segredo de sistema (não de tenant) — os dois formatos coexistem, resolvidos por `resolve_integrations` (`integrations.py:135`) conforme o prefixo.

## Consequências

- **Positivo:** identidade (`name`) preservada verbatim evita quebrar referências existentes (`template_name`, `cast`, `permissions`).
- **Positivo:** changelog genérico cobre os 3 tipos sem triplicar estrutura.
- **Positivo:** defaults como código evitam reabrir "sem seed de YAML" e não exigem tabela/exceção de `tenant_id`.
- **Trade-off:** sem cache no loader mantém comportamento atual, mas significa 1 leitura de banco por chamada de `run_flow()` — aceito, é o que já acontece hoje (glob+parse a cada chamada), não é regressão.
- **Fog nova:** se/quando os arquivos `catalogs/*.yaml` do repo são apagados (viram só histórico de onde os defaults foram transcritos) — não decidido aqui, é execução do build.

## Alternativas rejeitadas

- **Catálogo genérico (1 tabela + `kind` + blob)** — menos tabelas, mas esconde a estrutura já explícita nos 3 Pydantic models.
- **Changelog por-tipo (3 tabelas)** — mais fiel à decisão de tabelas separadas do conteúdo, mas triplica uma estrutura idêntica sem ganho.
- **Tenant novo sem default (catálogo vazio)** — mais simples, mas onboarding sem caminho de uso — self-signup público (ADR-0041) precisa de algo funcional de cara.
- **Default como seed de tabela sem `tenant_id`** — reabriria exceção ao invariante "toda tabela de tenant tem `tenant_id`, sem exceção" do ADR-0039; código evita a exceção por completo.
