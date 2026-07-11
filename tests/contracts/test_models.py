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
    DistilledPayload,
    EntityRef,
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
