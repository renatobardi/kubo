# Notas de execução — Sessão 0010 (UI Conhecimento + Execuções + fidelidade)

> Log da sessão de execução (CLI Opus + fable-advisor). Companheiro do plano
> `0010-ui-conhecimento.md`. Registra probes, decisões e a fila da 0011.

## M1 — Store reads (concluído, commit `feat(store): M1 ...`)

**Probe de projeção (E3) — decisão BINÁRIA: projeção vence.**
Contra container efêmero v3.1.5 + SDK 2.0.0, provado:
- `->derived_from->item.title` (título a 1 hop) — **funciona**.
- `->derived_from->item->from_source->source.canonical/kind` (fonte a 2 hops encadeados) — **funciona**.
- Card inteiro (título+fonte+data) numa query com `ORDER BY created_at` — **funciona**.
- Travessia de grafo volta **array mesmo para relação 1:1** (`title: ['X']`, `[None]` quando NULL) → `_unwrap` pega `[0]`, `[None]`→None→fallback 1ª linha do summary.
- `array::len(<-mentions)` + `ORDER BY` no alias computado — **funciona**, sem quirk.
- `read_entity`: `FROM $e<-mentions<-distilled` projetando `_CARD_COLS` — **funciona** (uma query).
- **`math::max` explode em datetime** (`Expected number`); **`time::max` funciona** → usado no badge de fonte (E4).

Não houve necessidade do fallback Python-compose. `read_distilled` segue com composição Python (não mexido).

## M7a — Probe read-only user (concluído, PROBE PASSOU)

Contra container efêmero v3.1.5 + SDK 2.0.0:

**(a) DEFINE USER … ROLES VIEWER** — funciona em `ON DATABASE` e em `ON ROOT`.

**(b) Signin do SDK 2.0.0:**
- Root: `signin({username, password})` — **sem** ns/db. Incluir ns/db no payload de root → `NotAllowedError` (falha!).
- User de DATABASE: `signin({username, password, namespace, database})` — **com** ns/db.
- User de ROOT (mesmo com ROLES VIEWER): `signin({username, password})` — **sem** ns/db, igual à forma atual do `client.py`.

**(c) Fail-closed — CONFIRMADO nas duas variantes.** Como viewer:
- SELECT funciona.
- UPDATE/CREATE retornam `[]` **sem levantar exceção** (negação SILENCIOSA no v3.1.5), mas o read-back como root prova que **o dado NÃO mudou** (status intacto, count intacto).
- ⇒ O teste fail-closed asserta **dado inalterado**, não *raises* (a negação não levanta).

**Duas rotas viáveis para M7b (decisão de ADR → advisor antes de cravar):**

| Rota | Impacto em `client.py` (strict) | Escopo |
|---|---|---|
| `ON DATABASE … VIEWER` | signin precisa de ns/db → ramo (root sem / DB-user com) | menor privilégio (só db kubo) |
| `ON ROOT … VIEWER` | **zero** (mesma forma de signin do root atual) | read-only em todos os NS (irrelevante: single-tenant) |

**DECISÃO (advisor, ADR-0014 amendment): Path A — ROOT-VIEWER.** Evita o branch permanente no caminho de auth (`client.py`), valor defensivo integral (nenhuma escrita possível). Path B (DB-scoped) rejeitado: o ganho de least-privilege é escopo vazio numa instância single-tenant; não vale um branch perpétuo no módulo mais sensível.

**Risco residual confirmado por sonda extra:** o ROOT-VIEWER LÊ `INFO FOR ROOT`, que expõe o PASSHASH argon2 do root (`INFO FOR DB` NÃO expõe). Mitigação: runbook MANDA senha root longa e aleatória (32+ chars) — argon2 + aleatória torna o crack offline irrelevante. Condição de validade da decisão: vale enquanto single-tenant (1 ns, 1 db); 2º namespace com dado de outra sensibilidade reabre a decisão.

**Quirk pinado (v3.1.5 + SDK 2.0.0):** negação de escrita é SILENCIOSA (retorna `[]`, não levanta). Companheiro do ADR-0005. Teste fail-closed: assert SELECT funciona + escrita NÃO altera dado (read-back) + tolera vazio OU exceção (não pina o silêncio).

**Amendment registra:** supersede da linha 101 do ADR-0014 (esboço `PERMISSIONS GRANT SELECT` → real é `ROLES VIEWER`); tripwire de escrita (1ª rota mutante NÃO reusa a viewer — no-op silencioso é footgun); rotação (`DEFINE USER OVERWRITE`/`REMOVE USER`) + passo de restore (RocksDB em volume novo não traz o user → runbook recria); higiene (DEFINE USER via `surreal sql` CLI, NUNCA pela store — structlog logaria a senha); só kubo-api aponta pro viewer (scheduler/distiller seguem com credencial de escrita).

Criação do user: **runbook one-time** (senha via env/getpass), NUNCA migration (invariante 8). `SURREAL_USER/PASS` do kubo-api apontam pro user read-only; fixture de integração cria o user no setup.

## Fila da 0011 (a consolidar na conclusão)

- (a preencher)
