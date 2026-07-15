# Sessão 0019 — UI mobile: o Kubo no bolso

> **Status:** aprovado pelo dono (2026-07-15, planejamento no Cowork); advisor GO com 4 correções incorporadas
> **Ambiente de execução:** Claude Code CLI — sessão de BAIXO risco (só templates/CSS/docs; zero store/contracts/runtime)
> **Política de modelo (custo-benefício, regra do dono):** sessão inteira pode rodar em **Sonnet** na thread principal (não há decisão de arquitetura); advisor no fim de cada marco; ambiguidade → pergunta; travou 2x → para.
> **Timebox:** 8 horas efetivas — advisor: 6-8h plausível COM os cortes; sem eles, 10h+. Estourou no meio da varredura de telas → fecha como fatia vertical (shell+gates) e abre 0019b.
> **Estrutura:** 1 PR — branch `feat/0019-ui-mobile` (D16)
> **Contrato:** executa SOMENTE o que está aqui. **Pré-condição: sessão 0018 encerrada** (ela toca templates de gate; rebase + "entender" curto confirmando que o estado dos templates não divergiu).
> **Numeração:** esta = 0019; exposição internet = 0020; a tabela do `fase4-roadmap.md` renumera (pipeline→0021, Grill→0022, dev-aidlc→0023-0025, canônico→0026) — atualizar o roadmap neste mesmo PR.

---

## Missão

**Desktop não muda NADA.** Em viewport mobile (`<md`), o app ganha a gramática do kit v3 (`docs/design/v3/templates/kubo-mobile/`): **bottom tab bar** no lugar da sidebar, large-title headers, busca sticky, cards full-width, detalhe como página cheia. Caso de uso âncora: **operar um gate do celular** (aprovar promoção do sofá). Validação física: dono + amigo, no aparelho real via tailnet. E o segundo pedido do dono: **o padrão do projeto passa a exigir mobile** — toda tela nova nasce responsiva.

## Decisões do dono

- **D49:** só acrescentar mobile; web/desktop intocada (tema claro segue default; toggle dark existente).
- **D50:** kit `docs/design/v3/` vira a referência autoritativa de design para TODAS as futuras implementações.

## Correções do advisor (incorporadas)

- **C1 — SEM ADR.** Norma datada em `docs/design/README.md` (mesmo padrão da regra de fidelidade ao mockup) + § mobile no `kubo-design-system.md` + linha no DoD do CLAUDE.md. **Obrigatório: declarar que v3 SUPERSEDE `docs/design/mvp/`** — sem isso o repo fica com duas referências autoritativas.
- **C2 — Navegação em pilha = navegação NORMAL de páginas.** As pilhas por-tab do JSX são artefato de SPA — não replicar. Voltar = botão do browser + chevron-voltar no header mobile. **Sem hx-boost na v1.** GateSheet mobile = página de detalhe comum, full-screen (sem bottom sheet arrastável).
- **C3 — Tab "Saber" NÃO vira tela consolidada** (fusão Destilados/Entidades/Fontes do kit = rota nova = estouro). V1: tab aponta pra `/distilled` com pills pra Entidades/Fontes no topo — desvio pré-declarado na tabela de paridade. Tab "Mais" = página simples com links do resto da nav.
- **C4 — Docs/norma PRIMEIRO** (30-60min, valor independente): se a implementação derrapar, o segundo pedido do dono já está entregue.

## Riscos técnicos nomeados (do advisor — atacar por construção)

