# ADR-0026 — Revoga a regra issue-ponteiro (substitui ADR-0024)

> Status: **aceito** · Data: 2026-07-17 · Validado pelo advisor (Fable 5) antes do crave.
> **Substitui o ADR-0024.**

## Contexto

O ADR-0024 (1 dia antes) estabeleceu "issue é ponteiro, nunca conteúdo", com um regime-andaime
para mapas de wayfinder. Ele estava **certo para os fatos que tinha**: o único caso de conteúdo
em corpo de issue era o wayfinder, e o regime-andaime resolvia — por isso o 0024 rejeitou a
revogação total (linhas 56-58) com o argumento "por causa de uma skill que nem precisa disso".

O fato mudou. O dono adota o skill `/to-spec`, que **publica a spec (PRD) como corpo de issue**
no GitHub — e a regra padrão do 0024 bloqueia esse workflow de frente. Não é mais "uma skill que
não precisa": é um workflow real barrado por uma regra de governança. Num projeto solo cuja razão
de existir é reduzir atrito, regra com custo real contra workflow real é revogável — e regra que
o dono ressente vira letra morta, que é pior que regra nenhuma.

A alternativa "honesta-mas-errada" seria empilhar um terceiro carve-out (estender o regime-andaime
a specs do `/to-spec`). Isso é exatamente o acúmulo de regimes que um mantenedor solo não entende
em 6 meses. Revogação limpa é mais simples.

## Decisão

**Revogar a regra dos dois regimes.** Issues podem carregar conteúdo à vontade — specs/PRDs
(`/to-spec`), mapas de wayfinder, notas de design, o que for. Não há mais "regime padrão" nem
"regime andaime".

**Único guardrail preservado** (o pedaço puramente técnico do 0024 — evita o bug que o motivou:
cópia obsoleta apresentada como atual):

> **Cópia congelada nunca é canônica.**
> (a) Link para doc vivo = link de **branch**; SHA-pin só para código imóvel.
> (b) Corpo de issue que **duplica** artefato do repo é snapshot — **o repo é canônico**; em
> divergência, o corpo perde, sem discussão.

**Norma que deixa de existir:** a cláusula "o mapa morre no handoff `/to-spec`" do regime-andaime
não é mais norma do repo. Se mantida, é **prática da skill `/wayfinder`**, não regra de governança.

## Consequências

- **Positivo:** `/to-spec` e afins publicam conteúdo em issue sem atrito; um regime a menos pra
  manter; o guardrail único cobre o bug técnico (stale) sem reintroduzir burocracia.
- **Trade-off aceito:** corpo de issue pode divergir do artefato do repo — mitigado pelo guardrail
  (b): em divergência o repo vence, sem discussão.
- **Neutro:** ADR-0024 vira "substituído por ADR-0026"; a memória `github-issue-pointer-not-content`
  reduz-se ao guardrail único + a taxonomia de labels.

**Gatilho de reabertura:** primeira decisão tomada com base num corpo de issue **divergente** do
repo — aí o custo do stale voltou a ser real e este ADR precisa de dente (enforcement), não só de
princípio.

## Alternativas rejeitadas

- **Manter o ADR-0024:** bloqueia o `/to-spec` de frente; regra ressentida vira letra morta.
- **Terceiro carve-out (regime-andaime estendido a specs):** acúmulo de regimes — a complexidade
  que o projeto existe para evitar.
- **Preservar só a nuance de permalink como guardrail:** deixa aberto o buraco mais provável do
  regime novo — o corpo da issue divergindo do repo. Por isso o guardrail é um princípio com duas
  aplicações, não só a do link.
