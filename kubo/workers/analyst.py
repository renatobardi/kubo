"""Worker `analista` — síntese sob demanda sobre o acervo (ADR-0016 §III/§VI).

Uma pergunta → embed → busca semântica (seam `search_distilled`) → síntese PT-BR via
executor → relatório markdown que vira `deliverable` no grafo + entrega no Telegram.

Duas regras de segurança são estruturais aqui, não disciplina:
1. **Citações NUNCA via LLM (§VI):** o modelo produz só o texto (`ReportOutput.report`);
   a lista de fontes (títulos + links) é apêndice PROGRAMÁTICO do conjunto recuperado, e
   `consulted` deriva do retrieval. Injeção num documento não forja proveniência.
2. **D6:** a pergunta do dono → `instruction` (confiável, higiene barata: cap no schema +
   strip de controle); os summaries top-k → `untrusted_content` com separadores
   `[DOCUMENTO N]` montados em código, jamais interpolados na instrução.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from kubo.contracts.models import (
    DispatchPayload,
    ErrorInfo,
    ReportPayload,
    RunResult,
    Stats,
    WorkerManifest,
)
from kubo.contracts.worker import RetrievedView, RunContext
from kubo.distribution.destinations import ResolvedDestination
from kubo.distribution.telegram import send_telegram
from kubo.errors import ConfigError, ContractError, SenderError

# Sender de um canal (assinatura por-keyword de `send_telegram`); injetável para teste.
Sender = Callable[..., None]
_MSG_CAP = 500  # teto da mensagem de erro (ADR-0009 §VIII) — sem vazar conteúdo/segredo
_TELEGRAM_LIMIT = 4096  # uma mensagem só (Bot API)
_TITLE_CAP = 120  # teto por título nas fontes (o link ocupa o resto da linha)
_NO_TITLE = "(sem título)"
_NO_SOURCES = "Não encontrei fontes no acervo para responder a esta pergunta."
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class AnalystConfig(BaseModel):
    """Config da analista: a pergunta do dono + o top-k da recuperação (ADR-0016 §III).

    `question` tem cap no schema (higiene barata de D6: a pergunta é confiável, mas o
    tamanho é limitado antes de virar instrução). `k` é config de worker, não budget (§VIII)."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    k: int = 8


class ReportOutput(BaseModel):
    """Schema de saída do LLM — SÓ o texto do relatório (§VI: citações nunca via LLM).

    Sem campo de fontes: a proveniência é apêndice programático do retrieval, nunca ecoada
    pelo modelo — fecha o canal de injeção que forjaria uma citação."""

    model_config = ConfigDict(extra="forbid")

    report: str = Field(min_length=1)


class AnalystWorker:
    """Produz um relatório de análise sobre o acervo e o entrega no Telegram.

    `executor` (LLM) e `prompt` (instrução congelada da persona) são injetados; o worker
    NÃO lê o catálogo nem `os.environ`. `destination`/`base_url`/`senders` vêm resolvidos do
    flow runner (env), como no DigestWorker. `embedder` vem do ctx (busca semântica)."""

    manifest = WorkerManifest(
        name="analista", version="1", integrations=["telegram"], config=AnalystConfig
    )

    def __init__(
        self,
        executor: object,
        *,
        prompt: str,
        destination: ResolvedDestination,
        base_url: str,
        senders: Mapping[str, Sender] | None = None,
    ) -> None:
        """Guarda o executor (seam), o prompt congelado da persona, o destino resolvido, a
        base URL dos links e o mapa de senders por canal (default = sender real do Telegram)."""
        self._executor = executor
        self._prompt = prompt
        self._destination = destination
        self._base_url = base_url
        self._senders: Mapping[str, Sender] = senders or {"telegram": send_telegram}

    def run(self, ctx: RunContext) -> RunResult:
        """Recupera top-k, sintetiza, monta as citações programaticamente e entrega.

        Devolve `[ReportPayload, DispatchPayload]`: o relatório vira deliverable no grafo
        (via FlowCtx no runner) e a entrega vira fato de dispatch (artifact=report — NÃO move
        o watermark do digest, fix E1). Falha de envio → dispatch(error) + ErrorInfo, o
        deliverable permanece no grafo (o produto é o deliverable; Telegram é entrega)."""
        config = ctx.config
        if not isinstance(config, AnalystConfig):  # narrowing (padrão do FeedWorker)
            raise ContractError(
                f"AnalystWorker recebeu config {type(config).__name__}, esperava AnalystConfig"
            )
        embedder = ctx.embedder
        if embedder is None:
            raise ConfigError("worker analista requer embedder no ctx")

        vector = embedder.embed([config.question])[0]
        docs = ctx.knowledge.search_distilled(vector, config.k)
        report_text = self._synthesize(config.question, docs)

        markdown = _render_markdown(report_text, docs, self._base_url)
        consulted = [doc.id for doc in docs]
        payloads: list[ReportPayload | DispatchPayload] = [
            ReportPayload(content=markdown, consulted=consulted)
        ]

        error = self._deliver(ctx, report_text, docs, consulted, payloads)
        stats = Stats.model_validate({"sources": len(docs), "delivered": 0 if error else 1})
        return RunResult(payloads=list(payloads), stats=stats, error=error)

    def _synthesize(self, question: str, docs: list[RetrievedView]) -> str:
        """Chama o LLM com a pergunta (instrução) + summaries (untrusted). Acervo vazio =
        resposta honesta fixa, sem gastar chamada. A saída é só o texto (§VI)."""
        if not docs:
            return _NO_SOURCES
        instruction = self._compose_instruction(question)
        untrusted = _build_untrusted(docs)
        out = self._executor.complete(instruction, untrusted, ReportOutput)  # type: ignore[attr-defined]
        return out.report

    def _compose_instruction(self, question: str) -> str:
        """Instrução = prompt congelado da persona + a pergunta do dono, higienizada
        (strip de controle; o cap já veio do schema). A pergunta é confiável (D6)."""
        clean = _CONTROL_CHARS_RE.sub("", question).strip()
        return f"{self._prompt}\n\nPergunta do dono: {clean}"

    def _deliver(
        self,
        ctx: RunContext,
        report_text: str,
        docs: list[RetrievedView],
        consulted: list[str],
        payloads: list[ReportPayload | DispatchPayload],
    ) -> ErrorInfo | None:
        """Envia o relatório ao destino e anexa o DispatchPayload (ok|error). Devolve
        ErrorInfo se o envio falhou (o run fecha em erro; o deliverable já está no payload)."""
        try:
            self._send(ctx, report_text, docs)
        except SenderError as exc:
            ctx.logger.warning("analyst.send_failed", destination=self._destination.id)
            payloads.append(_dispatch(self._destination, consulted, status="error", exc=exc))
            return ErrorInfo(kind=f"{self._destination.channel}_send", message=str(exc)[:_MSG_CAP])
        payloads.append(_dispatch(self._destination, consulted, status="ok"))
        return None

    def _send(self, ctx: RunContext, report_text: str, docs: list[RetrievedView]) -> None:
        """Monta a mensagem de texto do canal e envia; levanta SenderError se não puder."""
        dest = self._destination
        sender = self._senders.get(dest.channel)
        if sender is None:
            raise SenderError(f"canal {dest.channel!r} sem sender configurado")
        if dest.channel != "telegram":  # e-mail/etc não suportados nesta sessão
            raise SenderError(f"canal {dest.channel!r} não suportado nesta sessão")
        token = _integration_secret(ctx, "telegram")
        text = _render_telegram(report_text, docs, self._base_url)
        sender(token=token, chat_id=dest.address, text=text, parse_mode=None)


