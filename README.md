# Kubo (工房)

**A personal agent atelier: it collects information, curates knowledge into a graph, distributes results — and builds its own workers.**

Kubo (工房, *kōbō*, "the master craftsman's workshop") is a single-maintainer system built around one rule: *does this increase or reduce the cognitive load of a solo maintainer?* Everything else follows from that.

One runtime (Python), one database (SurrealDB), three YAML catalogs, four capabilities.

---

## The four capabilities

1. **Collect** — deterministic workers ingest sources (RSS feeds, GitHub repos, pages, APIs), on a schedule or on demand.
2. **Curate & store** — raw content is distilled and stored as a queryable graph: documents + embeddings + relations, in a single datastore.
3. **Distribute** — knowledge leaves the system: dashboards, digests, Telegram, email.
4. **Self-extend** — the system develops new agents and workers *as projects*, with personas working through flows and boards, with GitHub access, under human supervision.

Capability 4 consumes 1–3: an agent that builds an agent queries the knowledge base (2), reports through the distribution channels (3), and ships workers that feed collection (1). **The loop feeds itself.**

## What it is not

Negative scope is a contract, not a suggestion. Each item was considered and rejected with a recorded reason ([spec §1.2](docs/kubo-spec-funcional.md)):

- **Not multi-tenant.** One owner. Friends are distribution *recipients*, not operators — so there is no credential proxy.
- **Not a generic workflow engine.** No canvas, no workflow DSL. Flow templates are declarative YAML; logic that YAML cannot express belongs to a persona skill or a worker.
- **Not a standalone project management tool.** Boards and tasks are a data model in the graph plus views, not a product.
- **No heavyweight orchestrator.** APScheduler and FastAPI webhooks, not Prefect/Dagster/Temporal/Airflow.
- **Not fully autonomous.** Promoting generated code to an operational pipeline **always** passes a human gate.

---

## Status

Each phase must deliver usable value before the next one opens.

| Phase | Scope | State |
|---|---|---|
| **1 — Substrate** | Python runtime + SurrealDB + LiteLLM on Docker Compose; knowledge and work schemas; worker contract; integration and persona catalogs; `feed` worker; distillation into a searchable graph | ✅ live |
| **2 — Distribution** | Query → digest → Telegram; daily scheduled sweeps; internal notifications for gates and failures | ✅ live |
| **3 — Work model** | Flow templates and instantiation; boards as graph views; personas executing tasks (`api` and `cli` executors); GitHub integration (branch, commit, PR); human promotion gate in the browser | 🚧 partial — the promotion rite runs end to end; the full board model does not |
| **4 — Self-extension** | Full `dev-aidlc` template: interview → PRD → DAG of vertical slices → personas building under TDD with independent review, closed by the promotion rite | ○ started — a minimal dev flow opens real PRs; the full template is not built |

**Running today:** four scheduled jobs (GitHub releases sweep, RSS sweep, distiller, digest), four built-in workers (`feed`, `distiller`, `digest`, `github-releases`), and a FastAPI + HTMX interface covering distilled content, knowledge, runs, source registry, destinations, flows and gates.

---

## Architecture

```
┌──────────────── Docker Compose ────────────────────────────┐
│  ┌─────────────────────────────┐   ┌────────────────────┐  │
│  │  kubo (Python 3.12+)        │   │  SurrealDB         │  │
│  │  ├─ api (FastAPI + HTMX)    │◄──┤  document          │  │
│  │  ├─ scheduler (APScheduler) │   │  vector (HNSW)     │  │
│  │  ├─ flow/task runtime       │   │  graph             │  │
│  │  └─ executors (api | cli)   │   └────────────────────┘  │
│  └──────────┬──────────────────┘                           │
│             │ LiteLLM ──► Anthropic, OpenAI, Gemini,       │
│             │             OpenRouter, Ollama               │
│             │ CLI adapters ──► Claude Code (Agent SDK)     │
│             │ YAML integrations ──► GitHub, Telegram, RSS  │
└─────────────┴──────────────────────────────────────────────┘
```

