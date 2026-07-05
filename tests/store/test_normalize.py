"""Testes de `knowledge.normalize_entity` — função pura, sem banco (unit)."""

from __future__ import annotations

import unicodedata

from kubo.store.knowledge import normalize_entity


def test_case_and_surrounding_whitespace_collapse_to_the_same_value() -> None:
    """ "Python", com variação de caixa e espaço nas bordas, normaliza para um único valor."""
    variants = ["Python", "  python  ", "PYTHON", "python"]
    normalized = {normalize_entity(v) for v in variants}
    assert len(normalized) == 1


def test_internal_whitespace_run_collapses_to_single_space() -> None:
    """Múltiplos espaços internos colapsam para um único separador."""
    assert normalize_entity("foo   bar") == "foo bar"


def test_normalize_is_idempotent() -> None:
    """Normalizar um valor já normalizado é no-op (pré-condição do dedup por chave natural)."""
    once = normalize_entity("  Foo   Bar  ")
    assert normalize_entity(once) == once


def test_unicode_accent_form_is_folded_via_nfc() -> None:
    """ "café" composto (NFC, "é" como um único code point) e decomposto (NFD, "e"
    + combining acute) normalizam para o mesmo resultado — sem isso, o dedup de
    entidade vazaria por forma de representação Unicode, não por diferença real
    de conteúdo."""
    composed = unicodedata.normalize("NFC", "café")
    decomposed = unicodedata.normalize("NFD", "café")
    assert composed != decomposed  # pré-condição: os dois literais realmente diferem
    assert normalize_entity(composed) == normalize_entity(decomposed)