def _integration_secret(ctx: RunContext, name: str) -> str:
    """Lê o segredo resolvido de uma integração do ctx; ausente → SenderError (o worker
    nunca lê env, e um token faltando é falha de ENTREGA, não crash do run)."""
    integration = ctx.integrations.get(name)
    secret = getattr(integration, "secret", None)
    if not secret:
        raise SenderError(f"integração {name!r} sem segredo resolvido no ctx")
    return str(secret)


def _build_untrusted(docs: list[RetrievedView]) -> str:
    """Monta o bloco untrusted com separadores `[DOCUMENTO N]` EM CÓDIGO (D6) — os
    summaries são hostis, nunca interpolados na instrução."""
    blocks = [
        f"[DOCUMENTO {i}] {doc.title or _NO_TITLE}\n{doc.summary}"
        for i, doc in enumerate(docs, start=1)
    ]
    return "\n\n".join(blocks)


def _key(distilled_id: str) -> str:
    """A KEY de um id `distilled:<hex>` (sem o prefixo) — para o link `/distilled/<key>`."""
    return distilled_id.partition(":")[2]


def _source_title(title: str | None) -> str:
    """Título de uma fonte, capado — um título gigante não pode inflar a lista de fontes a
    ponto de o slice de 4096 do Telegram cortar um link no meio."""
    text = title or _NO_TITLE
    return text if len(text) <= _TITLE_CAP else text[: _TITLE_CAP - 1].rstrip() + "…"


def _render_markdown(report_text: str, docs: list[RetrievedView], base_url: str) -> str:
    """Relatório markdown COMPLETO para o `deliverable` (§VI: fontes = apêndice
    programático do retrieval). Título+link por documento recuperado."""
    body = report_text.strip()
    if not docs:
        return body
    lines = [f"- [{_source_title(doc.title)}]({base_url}/distilled/{_key(doc.id)})" for doc in docs]
    return f"{body}\n\n## Fontes\n" + "\n".join(lines)


def _render_telegram(report_text: str, docs: list[RetrievedView], base_url: str) -> str:
    """Mensagem de TEXTO PURO para o Telegram (parse_mode=None: sem HTML, sem escaping — o
    texto do LLM vai como está). Prosa + fontes, truncando a PROSA (não as fontes) para caber
    em 4096; a lista de fontes vem do retrieval (§VI)."""
    prose = report_text.strip()
    if not docs:
        return prose[:_TELEGRAM_LIMIT]
    source_lines = [
        f"- {_source_title(doc.title)}: {base_url}/distilled/{_key(doc.id)}" for doc in docs
    ]
    sources = "Fontes:\n" + "\n".join(source_lines)
    budget = _TELEGRAM_LIMIT - len(sources) - 2  # reserva os dois chars do separador prosa↔fontes
    if len(prose) > budget:
        prose = prose[: max(0, budget - 1)].rstrip() + "…"
    return f"{prose}\n\n{sources}"[:_TELEGRAM_LIMIT]


def _dispatch(
    dest: ResolvedDestination,
    consulted: list[str],
    *,
    status: str,
    exc: SenderError | None = None,
) -> DispatchPayload:
    """DispatchPayload de report (artifact=report, watermark None — NÃO move o watermark do
    digest, fix E1). `items` = as fontes consultadas (auditoria)."""
    return DispatchPayload(
        destination=dest.id,
        channel=dest.channel,
        status=status,  # type: ignore[arg-type]  # "ok"|"error" garantido pelo chamador
        artifact="report",
        watermark=None,
        item_count=len(consulted),
        items=consulted,
        error=ErrorInfo(kind=f"{dest.channel}_send", message=str(exc)[:_MSG_CAP]) if exc else None,
    )