Two neighbouring schemas share one database, with edges between them — that is the point. Knowledge (`source`, `item`, `distilled`, `entity`, `memory`) and work (`flow`, `task`, `persona`, `run`) are connected by cross-schema edges like `task -[consults]-> distilled` and `distilled -[produced_by]-> flow`, so work in progress can query accumulated knowledge and every distilled fact carries its provenance.

### Non-negotiable invariants

1. One runtime: Python 3.12+.
2. One database: SurrealDB. All database access goes through `kubo/store/` — never scattered queries.
3. Three YAML catalogs (`catalogs/integrations/`, `catalogs/personas/`, `catalogs/flow_templates/`): declarative, one file per item, versioned. **Templates are data, not code** — evolving them into a DSL is forbidden.
4. Versioned template, snapshot instance: instantiating a flow freezes a copy of the config. Changing a template never affects a running flow.
5. A human gate is mandatory before generated code becomes an operational pipeline. No bypass, not even behind a flag.
6. Every worker satisfies the worker contract. The runtime validates it rather than trusting the author.
7. Secrets only by reference (env / secret manager) — never inline in YAML, code, logs or commits.

---

## Repository layout

```
kubo/
├── docs/                    # functional spec, design system, ADRs, runbooks
│   └── adr/                 # architecture decisions (short ADR format)
├── catalogs/
│   ├── integrations/        # one YAML per integration
│   ├── personas/            # one YAML per persona
│   └── flow_templates/      # one YAML per flow template
├── kubo/
│   ├── api/                 # FastAPI routes and HTMX views
│   ├── store/               # the only SurrealDB access layer + migrations
│   ├── runtime/             # flows, tasks, boards, gates
│   ├── executors/           # api (LiteLLM) and cli (agent CLIs)
│   ├── workers/             # built-in workers
│   ├── scheduler/           # APScheduler jobs
│   ├── distribution/        # telegram, digests, destinations
│   └── contracts/           # worker protocol, manifests, validation
├── tests/                   # mirrors kubo/
├── schedules.yaml           # when things run (operation, not a catalog)
└── docker-compose.yml
```

## Getting started

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync --frozen                    # install pinned dependencies

# SurrealDB for integration tests (ephemeral, in-memory)
docker run -d --name surreal -p 127.0.0.1:8000:8000 \
  surrealdb/surrealdb:v3.1.5 start --user root --pass root memory

uv run pytest -m "not integration"  # unit tests
uv run ruff check . && uv run pyright
```

Connection settings come only from the environment — see `.env.example`.

## Development

Test-driven development is mandatory: no production code without a failing test that requires it. Quality gates run in a fixed order and stop at the first failure — `ruff check`, `ruff format --check`, `pyright`, `pytest`, `uv lock --check`, plus secret scanning and dependency audit in CI. Every architectural decision that extends or contradicts the spec becomes an ADR in `docs/adr/` *before* the code.

Canonical documents, in order of authority:

- [`docs/kubo-spec-funcional.md`](docs/kubo-spec-funcional.md) — functional spec; source of truth for scope and concepts (PT-BR)
- [`docs/kubo-design-system.md`](docs/kubo-design-system.md) — UI/UX tokens, components, layout (PT-BR)
- [`docs/adr/`](docs/adr/) — architecture decision records (PT-BR)
- [`CLAUDE.md`](CLAUDE.md) — working agreement for agent sessions (PT-BR)

## Lineage

Kubo is the deliberate collapse of a previous three-runtime, two-database ecosystem into one system a single person can maintain. The root cause of the restart was complexity fatigue. It is **not a fork** — it borrows functional specifications and patterns from three references (Valmis, Multica, RARA/Kura) without inheriting any codebase.

## License

MIT — see [LICENSE](LICENSE).
