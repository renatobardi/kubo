"""Testes RED do chunking puro (ADR-0013 §II) — kubo/chunking.py.

Cobrem: estimativa de tokens, split por parágrafo com merge greedy, cascata
para sentença e hard-split por chars, ausência de overlap, determinismo e
preservação do conteúdo original.
"""

import re

from kubo.chunking import chunk_text, estimate_tokens


def _non_whitespace(text: str) -> str:
    """Remove todo whitespace, isolando só os caracteres de conteúdo."""
    return re.sub(r"\s+", "", text)


def test_estimate_tokens_texto_vazio_retorna_zero():
    """String vazia não consome tokens."""
    assert estimate_tokens("") == 0


def test_estimate_tokens_quatro_chars_e_um_token():
    """Heurística chars/4: 4 caracteres arredondam para 1 token."""
    assert estimate_tokens("abcd") == 1


def test_estimate_tokens_oito_chars_e_dois_tokens():
    """Heurística chars/4: 8 caracteres viram 2 tokens."""
    assert estimate_tokens("abcdefgh") == 2


def test_chunk_text_vazio_retorna_lista_vazia():
    """Texto vazio não gera chunk algum (nada para embeddar)."""
    assert chunk_text("") == []


def test_chunk_text_so_whitespace_retorna_lista_vazia():
    """Texto só com espaços/quebras de linha também não gera chunk."""
    assert chunk_text("   \n\n\t  \n  ") == []


def test_chunk_text_curto_vira_um_unico_chunk_strippado():
    """Texto abaixo do teto vira exatamente 1 chunk igual ao texto stripado."""
    text = "  Um resumo curto que cabe tranquilo no teto.  "
    result = chunk_text(text, max_tokens=1200)
    assert result == [text.strip()]


def test_paragrafos_curtos_sao_mergeados_em_um_chunk():
    """Dois parágrafos cuja soma cabe no teto são unidos por '\\n\\n'."""
    p1 = "Para um."
    p2 = "Para dois."
    text = f"{p1}\n\n{p2}"
    result = chunk_text(text, max_tokens=10)  # teto de 40 chars
    assert result == [f"{p1}\n\n{p2}"]


def test_paragrafos_que_excedem_teto_viram_multiplos_chunks_sem_overlap():
    """Parágrafos cuja soma excede o teto quebram em chunks na fronteira certa."""
    p1 = "A" * 30
    p2 = "B" * 30
    p3 = "C" * 30
    text = f"{p1}\n\n{p2}\n\n{p3}"
    max_tokens = 10  # teto de 40 chars: cada parágrafo cabe sozinho, dois não

    result = chunk_text(text, max_tokens=max_tokens)

    assert len(result) > 1
    assert all(estimate_tokens(c) <= max_tokens for c in result)
    joined = "".join(result)
    for paragraph in (p1, p2, p3):
        assert joined.count(paragraph) == 1


def test_paragrafo_unico_maior_que_teto_e_dividido_por_sentenca():
    """Um parágrafo só, sem linha em branco, mas grande demais, quebra por sentença."""
    s1 = "Frase um do teste aqui."
    s2 = "Frase dois do teste aqui tambem."
    s3 = "Frase tres do teste para fechar tudo bem."
    text = f"{s1} {s2} {s3}"
    max_tokens = 10  # teto de 40 chars; texto inteiro passa de 90 chars

    result = chunk_text(text, max_tokens=max_tokens)

    assert len(result) > 1
    assert all(estimate_tokens(c) <= max_tokens for c in result)
    assert _non_whitespace("".join(result)) == _non_whitespace(text)


def test_sentenca_gigante_sem_pontuacao_e_hard_split_por_chars():
    """Sentença patologicamente grande e sem pontuação vira hard-split por chars."""
    text = "x" * 100
    max_tokens = 5  # teto de 20 chars por chunk no hard split

    result = chunk_text(text, max_tokens=max_tokens)

    assert len(result) > 1
    max_chars = max_tokens * 4
    assert all(len(c) <= max_chars for c in result)
    assert all(c != "" for c in result)
    assert "".join(result) == text


def test_chunk_text_e_deterministico():
    """Chamar 2x com o mesmo input produz listas idênticas."""
    text = "Primeiro paragrafo aqui.\n\nSegundo paragrafo logo ali, bem maior que o primeiro."
    max_tokens = 10

    first = chunk_text(text, max_tokens=max_tokens)
    second = chunk_text(text, max_tokens=max_tokens)

    assert first == second


def test_chunk_text_preserva_todo_conteudo_na_mesma_ordem():
    """Todo caractere não-whitespace do original aparece nos chunks, na mesma ordem."""
    text = (
        "Paragrafo inicial com conteudo relevante para o teste.\n\n"
        "Paragrafo seguinte, tambem com bastante texto para forcar divisao."
    )
    max_tokens = 10

    result = chunk_text(text, max_tokens=max_tokens)

    assert _non_whitespace("".join(result)) == _non_whitespace(text)


def test_nenhum_chunk_e_vazio_ou_so_whitespace():
    """Em nenhum cenário um chunk deve ser vazio ou conter só whitespace."""
    textos_e_tetos = [
        ("Texto curto simples.", 1200),
        ("Para um.\n\nPara dois.", 10),
        ("A" * 30 + "\n\n" + "B" * 30 + "\n\n" + "C" * 30, 10),
        ("Frase um aqui. Frase dois aqui tambem. Frase tres para fechar.", 10),
        ("x" * 100, 5),
    ]
    for texto, max_tokens in textos_e_tetos:
        result = chunk_text(texto, max_tokens=max_tokens)
        assert all(c.strip() != "" for c in result)
