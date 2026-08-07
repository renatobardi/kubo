"""Normalização de URL para dedup do digest (ADR-0050 §III, KUBO-194).

Unit puro: a normalização pega o caso fácil (trailing slash, http/https,
parâmetros de rastreamento) — não pega republicação com URL genuinamente
diferente (limite aceito no ADR).
"""

from __future__ import annotations

from kubo.store.knowledge import normalize_url


def test_strips_trailing_slash() -> None:
    assert normalize_url("https://example.com/article/") == normalize_url(
        "https://example.com/article"
    )


def test_normalizes_http_to_https() -> None:
    assert normalize_url("http://example.com/article") == normalize_url(
        "https://example.com/article"
    )


def test_strips_tracking_params() -> None:
    url_with_utm = "https://example.com/article?utm_source=newsletter&utm_medium=email"
    url_clean = "https://example.com/article"
    assert normalize_url(url_with_utm) == normalize_url(url_clean)


def test_strips_ref_and_source_params() -> None:
    url_with_ref = "https://example.com/article?ref=rss&source=feed"
    url_clean = "https://example.com/article"
    assert normalize_url(url_with_ref) == normalize_url(url_clean)


def test_preserves_non_tracking_params() -> None:
    url_with_id = "https://example.com/article?id=123"
    url_clean = "https://example.com/article"
    assert normalize_url(url_with_id) != normalize_url(url_clean)


def test_lowercases_scheme_and_host() -> None:
    assert normalize_url("HTTPS://Example.COM/Article") == normalize_url(
        "https://example.com/Article"
    )


def test_none_url_returns_none() -> None:
    assert normalize_url(None) is None


def test_empty_url_returns_none() -> None:
    assert normalize_url("") is None


def test_different_paths_are_not_equivalent() -> None:
    assert normalize_url("https://example.com/a") != normalize_url("https://example.com/b")


def test_case_sensitive_path() -> None:
    """Path é case-sensitive — /Article e /article são URLs diferentes."""
    assert normalize_url("https://example.com/Article") != normalize_url(
        "https://example.com/article"
    )


def test_normalized_form_is_stable() -> None:
    """Prende a forma concreta da saída — as outras asserções são relativas."""
    assert (
        normalize_url("HTTP://Example.COM/article/?utm_source=x&id=7")
        == "https://example.com/article?id=7"
    )


def test_url_without_scheme_returns_none() -> None:
    assert normalize_url("example.com/article") is None


def test_url_without_host_returns_none() -> None:
    assert normalize_url("https:///article") is None


def test_query_param_order_does_not_affect_dedup() -> None:
    """Parâmetros em ordem diferente produzem a mesma normalização."""
    assert normalize_url("https://example.com/a?p=1&q=2") == normalize_url(
        "https://example.com/a?q=2&p=1"
    )
