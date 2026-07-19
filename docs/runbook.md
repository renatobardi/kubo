# Runbook operacional — Kubo

Procedimentos operacionais da fase 1. Curto de propósito: cresce quando um procedimento real exigir.

## Pré-requisitos

- SurrealDB de pé e alcançável (ver `CLAUDE.md` §Comandos; produção usa `docker compose up -d`).
- Conexão só por env (invariante 8): `SURREAL_URL`, `SURREAL_USER`, `SURREAL_PASS`, `SURREAL_NS`, `SURREAL_DB` (ver `.env.example`).
- Migrations aplicadas no banco-alvo (o scheduler NÃO aplica migrations — ele assume o schema pronto):

```bash
uv run python -c "from kubo.store import client, migrations; \
c=client.connect(); db=c.__enter__(); print(migrations.apply_migrations(db)); c.__exit__(None,None,None)"
```

## Subir o scheduler

O scheduler lê `schedules.yaml` (raiz do repo), registra um job cron por entry e roda cada worker sob contrato com conexão por execução. É um processo bloqueante (`BlockingScheduler`).

```bash
uv run python -m kubo.scheduler
```

- `timezone` do `schedules.yaml` é obrigatória e explícita — todo cron usa essa tz (não a tz do processo).
- **Parada limpa:** envie `SIGTERM` (`kill <pid>` ou `docker stop`). O handler faz `shutdown(wait=True)` — a run em voo termina antes de sair.
- **Parada dura:** `SIGKILL` (`kill -9`, OOM, crash) pode deixar uma run pendurada em `running` (ver abaixo). Não há janitor: a idempotência já cura os dados; a linha órfã não tem consumidor.

## Achar e fechar runs órfãos

Um `SIGKILL` no meio de uma coleta deixa o `run` em `status = 'running'` sem `finished_at`. Query para encontrá-los (mesma do ADR-0010):

```surql
SELECT id, worker, started_at FROM run
WHERE status = 'running' AND started_at < time::now() - 1h;
```

Fechar manualmente um órfão confirmado (não há automação — é decisão de operador):

```surql
UPDATE <run:id> SET status = 'error', finished_at = time::now(),
  error = { kind: 'orphan', message: 'run órfã fechada manualmente (SIGKILL/crash)' };
```

Nenhum dado coletado se perde: a próxima execução agendada re-coleta o feed (idempotente por `external_id`).

## Rodar um worker avulso (fora do agendamento)

Para disparar uma coleta única sem esperar o cron — ex.: testar um feed novo antes de adicioná-lo ao `schedules.yaml`:

> ⚠️ **Este comando GRAVA no banco que `client.config()` resolve** (env `SURREAL_URL`/`SURREAL_NS`/`SURREAL_DB`) — é o caminho de persistência real, não um dry-run. Confirme que o ambiente apontado é o certo (use staging, nunca produção por engano) ANTES de rodar: `echo $SURREAL_URL $SURREAL_NS $SURREAL_DB`. Os dados de teste (`title: 'Teste'`) vão para o grafo real do ambiente configurado.

```bash
uv run python -c "
from kubo.scheduler import execute_job
execute_job('feed', {'feed_url': 'https://example.com/feed.xml', 'title': 'Teste', 'tags': ['manual']})
"
```

`execute_job` abre a conexão, roda o worker sob contrato (`run_worker` valida e persiste) e fecha — o mesmo caminho que o cron usa. O resultado (itens, stats, erro) fica no grafo: veja o último `run`:

```surql
SELECT worker, status, stats, error, started_at, finished_at FROM run ORDER BY started_at DESC LIMIT 1;
```

## PAT do worker `github-releases` (APOSENTADO `GITHUB_TOKEN_WATCH`, #110)

Até o #110 o worker DESCOBRIA a watch list do dono via `GET /user/subscriptions` (depois
GraphQL `viewer.watching`, D57) e exigia um PAT dedicado `GITHUB_TOKEN_WATCH` com escopo
`notifications`. O #110 aposentou a descoberta: o repo agora é um Cadastro `github-repo`
cadastrado à mão (UI #105) e o worker só lê `/repos/{owner}/{repo}/releases` (público, leitura
pura). O PAT dedicado perdeu a razão de existir.

**O worker usa hoje `GITHUB_TOKEN_READONLY`** — o MESMO token de leitura do rito de promoção
(integração `github-readonly`), já configurado no `.env` do kubo-test. Sem alargamento de escopo:
ambos os usos são leitura pública. Nada a criar no deploy do #110; `GITHUB_TOKEN_WATCH` pode ser
removido do `.env` do servidor (não é mais lido).

**Nota de método preservada (valor durável da investigação aposentada):** na sessão 0021, sobre
"fine-grained cobre `/user/subscriptions`?", houve TRÊS respostas divergentes — o advisor disse
que não (sem evidência), a doc oficial do GitHub disse que sim (evidência documental), a API real
quebrou com 503 ao testar (evidência empírica). Nenhuma das duas primeiras acertou sozinha.
Capacidade de API é fato empírico — doc oficial é hipótese até o `curl` responder.

Sem `GITHUB_TOKEN_READONLY`, o worker levanta `ConfigError` no run (integração `github-readonly`
sem secret resolvido) — falha legível, não silenciosa; o resto do scheduler segue rodando.

## Pendências de operação (a reconciliar antes do deploy)

- As 6 URLs de feed em `schedules.yaml` são os endpoints RSS canônicos conhecidos; **reconciliar com as URLs exatas do legado (NeonDB `feed_sources`) antes do deploy real** — o CI não toca a internet, então uma URL errada não quebra teste, só a coleta.
- Validar uma vez, manualmente, que os 6 feeds reais respondem a `Accept-Encoding: identity` sem quebrar (CDN que ignore `identity` faria o feed cair em erro estruturado — raro, mas cheque). Deploy é o M5.5.
