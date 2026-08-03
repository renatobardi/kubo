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
tamanho. As seções são persistidas mas **não substituem** o capítulo como
átomo do plano nesta fase: o planner/tutor/scheduler continuam operando em
capítulos. A persistência é aditiva, preparando o terreno para um ticket
futuro que fará a seção virar o átomo.

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

7. **Aditivo**: `plan_entry` continua referenciando `material_chapter`.
   Nenhum consumo de seções pelo planner/tutor/scheduler nesta fase.

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
- `material_section` é escrita mas não lida por ninguém nesta fase —
  débito temporário, pago no ticket que faz a seção virar átomo.

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
