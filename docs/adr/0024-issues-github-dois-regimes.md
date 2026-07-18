# ADR-0024 — Issues do GitHub: dois regimes (registro vs andaime wayfinder)

> Status: **substituído por ADR-0026** · Data: 2026-07-17 · Validado pelo advisor (Fable 5) antes do crave.
> Estende/versiona a regra "issue é ponteiro" que até aqui vivia só na memória do agente.
>
> **⚠️ Superado (2026-07-17):** o ADR-0026 revoga a regra dos dois regimes — issues podem carregar
> conteúdo à vontade (o `/to-spec` publica PRD gorda no corpo). Sobrevive só o guardrail "cópia
> congelada nunca é canônica". Ver ADR-0026.

## Contexto

O projeto adota o stack de skills do Matt Pocock por inteiro, incluindo `/wayfinder`
(esforços grandes e nebulosos). O wayfinder é issue-cêntrico por design: um "mapa"
(`wayfinder:map`) cujo corpo é um documento vivo (Notes / Decisions-so-far / Fog), tickets
de decisão como sub-issues, bloqueio via issue dependencies nativas, e frontier query sobre
as issues. A engrenagem não funciona fora das issues.

Isso tensiona uma regra de governança do dono, nascida de incidente real (issues virando
silos de conteúdo obsoleto): "Issue do GitHub é ponteiro, nunca conteúdo — título + 1
parágrafo + link; doc vivo = link de branch, não corpo". Essa regra, até agora, existia
apenas na memória do agente — fora do repo, violando "o repositório Git é a única ponte de
contexto" (CLAUDE.md). Revisá-la é o momento de versioná-la.

## Decisão

**A regra não é revogada — é reformulada como UM princípio com DOIS regimes.** O princípio
subjacente sempre foi: *conteúdo durável vive no repo; issue nunca é registro permanente*. O
mapa do wayfinder cumpre esse princípio por outro caminho — é andaime com morte programada
que colapsa em spec/ADRs no handoff `/to-spec`.

- **Regime padrão (toda issue):** título + 1 parágrafo + link. Doc vivo = link de branch,
  nunca conteúdo inline. SHA-pin só para código que não se move.
- **Regime andaime (só wayfinder):** o mapa (`wayfinder:map`) e seus tickets de decisão
  podem carregar conteúdo no corpo, sob condições:
  1. Contexto pesado continua fora do corpo (gist/branch); o corpo do mapa é estado + índice
     de ponteiros.
  2. No handoff `/to-spec` o mapa **morre**: tudo que importa colapsa em spec/ADRs no repo;
     o mapa é fechado com comentário final linkando os artefatos. Decisão não-colapsada é
     **descarte explícito nomeado** no comentário de fechamento — nunca herança implícita via
     gist órfão.
  3. Mapa sem atividade por 30 dias, ou aberto após o handoff, é violação: fechar ou
     justificar no próprio mapa.
  4. O regime andaime pertence ao **ciclo de vida da skill**, não ao label: rotular issue
     comum de `wayfinder:map` para escapar do padrão é violação.
  5. No máximo 1–2 mapas ativos simultâneos (mantenedor solo).

## Consequências

- **Positivo:** a regra passa a existir versionada no repo; o wayfinder roda puro, sem fork
  da convenção da skill. Critério de decisão vira uma pergunta única: *isto é registro ou
  andaime?*
- **Trade-off aceito:** gists são armazenamento transitório fora do repo (fora de backup,
  grep, detect-secrets) — mitigado pela cláusula de colapso/descarte; o risco residual é o
  handoff malfeito.
- **Neutro:** a memória `github-issue-pointer-not-content.md` reduz-se a um ponteiro para
  este ADR, coerente com a própria regra.

## Alternativas rejeitadas

- **Substituir a regra inteira (conteúdo em qualquer issue):** revogaria uma regra baseada em
  evidência por causa de uma skill que nem precisa disso — scope creep disfarçado de
  simplificação.
- **Dobrar o wayfinder para manter o mapa num arquivo do repo:** quebraria sub-issues,
  native blocking e frontier query — a engrenagem da skill.
- **Regra + carve-out por label:** rejeitado — critério frágil (label é falsificável);
  "dois regimes por ciclo de vida" é mais robusto e mais fácil de um solo entender em 6 meses.
