# ADR-0032 — LLM síncrono em rota HTTP para descoberta de feed RSS

> Status: aceito · Data: 2026-07-20

## Contexto

O cadastro de fonte RSS (KUBO-50) introduz três modos de entrada: URL direta de feed, URL de site com autodiscovery e nome da empresa com chute da persona `finder`. Os dois últimos exigem feedback imediato na UI ("Testar"), antes de salvar. Até aqui todo uso de LLM no Kubo passava por flow/task assíncrono com gate humano (ADR-0016); a descoberta assistida é a primeira operação interativa que precisa de uma resposta síncrona.

## Decisão

A rota `POST /sources/test` chama a persona `finder` de forma síncrona dentro do request HTTP. A resposta da IA é tratada como sugestão e sempre validada por um fetch+parse real antes de ser apresentada ao dono; a rota nunca persiste nada (dry-run). O timeout do caminho interativo é curto, e falhas do provider ou output malformado degradam para "não achei" em vez de 500.

## Consequências

- O dono recebe feedback no momento do cadastro, sem esperar um job ou sweep.
- A superfície de prompt injection é reduzida: o `finder` só vê o texto que o próprio dono digitou; HTML ou conteúdo de terceiros nunca entra no prompt.
- A IA não decide sozinha: o chute é validado por fetch real; se falhar, tenta autodiscovery no domínio chutado; se ainda falhar, a UI pede entrada manual.
- O fetch da URL inicial no caminho do route não herda a isenção do `FeedWorker` (cuja URL vem de `schedules.yaml` do dono). Todo URL vindo do formulário, autodiscovery ou chute da IA passa pelo guard SSRF completo, incluindo a primeira requisição.
- A resposta é uma render parcial HTML (HTMX) para atualização inline da sheet.

## Alternativas rejeitadas

- **Job em background + polling:** over-engineering para um mantenedor solo; adicionaria estado, fila e uma segunda tela sem ganho proporcional.
- **Chamada direta a provider no route:** quebraria o seam `Executor` e dificultaria testes com fakes (regra do projeto: LLMs sempre mockados).
