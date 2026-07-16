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

## PAT `GITHUB_TOKEN_WATCH` (worker github-releases v0.2.0, D54, sessão 0021)

O worker `github-releases` lê a watch list do dono via `GET /user/subscriptions`. Dois PATs
funcionam — **prefira o fine-grained** (least-privilege):

**Opção A — fine-grained (preferida, menor privilégio):**

1. *GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token.*
2. **Permissions → Account permissions → Watching = Read-only.** A doc oficial da API (REST,
   seção "Watching") lista `GET /user/subscriptions` sob esta permissão — só leitura, sem o
   escopo mais largo `notifications` do PAT clássico abaixo.
3. Expiração curta (renovável). O valor NUNCA passa pelo chat/log — cole direto no `.env`
   do servidor (`GITHUB_TOKEN_WATCH=`, ver `.env.example`), invariante 8.
4. Redeploy (`./scripts/deploy.sh`) pra o `kubo-scheduler` pegar a env nova.

**Opção B — PAT clássico (fallback provado fisicamente, sessão 0021/D51):** escopo
`notifications`. **Atenção:** este escopo **NÃO é read-only** — permite marcar notificação
como lida e inscrever/desinscrever watches, além de ler a lista. É escrita de baixo risco, mas
não documente nem trate este token como "leitura". Use se a opção A não funcionar no seu caso
(watches de conta/org podem ter comportamento diferente — não testado à exaustão) — os mesmos
passos 3-4 acima se aplicam.

Sem este PAT, o worker levanta `ConfigError` no run (integração `github-watch` sem secret
resolvido) — falha legível, não silenciosa; o resto do scheduler segue rodando.

## Pendências de operação (a reconciliar antes do deploy)

- As 6 URLs de feed em `schedules.yaml` são os endpoints RSS canônicos conhecidos; **reconciliar com as URLs exatas do legado (NeonDB `feed_sources`) antes do deploy real** — o CI não toca a internet, então uma URL errada não quebra teste, só a coleta.
- Validar uma vez, manualmente, que os 6 feeds reais respondem a `Accept-Encoding: identity` sem quebrar (CDN que ignore `identity` faria o feed cair em erro estruturado — raro, mas cheque). Deploy é o M5.5.
