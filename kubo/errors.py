"""Exceções específicas do domínio Kubo.

Erros do runtime/worker retornam estruturados em RunResult (CLAUDE.md); estas
exceções são para falhas de configuração/programação que devem interromper.
"""

from __future__ import annotations

from pydantic import ValidationError


def format_validation_error(exc: ValidationError) -> str:
    """Formata um ValidationError lendo SÓ `loc`+`msg`, nunca `input`.

    O campo `input` (que `str(exc)` embute) carregaria o valor candidato —
    conteúdo coletado hostil ou um segredo colado por engano — para o
    ConfigError/ContractError/run.error. Formatador ÚNICO da fronteira; NÃO
    adicione `e['input']` a esta string."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


class KuboError(Exception):
    """Base de todas as exceções do domínio Kubo."""


class ConfigError(KuboError):
    """Configuração ausente ou inconsistente (ex.: credencial obrigatória faltando)."""


class DuplicateSourceError(KuboError):
    """Já existe um Cadastro de fonte com este (kind, canonical) — a store recusa a duplicata.

    Distinta de `StoreError` (que é 'statement revertido numa transação'): esta é uma
    decisão de negócio detectada por lookup ANTES da escrita — `create_source` recusa
    criar uma fonte que já existe (ADR-0025 §unicidade composta). O índice
    `UNIQUE(kind, canonical)` do banco segue como garantia dura; esta exceção dá à UI o
    sinal limpo para o aviso soft, sem parsear mensagem de erro do SurrealDB (frágil ao
    par SDK/server pinado). A mensagem carrega só `kind`/`canonical` (entrada do dono,
    não conteúdo coletado hostil)."""


class StoreError(KuboError):
    """Falha na camada de acesso ao datastore (ex.: statement revertido numa transação).

    O SurrealDB 3.x reverte uma transação com erro no meio mas NÃO propaga a falha
    via `query()` (ADR-0005); o wrapper transacional da store inspeciona todos os
    statements e levanta este erro quando algum falhou.
    """


class StateError(KuboError):
    """Transição de task inválida (ADR-0016 §II/§III).

    Levantada por `transition_task` quando o `from_state` não bate com o estado
    atual do task, ou quando o par `(from, to)` não está nas transições do
    `flow.snapshot` congelado. A validação é SEMPRE contra o snapshot, nunca contra
    o catálogo vivo — reescrever o template não afeta um flow em andamento
    (invariante 4)."""


class EmbeddingError(KuboError):
    """Falha ao gerar embedding (ADR-0006/0013).

    Levantada pelo cliente de embedding quando a API responde erro, devolve
    quantidade de vetores diferente da de textos, ou honra dimensão distinta
    da tripla pinada — um vetor com dimensão/tripla errada é incomparável e
    corromperia o espaço de busca, então a geração falha alto em vez de gravar.
    """


class ExecutorError(KuboError):
    """Base das falhas do executor de LLM (ADR-0013 §IV)."""


class MalformedOutputError(ExecutorError):
    """Saída do LLM não valida contra o schema esperado (ADR-0013 §IV).

    A mensagem NUNCA embute a saída crua do LLM nem o conteúdo coletado
    (ADR-0013 §VIII): saída malformada é rejeitada e contada, jamais aproveitada,
    e não se propaga o texto que quebrou para log/run.error. Não entra no backoff
    (retry de malformado queima quota do free tier e é induzível por item hostil)."""


class RateLimitExhausted(ExecutorError):
    """Quota/rate limit do provider estourou após o teto de tentativas (ADR-0013 §V).

    Falha SISTÊMICA (distinta de malformado por-item): o worker para o loop e
    devolve o parcial. A mensagem não propaga o corpo cru da resposta do provider
    (que pode embutir conteúdo/segredo — §VIII).

    `scope` discrimina a janela da quota a partir do header `retry-after` (0014 A2):
    `minute` (retry-after curto, janela de minuto — recuperável no próximo run),
    `day` (retry-after longo — TPD/RPD, o run desiste imediato) ou `unknown` (header
    ausente/não-numérico). O distiller mapeia `scope` para o `error.kind` visível em
    Execuções."""

    def __init__(self, message: str, *, scope: str = "unknown") -> None:
        """Guarda a `message` (já sanitizada pelo executor) e o `scope` da quota."""
        super().__init__(message)
        self.scope = scope


class SenderError(KuboError):
    """Falha ao entregar um digest por um canal (Telegram/e-mail, ADR-0015 §IV).

    A mensagem NUNCA embute o segredo do canal: o token do bot vai na URL do Bot
    API e as exceções httpx embutem a URL — o sender captura e SANITIZA (redação do
    token, análogo do `repr=False`) antes de construir este erro. O worker de digest
    a captura e a transforma num `dispatch(error)` estruturado, sem explodir o run."""


class ForgeError(KuboError):
    """Falha numa operação contra o repositório sandbox — git ou GitHub API (ADR-0019).

    A mensagem NUNCA embute o PAT nem o corpo cru do erro (git/httpx): o PAT viaja no
    header `Authorization` (open/close-PR) e no `http.extraHeader` do push (C2), e o corpo
    de um erro poderia carregá-lo — os módulos `gitops`/`github_api` descrevem a falha por
    status/tipo e redigem o PAT belt-and-suspenders antes de construir este erro (mesma
    disciplina do `SenderError`). O worker dev a captura e a transforma num `RunResult`
    estruturado, sem explodir o runtime."""


class PromotionError(KuboError):
    """A confirmação de promoção falhou uma validação do rito (ADR-0021 §2/§9).

    Duas causas, ambas com o gate SEGUINDO ABERTO (o dono relê e reclica): o PR ainda NÃO está
    mesclado na API do GitHub (aprovação ≠ merge — D38), ou o worker informado NÃO resolve no
    `WORKER_REGISTRY` do processo vivo (import-oráculo — o deploy não rodou; a mensagem manda
    rodar `./scripts/deploy.sh`). Distinta de `StateError` (board) e `ForgeError` (I/O de rede):
    é a fronteira do rito, traduzida pela rota num aviso visível, sem efeito colateral."""


class ContractError(KuboError):
    """Objeto não honra o contrato de worker (ADR-0009).

    Levantado por `validate_worker` quando `manifest` está ausente, não valida
    como `WorkerManifest`, ou `run` não é callable com a assinatura esperada.
    O runner chama `validate_worker` antes de abrir `run` — worker inválido
    nunca executa.
    """
