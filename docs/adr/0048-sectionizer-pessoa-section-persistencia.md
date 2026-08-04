# ADR-0048 — Sectionizer: particionamento de capítulos em seções no upload

> Status: aceito · Data: 2026-08-03

## Contexto

O módulo Estudos (ADR-0043/0047) trata o **capítulo** como átomo do plano de
estudo: o planner agrupa capítulos em lições, o tutor gera a lição do dia a
partir do texto do capítulo, e o scheduler dispara a geração na véspera do
dia de cadência.

Capítulos de epub/PDF variam muito em tamanho — um capítulo de 2 páginas e
um de 40 viram lições do mesmo "tamanho" no plano. Para materiais sem
estrutura de marcadores (PDFs sem outline), o capítulo único é o documento
inteiro, e a lição cobre tudo de uma vez.

KUBO-184 introduz uma persona `sectionizer` que particiona cada capítulo em
**seções tópicas** — as divisões naturais do texto (ex.: "Fundamentos",
"RAG e tool calling", "Orquestração"), não fatiamento arbitrário por
tamanho. KUBO-185 (emenda 2026-08-03) faz a seção virar o átomo do plano:
o planner agrupa seções em lições, permitindo múltiplas lições curtas
diárias mesmo quando o material tem um único capítulo. KUBO-189 (emenda
2026-08-04) migra tutor e scheduler para `get_sections_for_entry` e remove
o shim `get_chapters_for_entry` — a seção é agora o átomo em todo o
caminho (planner, tutor, scheduler).

## Decisão

1. **Nova persona `sectionizer`** (executor `api`, modelo Haiku), semeada
   por default no catálogo do tenant (ADR-0042). Recebe o conteúdo de um
   capítulo e devolve seções com `title`, `content` e `summary` (~100 chars).

2. **`anchor_text` derivado em código** dos primeiros ~200 chars de
   `content` — não pedido ao LLM (economia de tokens e evita alucinação
   de âncora).

3. **Validação de cobertura em código**: cada `title` não-vazio, cada
   `content` não-vazio, e contenção de tokens (conjunto de palavras) do
   texto enviado ao LLM na concatenação das seções ≥ 90% (threshold
   calibrável). A cobertura é medida sobre o texto efetivamente enviado
   ao LLM (truncado a 20k chars), não sobre o capítulo inteiro — capítulos
   muito longos são truncados para caber no budget de output. Falha de
   validação → fallback.

4. **Fallback gracioso**: sectionizer falha (LLM indisponível, JSON
   inválido, ValidationError) OU cobertura < 90% → 1 seção com
   `content = chapter.content`, `title = chapter.title`,
   `anchor_text = ""`, `summary = chapter.title`. O estudo nunca é
   bloqueado. Capítulos além do limite de 20 sectionizados por material
   também recebem fallback (controle de latência no upload).

5. **Nova tabela `material_section`** com `material_chapter` (FK),
   `material` (FK), `tenant_id`, `user_id`, `seq` (local ao capítulo,
   1-based), `title`, `anchor_text`, `content`, `summary`. Índice UNIQUE
   em `(material_chapter, seq)`.

6. **Sectionização síncrona no upload**: 1 chamada LLM por capítulo, ao
   lado do `summarizer` existente. `create_material` persiste seções na
   mesma transação que o material + capítulos.

7. **Seção como átomo do plano (KUBO-185, emenda 2026-08-03)**: `plan_entry`
   passa a referenciar `material_section` (não `material_chapter`). O planner
   agrupa seções em lições, a UI mostra seções por lição, e a rota
   `remove-chapter` vira `remove-section`. Migration destrutiva (0037) —
   recomeço limpo (ADR-0047 §7): nenhum `plan_entry` em `running` existia.
   KUBO-189 remove o shim `get_chapters_for_entry` — tutor e scheduler
   agora chamam `get_sections_for_entry` diretamente. Materiais pré-0036
   recebem backfill de 1 seção por capítulo na migration 0037.

## Consequências

**Positivas:**
- Preparação para plano granular (ticket futuro): seções persistidas
  permitem que o planner agrupe por tópico real, não por capítulo
  arbitrário.
- Custo controlado: Haiku por capítulo, síncrono, com fallback — o
  upload nunca bloqueia por indisponibilidade de LLM.
- Validação em código: cobertura ≥ 90% do vocabulário do texto enviado
  garante que as seções não pulam trechos do capítulo (sobre o texto
  truncado enviado ao LLM).

**Negativas:**
- +1 chamada LLM por capítulo no upload (latência adicional).

**Neutras:**
- O `anchor_text` é derivado, não curado — serve como localizador
  aproximado, não como âncora precisa.

## Alternativas rejeitadas

- **Fatiamento por tamanho fixo** (ex.: 2000 chars por seção): não
  respeita divisões tópicas naturais; lições ficam sem coesão semântica.
- **Sectionização assíncrona (job)**: adiciona complexidade de
  orquestração para um particionamento que cabe em segundos; o upload
  síncrono já faz o sumário da mesma forma.
- **Sectionizar só sob demanda (lazy)**: o plano precisa da estrutura
  de seções no momento da proposta; lazy adicionaria latência no
  planning, que é interativo.

## Emendas

### Emenda 1 — §6 revogado pelo ADR-0049 (2026-08-04)

O §6 ("sectionização síncrona no upload") e a alternativa rejeitada
"sectionização assíncrona (job)" caem pelo **ADR-0049 §III**. O argumento
original — "o particionamento cabe em segundos" — era hipótese; a operação
mostrou até 20 chamadas LLM sequenciais de 30s dentro do request de upload,
estourando o timeout do proxy sem retomada.

A ingestão (parse + sumário + sectionizer) passa a rodar num job de intervalo
no scheduler, sobre `material.status` (`pending|ready|failed`). Tudo o mais
deste ADR permanece: a persona `sectionizer`, o `anchor_text` derivado, a
validação de cobertura ≥ 90%, o fallback gracioso, a tabela `material_section`
e a seção como átomo do plano.
