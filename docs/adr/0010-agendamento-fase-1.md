# ADR-0010 — Agendamento na fase 1 (`schedules.yaml` + APScheduler)

> Status: **aceito** · Data: 2026-07-05

## Contexto

A fase 1 precisa de coleta periódica (feeds em cron). O invariante 7 do CLAUDE.md proíbe orquestradores pesados (Prefect/Dagster/Temporal/Airflow — "escopo negativo" da spec); o CLAUDE.md fixa **APScheduler** como mecanismo de scheduling. Este ADR decide a **forma da configuração de agendamento** e o **ciclo de vida do agendador**, não o contrato de worker (que é ADR-0009).

## Decisão

### I. `schedules.yaml` na raiz do repositório (localização é decisão arquitetural)

A configuração de schedules mora em **`schedules.yaml`** no raiz do repositório, ao lado de `docker-compose.yml`, `pyproject.toml` e `CLAUDE.md`. Localização importa: o arquivo de orquestração está **fora dos catálogos** (next-to the runtime).

**Não é uma "4ª categoria de catálogo" (objeção central):** os três catálogos YAML (invariante 3: `catalogs/integrations/`, `catalogs/personas/`, `catalogs/flow_templates/`) descrevem **ARTEFATOS** do ateliê — QUAIS (the *what*: que integração, que persona, que template). `schedules.yaml` descreve **OPERAÇÃO** — QUANDO as coisas rodam (the *when*: que worker, em qual cron). Eixos diferentes; logo não viola o invariante 3 ("Três catálogos YAML"). É o mesmo movimento consciente que introduzir a tabela `run` (ADR-0002, também operacional, não parte do modelo de conhecimento/artefato).

### II. Formato: `timezone`, lista de workers agendados, sem listas de feeds

```yaml
timezone: "America/Sao_Paulo"

schedules:
  - worker: "feed"
    cron: "0 8 * * *"
    config:
      feed_name: "hacker_news"
  - worker: "feed"
    cron: "0 9 * * *"
    config:
      feed_name: "github_trends"
  # ... mais entries, uma por feed real
```

**Campo `timezone` OBRIGATÓRIO e EXPLÍCITO.** Fundamento: APScheduler usa `timezone.now()` do processo (system tz por default) quando um trigger não especifica tz. Em dev (Mac, system tz local) vs. produção (container, UTC) isso diverge silenciosamente — comportamento diferente no mesmo código é armadilha. Obrigatório + explícito fecha a lacuna: todo cron é construído com o tz declarado (invariante do ateliê: "um mantenedor solo entende em 6 meses").

**Estrutura `schedules`: lista de `{worker, cron, config}`**, cada entrada uma coleta agendada. **NÃO há lista de feeds dentro de uma entrada** — um worker `feed` por entrada. Carga-bearing: ADR-0009 item VII (idempotência, uma run = um feed = fonte consistente). Listar múltiplos feeds numa entry quebraria last-write-wins (que vence dentro de uma fonte). Seis feeds reais = seis entradas, pronto.

**Loader Pydantic com `extra="forbid"`** — rejeita campo desconhecido na config, segurança na borda.

### III. Mapeamento worker-nome→classe: dicionário hardcoded no módulo do agendador

```python
# kubo/scheduler/ (entrypoint: python -m kubo.scheduler)
WORKER_REGISTRY: dict[str, type] = {
    "feed": FeedWorker,
    # "scribe": ScribeWorker,  # fase posterior
}
```

**Sem registry/plugin/entry-point.** Um sistema de descoberta dinâmica (importação de `kubo.workers.<name>`, ou via `importlib.metadata`) seria um **DSL disfarçado** (invariante 3: "Templates são dados, não código; proibido evoluí-los para DSL"). O registro explícito é o análogo do matcher `_persist` no runner: um `if/elif` ou dict que diz "este string vira esta classe". Exigir adicionar um import ao side of `WORKER_REGISTRY` para ativar um novo worker é **feature**, não bug — força revisão de fluxo (gate humano, invariante 5).

### IV. Ciclo de vida: `BlockingScheduler` + SIGTERM que aguarda

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BlockingScheduler(timezone=tz_from_config)

for entry in schedules:                       # entry: {worker, cron, config}
    worker_cls = WORKER_REGISTRY[entry.worker]  # KeyError = falha alta, sem descoberta dinâmica
    scheduler.add_job(
        execute_job,
        trigger=CronTrigger.from_crontab(entry.cron, timezone=tz_from_config),
        kwargs={"worker_cls": worker_cls, "config": entry.config},
    )

def _sigterm(signum, frame):
    scheduler.shutdown(wait=True)             # espera a run em voo terminar

