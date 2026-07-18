# Kubo — Glossário

Linguagem ubíqua do Kubo. Glossário, não spec: define o que os termos **são**, não como
são implementados. A fonte de escopo/conceitos continua sendo `docs/kubo-spec-funcional.md`;
aqui ficam os termos que precisaram ser afiados por decisão explícita (com ADR quando a
escolha é difícil de reverter).

## Coleta de conhecimento

**Fonte** (origem):
A coisa lá fora de onde o conhecimento é coletado — um feed RSS, um repositório do GitHub, um
canal. Identificada pelo seu endereço canônico (a URL). É referida por um [[Cadastro de fonte]],
não é ela mesma um registro no banco.
_Evite_: "source" solto para se referir ao cadastro (ver abaixo).

**Cadastro de fonte**:
O registro no grafo que representa uma [[Fonte]] que o Kubo coleta. Tem identidade própria
(um id que não é a URL), é gerido pelo dono na UI, e é o que **dirige a coleta** — o
agendador coleta as fontes a partir dos cadastros habilitados. Um cadastro pode ter sua URL
editada sem perder o histórico já coletado. Ver ADR-0025.
_Evite_: chamar o cadastro de "fonte" quando a distinção importa; "assinatura".

**Item**:
Uma unidade de conteúdo coletada de uma fonte (um post de feed, uma release). Aponta para o
[[Cadastro de fonte]] de onde veio — essa ligação é a proveniência da qual pende toda a
destilação. Um item nunca fica órfão de cadastro.

**kind**:
O tipo de uma [[Fonte]] (ex.: `rss`, `github-repo`). É a chave que decide qual coletor roda
para aquela fonte — mapeamento fixo em código, nunca configurável como dado. Ver ADR-0025
(despacho por kind).

**Sweep**:
A passada de coleta em horário fixo que varre todos os [[Cadastro de fonte|cadastros]]
habilitados e dispara um run por cadastro. Contrasta com agendamento por-fonte (adiado). O
relógio fixo diz *quando*; o cadastro diz *o quê*; o código diz *como*. Ver ADR-0025.
