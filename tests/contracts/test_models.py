"""Contrato dos modelos novos da destilação (ADR-0013 §III, Marco 8.6).

RED da fase 8.6: `EntityRef`, `ChunkPayload` e `DistilledPayload` ainda são
STUBS sem as cercas de volume (min_length/max_length) e sem widening da união
`Payload` — os testes de construção básica passam, mas os de constraint e de
discriminação da união falham por comportamento ausente (ainda não é
ImportError/erro de sintaxe).
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from kubo.contracts.models import (
    ChunkPayload,
    DispatchPayload,
    DistilledPayload,
    EntityRef,
    ErrorInfo,
    ItemPayload,
    Payload,
    SourcePayload,
)

# ---------------------------------------------------------------------------
# EntityRef
# ---------------------------------------------------------------------------


def test_entity_ref_valido_instancia() -> None:
    """EntityRef(name=..., kind=...) instancia com os dois campos corretos."""
    ref = EntityRef(name="Anthropic", kind="org")

    assert ref.name == "Anthropic"
    assert ref.kind == "org"


def test_entity_ref_kind_default_none() -> None:
    """`kind` é opcional — default None quando o worker não sabe classificar."""
    ref = EntityRef(name="Anthropic")

    assert ref.kind is None


def test_entity_ref_rejeita_name_vazio() -> None:
    """`name=""` deve ser rejeitado (min_length=1) — entidade sem nome não
    tem chave natural para `get_or_create_entity` resolver (ADR-0013 §III.4)."""
    with pytest.raises(ValidationError):
        EntityRef(name="")


def test_entity_ref_rejeita_name_acima_de_200() -> None:
    """`name` tem teto de 200 — cerca de volume contra payload hostil/degenerado."""
    with pytest.raises(ValidationError):
        EntityRef(name="x" * 201)


def test_entity_ref_rejeita_kind_acima_de_50() -> None:
    """`kind`, quando presente, tem teto de 50."""
    with pytest.raises(ValidationError):
        EntityRef(name="Anthropic", kind="x" * 51)


def test_entity_ref_rejeita_campo_extra() -> None:
    """extra="forbid": campo com nome errado é rejeitado, não descartado."""
    with pytest.raises(ValidationError):
        EntityRef(name="Anthropic", bogus_field="não deveria existir")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ChunkPayload
# ---------------------------------------------------------------------------


def test_chunk_payload_valido_espelha_chunk_da_store() -> None:
    """ChunkPayload espelha 1:1 o dataclass Chunk de kubo/store/knowledge.py."""
    chunk = ChunkPayload(
        text="trecho do item",
        seq=0,
        embedding=[0.1, 0.2, 0.3],
        model="text-embedding-004",
        dim=3,
        task_type="RETRIEVAL_DOCUMENT",
    )

    assert chunk.text == "trecho do item"
    assert chunk.seq == 0
    assert chunk.embedding == [0.1, 0.2, 0.3]
    assert chunk.model == "text-embedding-004"
    assert chunk.dim == 3
    assert chunk.task_type == "RETRIEVAL_DOCUMENT"


def test_chunk_payload_rejeita_dim_diferente_do_tamanho_do_embedding() -> None:
    """`dim` deve bater com `len(embedding)` — a store já valida, mas o contrato é a
    fronteira de segurança: falha mais rápido, mais perto do worker que montou o
    chunk (Trivial, achado de review). Mensagem clara, sem embutir o embedding."""
    with pytest.raises(ValidationError):
        ChunkPayload(text="x", seq=0, embedding=[0.1, 0.2], model="m", dim=3, task_type="t")


def test_chunk_payload_aceita_dim_igual_ao_tamanho_do_embedding() -> None:
    """`dim` batendo com `len(embedding)` instancia normalmente."""
    chunk = ChunkPayload(text="x", seq=0, embedding=[0.1, 0.2], model="m", dim=2, task_type="t")

    assert chunk.dim == 2
    assert len(chunk.embedding) == 2


def test_chunk_payload_rejeita_campo_extra() -> None:
    """extra="forbid" no ChunkPayload — mesma disciplina dos outros payloads."""
    with pytest.raises(ValidationError):
        ChunkPayload(
            text="trecho",
            seq=0,
            embedding=[0.1],
            model="text-embedding-004",
            dim=1,
            task_type="RETRIEVAL_DOCUMENT",
            bogus_field="não deveria existir",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# DistilledPayload
# ---------------------------------------------------------------------------


def test_distilled_payload_valido_instancia_com_defaults() -> None:
    """DistilledPayload(ref=..., summary=...) instancia; type/schema_version
    vêm por default e entities/chunks aceitam os payloads embutidos."""
    payload = DistilledPayload(
        ref=0,
        summary="resumo",
        entities=[EntityRef(name="X")],
        chunks=[
            ChunkPayload(
                text="trecho",
                seq=0,
                embedding=[0.1],
                model="text-embedding-004",
                dim=1,
                task_type="RETRIEVAL_DOCUMENT",
            )
        ],
    )

    assert payload.type == "distilled"
    assert payload.schema_version == 1
    assert payload.ref == 0
    assert payload.summary == "resumo"
    assert payload.entities == [EntityRef(name="X")]
    assert len(payload.chunks) == 1


def test_distilled_payload_ref_aceita_int_e_defaults_vazios() -> None:
    """`ref` aceita qualquer int (é opaco pro worker); entities/chunks default []."""
    payload = DistilledPayload(ref=3, summary="resumo")

    assert payload.ref == 3
    assert payload.entities == []
    assert payload.chunks == []


def test_distilled_payload_rejeita_summary_vazio() -> None:
    """`summary=""` deve ser rejeitado (min_length=1) — destilado sem
    conteúdo não tem o que persistir."""
    with pytest.raises(ValidationError):
        DistilledPayload(ref=0, summary="")


def test_distilled_payload_rejeita_summary_acima_de_8000() -> None:
    """`summary` tem teto de 8000 — cerca de volume."""
    with pytest.raises(ValidationError):
        DistilledPayload(ref=0, summary="x" * 8001)


def test_distilled_payload_rejeita_mais_de_20_entities() -> None:
    """`entities` tem teto de 20 (cerca de volume; §III.4/advisor — `mentions`,
    diferente disso, é permanente e não leva teto)."""
    with pytest.raises(ValidationError):
        DistilledPayload(
            ref=0,
            summary="resumo",
            entities=[EntityRef(name=f"e{i}") for i in range(21)],
        )


def test_distilled_payload_rejeita_campo_extra() -> None:
    """extra="forbid" no DistilledPayload — mesma disciplina dos outros payloads."""
    with pytest.raises(ValidationError):
        DistilledPayload(
            ref=0,
            summary="resumo",
            bogus_field="não deveria existir",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Discriminação da união Payload
# ---------------------------------------------------------------------------


def test_payload_uniao_resolve_distilled_pelo_discriminador() -> None:
    """A união `Payload` deve incluir DistilledPayload — `type="distilled"`
    resolve para a classe certa (ADR-0013 §III.2), sem quebrar os membros
    já existentes da união."""
    adapter = TypeAdapter(Payload)

    resolved = adapter.validate_python({"type": "distilled", "ref": 0, "summary": "r"})

    assert isinstance(resolved, DistilledPayload)


def test_payload_uniao_continua_resolvendo_source_e_item() -> None:
    """Widening da união não pode quebrar a discriminação de source/item
    já existente."""
    adapter = TypeAdapter(Payload)

    source = adapter.validate_python(
        {"type": "source", "kind": "rss", "canonical": "https://x/feed"}
    )
    item = adapter.validate_python(
        {
            "type": "item",
            "source": {"type": "source", "kind": "rss", "canonical": "https://x/feed"},
            "external_id": "ep-1",
            "content": "conteúdo bruto",
        }
    )

    assert isinstance(source, SourcePayload)
    assert isinstance(item, ItemPayload)


# ---------------------------------------------------------------------------
# DispatchPayload (ADR-0015) — validação de items + error estruturado
# ---------------------------------------------------------------------------

_WM = "2026-07-13T09:30:00+00:00"


def _dispatch(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "dispatch",
        "destination": "owner-telegram",
        "channel": "telegram",
        "status": "ok",
        "artifact": "digest",
        "watermark": _WM,
        "item_count": 1,
        "items": ["distilled:abc123"],
    }
    base.update(kw)
    return base


def test_dispatch_payload_accepts_valid() -> None:
    """Um dispatch bem formado valida, com items em forma `distilled:<hex>`."""
    d = DispatchPayload.model_validate(_dispatch())
    assert d.items == ["distilled:abc123"]
    assert d.error is None


def test_dispatch_payload_rejects_unicode_item_id() -> None:
    """Item com id não-ASCII (borda contra id forjado) é rejeitado."""
    with pytest.raises(ValidationError):
        DispatchPayload.model_validate(_dispatch(items=["distilled:café"]))


def test_dispatch_payload_rejects_non_distilled_item() -> None:
    """Item que não é id de distilled é rejeitado."""
    with pytest.raises(ValidationError):
        DispatchPayload.model_validate(_dispatch(items=["run:abc123"]))


def test_dispatch_payload_error_is_structured() -> None:
    """`error` é ErrorInfo (extra=forbid, message<=500) — não dict solto."""
    d = DispatchPayload.model_validate(
        _dispatch(status="error", error={"kind": "telegram_send", "message": "HTTP 400"})
    )
    assert isinstance(d.error, ErrorInfo)
    assert d.error.kind == "telegram_send"
    with pytest.raises(ValidationError):
        DispatchPayload.model_validate(_dispatch(error={"kind": "x", "message": "y", "boom": 1}))


def test_dispatch_requires_artifact() -> None:
    """`artifact` sem default (fix E1): omiti-lo é ValidationError, não vira digest em
    silêncio — omitir num report moveria o watermark do digest."""
    base = _dispatch()
    del base["artifact"]
    with pytest.raises(ValidationError):
        DispatchPayload.model_validate(base)


def test_dispatch_report_has_no_watermark() -> None:
    """Um report valida SEM watermark (não move a marca-d'água do acervo)."""
    d = DispatchPayload.model_validate(_dispatch(artifact="report", watermark=None, items=[]))
    assert d.artifact == "report"
    assert d.watermark is None


def test_dispatch_report_rejects_watermark() -> None:
    """Um report COM watermark é rejeitado — o validador cruza artifact↔watermark."""
    with pytest.raises(ValidationError):
        DispatchPayload.model_validate(_dispatch(artifact="report"))


def test_dispatch_digest_requires_watermark() -> None:
    """Um digest SEM watermark é rejeitado (o watermark é a semântica do digest)."""
    with pytest.raises(ValidationError):
        DispatchPayload.model_validate(_dispatch(artifact="digest", watermark=None))
