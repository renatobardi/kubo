"""Protocol do contrato de worker e validação de runtime (ADR-0009).

`Worker`/`RunContext`/`KnowledgeReader` são Protocols — servem à checagem
estática do pyright, NÃO validam nada em runtime (`isinstance`/
`@runtime_checkable` só checam presença de membros, não a forma do manifest
nem a assinatura de `run` — falsa validação de uma fronteira de segurança,
ADR-0009 item I). A validação de runtime é a função explícita
`validate_worker`.

"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from kubo.contracts.models import RunResult, WorkerManifest
from kubo.embedding import Embedder
from kubo.errors import ContractError

_MISSING = object()


@dataclass(frozen=True)
class ItemView:
    """View read-only de um item pendente, entregue ao worker destilador
    (ADR-0013 §III.1).

    `ref` é OPACO: o worker não conhece o `RecordID` real, só um inteiro que
    identifica o item DENTRO do lote da run atual — resolvê-lo a RecordID é
    tarefa do runtime (`GraphKnowledge.resolve`), fora deste Protocol.
    `content` é conteúdo coletado (hostil): nunca interpolar, sempre tratar
    como entrada não-confiável.
    """

    ref: int
    title: str | None
    url: str | None
    content: str


@dataclass(frozen=True)
class DigestView:
    """View read-only de um item a incluir num digest (ADR-0015 §IV, ADR-0050 §V,
    ADR-0052 §I).

    `id` é a forma STRING do RecordID do ITEM (`item:<hex>`) — exceção nomeada à
    disciplina de ref opaco: o digest worker é MECÂNICO (sem LLM no circuito), a
    razão do ref opaco (LLM forjando alvos de escrita) não existe aqui. O id é
    leitura display-only (link da UI + auditoria em `dispatch.items`). A chave de
    identidade é o `item` (não o `distilled`): um item redestilado ganha id de
    distilled novo e reentraria como inédito se a chave fosse o destilado (ADR-0050
    §III). `score` é a nota de relevância (aresta `scored_for`). `published_at` é a
    data de publicação na fonte. `url` é o link do item na fonte. `title`/`summary`/
    `entities` são conteúdo derivado de dado HOSTIL — o builder os escapa sempre.
    `opinion` é o parecer editorial (ADR-0052 §I) — computado no envio, persistido
    por (item, tenant), compartilhado entre canais. `None` quando ainda não
    computado ou nas formas de aviso (empty_window/none_passed)."""

    id: str
    title: str | None
    summary: str
    score: int
    published_at: datetime
    url: str | None
    entities: list[str]
    opinion: str | None = None


@dataclass(frozen=True)
class DigestSelectionView:
    """Resultado da seleção do digest por janela de publicação (ADR-0050).

    `form` discrimina as quatro formas de mensagem (§VI):
    - "normal": houve conteúdo aprovado, o digest sai com as notícias.
    - "empty_window": a janela de publicação estava vazia na origem.
    - "none_passed": N publicações, nenhuma passou o corte (com o número).
    - "recovery": o dispatch cobre mais de um dia (janela elástica ativa).

    `items` são os itens selecionados (vazio nas formas 2/3). `window_start`/
    `window_end` são as fronteiras da janela de calendário. `watermark` é o
    último dia coberto pela janela (ADR-0050 §IV) — independe de haver itens.
    `total_publications` é o total de itens publicados na janela inteira (para
    a forma 3); na forma 4 (recovery), os itens vêm só do dia mais recente, mas
    `total_publications` cobre o período inteiro — essa assimetria é intencional:
    o número é sinal de calibragem do corte, não contagem do que foi enviado.
    `day_summary` é o resumo editorial do dia (ADR-0052 §II) — mesmo texto nos
    dois canais. `None` nas formas de aviso (empty_window/none_passed) ou quando
    não houve tempo de computar (destilador falhou e fallback ainda não rodou)."""

    form: Literal["normal", "empty_window", "none_passed", "recovery"]
    items: list[DigestView]
    window_start: datetime | None
    window_end: datetime | None
    watermark: datetime | None
    total_publications: int
    day_summary: str | None = None


@dataclass(frozen=True)
class RetrievedView:
    """View read-only de um distilled recuperado pela busca semântica, entregue à analista
    (ADR-0016 §III). `id` é a forma STRING do RecordID (`distilled:<hex>`): a analista tem
    LLM no circuito, mas o id vem do RETRIEVAL (não do LLM) — entra em `consulted` e vira link
    de citação, nunca forjável pela saída do modelo (regra das citações §VI). `summary` é
    conteúdo derivado de dado HOSTIL — vai como untrusted_content ao LLM, nunca como instrução."""

    id: str
    title: str | None
    summary: str


class KnowledgeReader(Protocol):
    """Seam de leitura do grafo entregue ao worker (ADR-0009 item VI, ADR-0013 §III.1).

    Métodos entram quando um worker exigir leitura do grafo, com teste que
    justifique — não se especula agora.
    """

    def items_to_score(self, limit: int) -> list[ItemView]:
        """Devolve até `limit` itens pendentes de PONTUAÇÃO (ADR-0051 §I), com
        `ref` opaco atribuído pelo runtime — nunca o RecordID real."""
        ...

    def work_context(self) -> str:
        """Contexto de trabalho do dono do TENANT ativo (ADR-0051 §I.1/Nota de
        compatibilidade) — string vazia se não houver. Nunca logado (PII)."""
        ...

    def search_distilled(self, embedding: Sequence[float], k: int) -> list[RetrievedView]:
        """Busca semântica no acervo para a analista (ADR-0016 §III): top-k distilled por
        proximidade, dedup por distilled. O worker recebe a lista pronta e nunca conhece
        RecordIDs — só a forma string do id (display/citação/`consulted`)."""
        ...

    def items_for_digest(self, destination: str, limit: int) -> DigestSelectionView:
        """Seleciona itens para o digest de `destination` por janela de publicação
        (ADR-0050): do dia seguinte ao último dispatch `ok` até ontem, com teto de
        7 dias. Exclui já-enviados, deduplica por URL, ordena por nota, corta em
        `limit`. Devolve `DigestSelectionView` com a forma (normal/empty/none_passed/
        recovery), os itens, a janela e o watermark (último dia coberto)."""
        ...

    def get_opinions(self, item_ids: list[str]) -> dict[str, str]:
        """Lê pareceres persistidos por (item, tenant) em lote (ADR-0052 §I).
        Chave = forma string do item id (`item:<hex>`), valor = texto do parecer.
        Itens sem parecer não aparecem no dict — o chamador detecta ausência por
        `key not in result` e computa o que falta via LLM."""
        ...

    def get_day_summary(self, day: date) -> str | None:
        """Lê o resumo do dia para o tenant (ADR-0052 §II). `day` é o dia de
        calendário no fuso do tenant. Retorna `None` se não existe — o chamador
        computa via LLM e devolve `DaySummaryPayload` para o runner persistir
        (caminho de fallback, ADR-0052 §III)."""
        ...


class RunContext(Protocol):
    """Contexto somente-leitura entregue ao worker (ADR-0009 item VI).

    O worker nunca recebe handle de `db` — persistir é do runtime.

    Membros são `@property` (só-leitura) de propósito: atributo mutável de
    Protocol é INVARIANTE, e o ctx concreto (dataclass frozen) usa tipos mais
    estreitos (`Mapping[str, ResolvedIntegration]`, `GraphKnowledge`) — sob
    invariância ele não satisfaria este Protocol. Property de leitura é
    covariante, então o concreto o honra, coerente com "somente-leitura".
    """

    @property
    def config(self) -> BaseModel: ...
    @property
    def integrations(self) -> Mapping[str, Any]: ...
    @property
    def knowledge(self) -> KnowledgeReader: ...
    @property
    def logger(self) -> Any: ...
    @property
    def embedder(self) -> Embedder | None: ...


class Worker(Protocol):
    """O que o pyright vê: manifest declarado + `run(ctx) -> RunResult` (ADR-0009 item I)."""

    manifest: WorkerManifest

    def run(self, ctx: RunContext) -> RunResult: ...


def _safe_getattr(obj: object, name: str) -> object:
    """Lê um atributo tratando QUALQUER falha como ausência.

    `getattr(obj, name, default)` só cai no default em AttributeError — uma
    property/descriptor hostil que levanta outra exceção propagaria e faria
    `validate_worker` explodir (em vez de rejeitar com ContractError). Aqui, um
    worker não-confiável que estoura no acesso vira simplesmente "ausente"."""
    try:
        return getattr(obj, name, _MISSING)
    except Exception:  # noqa: BLE001 — fronteira: descriptor hostil vira ausência, não crash
        return _MISSING


def validate_worker(obj: object) -> WorkerManifest:
    """Valida que `obj` honra o contrato de worker; retorna o manifest validado.

    Checa: (a) `obj.manifest` existe e é validável como `WorkerManifest`; (b)
    `obj.run` é callable com a assinatura esperada (um parâmetro posicional
    além de `self`). Falha em qualquer uma das duas condições levanta
    `ContractError` (ADR-0009 item V).

    O retorno é o manifest VALIDADO — o runner usa esse retorno e nunca relê
    `obj.manifest` depois, fechando o TOCTOU de um worker hostil que expõe
    `manifest` como property inconsistente entre leituras.
    """
    raw = _safe_getattr(obj, "manifest")
    if raw is _MISSING:
        raise ContractError("worker não expõe um atributo `manifest` legível")
    try:
        manifest = WorkerManifest.model_validate(raw)
    except ValidationError as exc:
        # Sem str(exc): não propaga o input_value (que poderia carregar valor
        # sensível colado no manifest) para o ContractError/log.
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        raise ContractError(f"manifest do worker é inválido (campos: {fields})") from exc

    run = _safe_getattr(obj, "run")
    if not callable(run):
        raise ContractError("worker.run não é callable")
    try:
        signature = inspect.signature(run)
    except (TypeError, ValueError) as exc:  # callable sem assinatura inspecionável
        raise ContractError("worker.run não tem assinatura inspecionável") from exc
    # `run` é lido como método vinculado — `self` já não aparece; sobra só o ctx.
    positional = [
        p
        for p in signature.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) != 1:
        raise ContractError(
            f"worker.run deve aceitar exatamente um parâmetro (ctx); tem {len(positional)}"
        )
    return manifest
