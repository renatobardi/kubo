# ADR-0005 — Veredito do spike SurrealDB + pins definitivos

> Status: **aceito** · Data: 2026-07-05

## Contexto

A aposta estrutural do Kubo é usar **um banco só** — SurrealDB — para document, graph e vector (invariante 2; Direção B ratificada no design). Antes de construir a store e o schema de conhecimento (M3), o M2 exigiu um spike que comprasse essa aposta **por evidência**, com gate de reversão explícito: fragilidade séria (HNSW instável, SDK async impraticável, travessia lenta) seria NO-GO e reabriria a decisão com o dono.

O spike rodou contra containers reais, com uma suíte de 14 testes (unit + integração) que passou a ser a **suíte de canário de upgrade** do par servidor/SDK.

## Decisão

**GO.** A aposta se sustenta. Os cinco comportamentos + persistência pós-restart foram verificados; nenhum disparador do gate se materializou.

**Pins definitivos (o par move junto):**
- Servidor: **`surrealdb/surrealdb:v3.1.5`** (linha estável atual, jul/2026; release de segurança).
- SDK Python: **`surrealdb==2.0.0`** (declara compatibilidade v2.0.0 → v3.1.5).

O candidato inicial do plano era o servidor `v2.1.4` (provisório no compose). Foi **rejeitado como pin definitivo**: é de dez/2024, uma major inteira atrás do 3.x (GA desde fev/2026) e sem patches de segurança recentes — indefensável para um datastore de produção. A suíte de canário foi rodada contra `v3.1.5` e passou (14/14), então o pin nasce numa versão suportada. Este é o momento mais barato para fixar versão: zero dados, store nascente.

Bump futuro do par: subir servidor **e** SDK juntos e rodar a suíte de canário; só vira pin com os 14 verdes.

## Contrato de comportamento (aceito, pinado em teste)

- **KNN HNSW exige o parâmetro EF: `<|K,EF|>`.** No 3.x, `<|K|>` sem EF **falha alto** (erro explícito) — corrige o footgun do 2.x, onde retornava vazio silencioso (busca vazia indistinguível de "nada encontrado"). `vector::distance::knn()` dá a distância.
- **Transação via single query** `.query("BEGIN; …; COMMIT;")`. Atômica: `CANCEL` e erro no meio revertem tudo. No 3.x o `CANCEL` reverte **silenciosamente** (sem exceção); um erro no meio reverte mas **não** propaga via `query()` (que só inspeciona o 1º statement). **Contrato para o M3:** o wrapper transacional da store deve usar `query_raw` e checar todos os statements, não confiar em exceção.
- **Entrada hostil (invariante):** transações e queries usam bind params (`$var`) **inclusive dentro** de strings transacionais — nunca interpolação de conteúdo coletado.
- Detalhes de API pinados no código: signin `{"username","password"}`; DDL em objeto inexistente levanta (`IF EXISTS`); `ORDER BY campo` exige o campo no SELECT; `SELECT` em tabela inexistente levanta no 3.x; `RecordID.id`/`.table_name`.

## Consequências

Banco único operável por um mantenedor solo, com os três modos de dados provados num engine só. A store (`kubo/store/`) encapsula todo acesso (invariante 2), então os contratos acima ficam confinados a uma camada.

**Riscos monitorados (não eliminados):**
- **Performance sob volume:** travessia/KNN foram provados em *correção* (3 documentos), não em *escala*. Fase 1 é escala pessoal; lentidão futura é tuning de índice/query, não reescrita — mas é risco vivo, não resolvido.
- **Restart ≠ persistência do índice:** dados e índice HNSW sobrevivem ao restart (verificado no pin), mas com corpus pequeno não se distingue "índice persistido" de "reconstruído rápido no boot" — relevante para tempo de boot e RAM na instância OCI quando o corpus crescer.
- **Recuperabilidade:** o spike matou o container (persistência), mas backup/restore (`surreal export`/snapshot de volume) **não foi ensaiado** — dívida nomeada, candidata a sessão curta.
- **`user: root` no compose** para o volume rocksdb: aceitável agora; hardening futuro (non-root + chown) na trilha OCI.

**Mitigação obrigatória para o M3 (footgun do EF):** a store exporá **uma única** função de busca KNN que sempre injeta EF (default `ef = max(k*4, 40)`) e nunca aceita SurrealQL cru com `<|K|>` vindo de fora; a suíte de canário mantém o teste que pina a falha-alta.

## Alternativas rejeitadas

(a) Múltiplos datastores (document + graph + vector separados) — rejeitada no design (Direção B): contraria o invariante 2 e a premissa de fadiga de complexidade.

(b) Pinar servidor `v2.1.4` — rejeitada: versão morta, sem patches de segurança, uma major atrás do estável; pin "definitivo" numa versão de dez/2024 seria indefensável.

(c) HTTP direto via httpx na store (em vez do SDK) — não decidida aqui: o SDK async se mostrou praticável, então o meio-termo não foi necessário. Se um dia o SDK decepcionar, essa é **decisão do dono**, não da store (escopo negativo do plano 0002).

## Pendência ligada (ADR-0006, embeddings)

A decisão de embeddings (modelo, dimensão, métrica) foi **diferida** com o smoke ao vivo (cortado por ausência de `GEMINI_API_KEY`) para uma mini-sessão dedicada. Não bloqueia o GO: a mecânica do índice HNSW independe do modelo. **Condição:** o ADR-0006 deve sair **antes** de qualquer migration de schema de conhecimento (`distilled`/embedding) no M3 — nenhuma migration de índice HNSW de conhecimento nasce "de palpite" antes dele. Dono: Renato. Prazo: antes do primeiro schema de conhecimento do M3.