signal.signal(signal.SIGTERM, _sigterm)
scheduler.start()                             # bloqueia; a main thread dorme aqui
```

**`BlockingScheduler` síncrono.** Sem event loop; `APScheduler.BackgroundScheduler` + `asyncio` seria o passo para async, mas não há consumer (workers não são async na fase 1, store é sync + ws blocking). Adicionar um event loop sem nada a esperar é overhead e complexidade sem retorno — custo mental de mantenedor solo não compensa.

**SIGTERM ordena shutdown com `wait=True`:** um `SIGKILL` pode interromper uma run meio-caminho; `SIGTERM` permite que a job em voo termine tranquilamente antes de sair. O `wait=True` garante que `scheduler.shutdown()` não retorna até que a job corrente finalize e o DB feche.

**Cada trigger construído com timezone explícito do YAML.** Sem fallback para system tz — o comportamento é determinístico.

### V. Conexão-por-job: cada execução abre e fecha a handle própria

```python
def execute_job(worker_cls, config):
    # Síncrono, via o context manager da store (kubo/store/client.py) — abre e
    # fecha a conexão POR execução. O worker NUNCA toca a store: run_worker valida
    # o contrato, monta o ctx read-only e persiste (ADR-0009).
    with client.connect(client.config()) as db:
        run_worker(db, worker_cls(), config=config)
```

**Sem handle de DB global e longa-vida.** Um WebSocket que fica aberto por dias apodreceria (ws do SurrealDB cai, timeout, cliente não sabe — jobs falham silenciosamente até restart). Seis feeds em cron fazem o custo por-execução irrelevante (a coleta é ordem de segundos; o connect é ordem de ms). Compatível com single-threaded (sem thread pool, sem pool de conexões) — o que um mantenedor solo entende. Um pool seria prematuro. A job atravessa `run_worker`, então uma exceção do worker já vira `run.error` estruturado dentro da fronteira — a job não derruba o agendador.

### VI. Orphan runs: aceitos, sem janitor

Uma aplicação morta durante uma job (hard kill, SIGKILL) deixa um `run` em status `running`, orfão (ninguém vai marcar como `ok`/`error`). **Aceito e documentado no runbook.** Uma query SQL encontra orphans:

```surql
SELECT id, worker, started_at FROM run
WHERE status = 'running' AND started_at < time::now() - 1h;
```

**Sem worker janitor que varre e fecha orphans.** Motivo: idempotência já cura os dados. Re-rodar a mesma feed (re-execução + idempotência = upsert de source/item) abre um novo `run` e reescreve os dados — o orphan em `running` fica como fato histórico, sem consumidor. Não há leitor de "run.status = running ⇒ erro", então o run órfão não bloqueia pipeline — é um fato observável, não um estado que exija cura automática. Cleanup manual (ex.: `UPDATE ... SET status = 'error'`) é função de runbook, não de código.

### VII. Secrets: nunca em `schedules.yaml` (invariante 8)

URLs de feed são públicas; credenciais para integração resolvem-se via catálogo de integrações + variáveis de ambiente (invariante 8: "segredos só por referência"). `schedules.yaml` nunca carrega um secret literal, nunca referencia um `secret_ref` que o runtime poderia resolver — a config de worker fica restrita ao que é não-sensível (nomes de feed, parâmetros públicos). Um worker que exigisse credencial derivaria do contexto (`ctx.integrations[...]`, resolvido pelo runtime a partir do manifest — ADR-0009 item VI), não do YAML.

## Consequências

- **Agendamento é operacional, não artefato.** Mudança de cron não é mudança de spec — é ajuste operacional (gitops-friendly, deploy sem code change).
- **Timezone explícito elimina surpresa de drift.** Comportamento é replicável entre dev e prod.
- **Registry hardcoded é força para gate humano.** Novo worker = edição de código (registro) + PR (força review, ADR-0009 item V).
- **Cada job abre conexão fresca.** Robustez contra WebSocket stale e simplicidade de implementação (sem pool complexo).
- **Orphan runs são fatos observáveis, não estados travados.** Idempotência e gitops (re-deploy força re-execução) curam dados naturalmente.

## Alternativas rejeitadas

**(a) `schedules/` como uma 4ª categoria de catálogo** — rejeitada: confunde eixo (artefatos vs. operação). Catálogos descrevem o quê; schedules descrevem quando — eixos semânticos diferentes.

**(b) `AsyncIOScheduler` com workers async** — rejeitada: sem consumidor (workers não são async na fase 1, store é sync). Adiciona event loop, complexidade, sem retorno. Revisitável quando houver demanda (fase 3+).

**(c) Registry/entry-point/importlib.metadata dinâmico** — rejeitada: disguised DSL (invariante 3). Registro explícito força review + gate humano (invariante 5).

**(d) Janitor worker que limpa orphans** — rejeitada: sem consumidor (idempotência cura dados, orphan não bloqueia pipeline). Complexidade sem demanda. Runbook basta.

**(e) Conexão global + pool** — rejeitada: overhead de pool sem escala (seis jobs) e risco de stale WebSocket. Per-job é mais simples e mais seguro em single-threaded/longa-vida (dias de uptime).