1. **iOS Safari quebra `h-screen`/100vh** (toolbar dinâmica) → usar **`h-dvh`** no mobile; testar no Safari REAL, não só emulação. O bug mais provável da validação no sofá.
2. **Safe-area:** meta viewport ganha `viewport-fit=cover` (hoje ausente) + `env(safe-area-inset-bottom)` na tab bar.
3. **CSS inline do `nav-collapsed`** em base.html usa seletor de elemento sem media query — escopar a `≥md` explicitamente ou briga com o `hidden max-md` da sidebar.
4. **Loop de build do Tailwind:** binário standalone `--watch` LOCAL durante a sessão (mesmo binário pinado por SHA do Dockerfile) — nunca iterar classe via deploy.sh.
5. **Regressão desktop:** mobile SÓ por variantes (`max-md:` / aditivo com `md:hidden`); evidência inclui **screenshot desktop antes/depois** — o "não mudou" também se prova.
6. **Evidência mobile:** device emulation do DevTools (preset iPhone) + passe no aparelho real do dono = aceite. **NÃO introduzir Playwright** (dependência nova sem justificativa).

## Marcos (ordem de ataque)

| # | Marco |
|---|---|
| 19.1 | **Docs/norma (C4):** § mobile no `kubo-design-system.md` (gramática, breakpoint binário `<md`/`≥md`, tab bar, referência `docs/design/v3/`); `docs/design/README.md` — norma datada "toda tela nasce responsiva" + **v3 supersede mvp/**; linha no DoD do CLAUDE.md; renumeração do fase4-roadmap |
| 19.2 | **Shell responsivo** em base.html: sidebar `hidden max-md`, bottom tab bar fixa `md:hidden` (Painel · Saber · Trabalho · Distribuição · Mais), `h-dvh` mobile, safe-area (risco 1-3), header large-title com chevron-voltar nas páginas de detalhe |
| 19.3 | **Gates no celular** (nunca cortável): board + GateSheet full-screen operáveis em mobile — aprovar/rejeitar/confirmar-promoção com dedo |
| 19.4 | **Varredura por tela** (variantes `max-md:`): Painel, Destilados (+busca sticky), Entidades, Fontes, Execuções, Envios, Destinos, detalhes. Metadados secundários `hidden md:flex`; truncation/padding |
| 19.5 | **Tab Mais** (página simples de links) + pills do Saber (C3) |
| 19.6 | **Paridade:** tabela contra `templates/kubo-mobile/KuboMobileApp.jsx` com desvios declarados (C3, pilha→páginas) + screenshots mobile (emulation) + **desktop antes/depois** |
| 19.7 | **Deploy + validação física (gated no "pode executar"):** dono navega TUDO no celular via tailnet (Safari real), opera um gate; depois o amigo valida |

## Critérios de aceite

- Desktop: screenshots antes/depois idênticos (zero regressão).
- Mobile: tab bar fixa com safe-area; todas as telas navegáveis; gate operado com sucesso no aparelho real do dono; busca sticky em Destilados.
- Docs: norma no README + § no design system + DoD atualizado + v3 declarado autoritativo (supersede mvp/).
- Suite verde (templates não têm testes próprios de layout; XSS/rotas existentes seguem verdes).

## Escopo negativo

- NADA de mudança visual no desktop; sem SPA/hx-boost/transições/swipe/bottom-sheet arrastável; sem PWA (gestos/offline/instalável = conversa de arquitetura, ADR + advisor antes); sem tela Conhecimento consolidada; sem telas novas (Catálogos/Configurações do kit ficam na fila); sem Playwright; sem breakpoint tablet (binário `<md`/`≥md`); **sem exposição internet** (sessão 0020, D46-D48).

## Sacrifícios pré-declarados (ordem)

1. Paridade fina por-tela com o JSX (layouts bespoke) → tabela de desvios cobre.
2. Busca sticky em toda tela → só Destilados.
3. Tela "Mais" rica → lista de links simples.
4. Breakpoints intermediários → fora por definição.
**Nunca cortar:** tab bar + shell, gate operável no celular, docs/norma (19.1), safe-area.

## Pontos de consulta ao advisor

1. Fim do 19.2 (shell) — antes da varredura.
2. Antes de declarar conclusão.
3. Extraordinária: >2 telas exigindo markup mobile próprio (não só variantes) → timebox furado → fechar como shell+gates e abrir 0019b.
