"""Unit tests for welcome message builders (no network, no DB)."""

from __future__ import annotations

import html

from kubo.distribution.welcome import welcome_email, welcome_telegram_text


def test_welcome_telegram_text_contains_name_and_bardi_signature() -> None:
    """Telegram HTML message greets the recipient and signs as Bardi."""
    text = welcome_telegram_text("Renato")
    assert "<b>Olá, Renato!</b>" in text
    assert "Com carinho,\nBardi" in text


def test_welcome_telegram_text_escapes_html_in_name() -> None:
    """Name is HTML-escaped to prevent injection in the Telegram message."""
    text = welcome_telegram_text("<script>alert(1)</script>")
    assert html.escape("<script>alert(1)</script>") in text
    assert "<script>" not in text


def test_welcome_email_signs_as_bardi_in_both_parts() -> None:
    """Email text and HTML bodies both sign as Bardi (not Renato)."""
    subject, text_body, html_body = welcome_email("Claudia")
    assert subject == "Bem-vindo ao Kubo"
    assert "Olá, Claudia!" in text_body
    assert "Com carinho,\nBardi" in text_body
    assert "Olá, Claudia!</p>" in html_body
    assert "Com carinho,<br>Bardi</p>" in html_body


def test_welcome_email_escapes_html_in_name() -> None:
    """Name is HTML-escaped in the HTML body."""
    _, _, html_body = welcome_email("<b>evil</b>")
    assert html.escape("<b>evil</b>") in html_body
    assert "<b>evil</b></p>" not in html_body
