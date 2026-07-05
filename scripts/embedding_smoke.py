#!/usr/bin/env python3
"""Smoke ao vivo de embedding — valida ordenação de similaridade semântica em PT-BR.

Probe standalone (fora de `kubo/`, stdlib pura — sem httpx nem litellm; ver plano
0002 §2.1.6). Cada caso é um trio (âncora, paráfrase, distrator): a paráfrase diz
o mesmo com OUTRAS palavras; o distrator COMPARTILHA uma palavra com a âncora mas
em outro sentido (polissemia PT-BR: banco, fonte, prova, luz, remédio). Um embedding
semântico bom ordena sim(âncora, paráfrase) > sim(âncora, distrator) em todos os
trios — casamento lexical (BoW) falharia nos distratores.

Uso (key SÓ por env, invariante 8; NUNCA no CI nem na suite):
    GEMINI_API_KEY=... uv run python scripts/embedding_smoke.py
    EMBED_MODEL=gemini-embedding-001 EMBED_DIM=3072 uv run python scripts/embedding_smoke.py

Saída: tabela PT-BR + resumo. Exit 0 se todos os trios ordenam certo, 1 se algum
inverte (o probe É a asserção), 2 em erro de API/config.
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.error
import urllib.request

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"

# Trios (âncora, paráfrase, distrator). Distrator dos casos 6–10 usa polissemia:
# mesma palavra, sentido diferente — o teste duro de semântica sobre léxico.
TRIOS: list[tuple[str, str, str]] = [
    (
        "O gato dormia no sofá durante a tarde.",
        "Durante a tarde, o felino cochilava no divã.",
        "O gato derrubou o vaso da sala.",
    ),
    (
        "A reunião foi adiada para a próxima semana.",
        "O encontro foi remarcado para os próximos dias.",
        "A reunião durou mais de três horas.",
    ),
    (
        "O desenvolvedor corrigiu o erro no código.",
        "O programador consertou a falha no sistema.",
        "O código-fonte tem mais de mil linhas.",
    ),
    (
        "Choveu muito ontem à noite na cidade.",
        "Houve uma forte tempestade na região durante a madrugada.",
        "A cidade tem mais de um milhão de habitantes.",
    ),
    (
        "Os alunos estudaram bastante para o exame final.",
        "Os estudantes se prepararam com afinco para a avaliação.",
        "O professor faltou à aula de ontem.",
    ),
    (
        "O agente coletou notícias de várias fontes de RSS.",
        "O worker reuniu artigos de diversos feeds de informação.",
        "As fontes tipográficas do relatório estão desalinhadas.",
    ),
    (
        "Preciso pagar a conta de luz até sexta-feira.",
        "Tenho que quitar a fatura de energia antes do fim de semana.",
        "A luz do quarto queimou e ficou tudo escuro.",
    ),
    (
        "O médico receitou um remédio para a dor de cabeça.",
        "A doutora prescreveu um medicamento contra a enxaqueca.",
        "O remédio para a crise foi cortar todos os gastos.",
    ),
    (
        "A juíza analisou as provas apresentadas no julgamento.",
        "A magistrada examinou as evidências do processo criminal.",
        "Os alunos fizeram a prova de matemática na segunda.",
    ),
    (
        "O banco fica aberto até as quatro da tarde.",
        "A agência bancária atende até o meio da tarde.",
        "Sentamos no banco da praça para descansar.",
    ),
]


def _embed(texts: list[str], model: str, dim: int, api_key: str) -> list[list[float]]:
    """Retorna um embedding por texto (batchEmbedContents, uma chamada)."""
    body = {
        "requests": [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": t}]},
                "taskType": "SEMANTIC_SIMILARITY",
                "outputDimensionality": dim,
            }
            for t in texts
        ]
    }
    req = urllib.request.Request(  # noqa: S310 (URL fixa https, não entrada externa)
        _ENDPOINT.format(model=model),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # Corpo truncado: a key vai só no header, mas trunca-se por precaução
        # (invariante 8) contra metadados de request que a API possa ecoar.
        body = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"ERRO HTTP {e.code} da API Gemini (corpo truncado): {body}")
    except urllib.error.URLError as e:
        sys.exit(f"ERRO de rede ao chamar a API Gemini: {e.reason}")
    return [emb["values"] for emb in payload["embeddings"]]


def _cosine(a: list[float], b: list[float]) -> float:
    """Similaridade do cosseno (invariante a escala — normalização é implícita)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def main() -> int:
    """Roda o smoke e devolve exit code (0 ok, 1 alguma inversão, 2 config/API)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY ausente no ambiente (invariante 8: key só por env).", file=sys.stderr)
        return 2
    model = os.environ.get("EMBED_MODEL", "gemini-embedding-001")
    try:
        dim = int(os.environ.get("EMBED_DIM", "768"))
    except ValueError:
        print("EMBED_DIM deve ser um inteiro.", file=sys.stderr)
        return 2

    print(f"Modelo: {model} · dimensão solicitada: {dim} · {len(TRIOS)} trios PT-BR\n")

    flat = [s for trio in TRIOS for s in trio]
    vecs = _embed(flat, model, dim, api_key)
    got_dim = len(vecs[0])
    if got_dim != dim:
        print(f"AVISO: dimensão retornada {got_dim} ≠ solicitada {dim}\n", file=sys.stderr)

    print(f"{'#':>2}  {'sim(paráfrase)':>14}  {'sim(distrator)':>14}  {'margem':>8}  ok")
    print("-" * 56)
    passed = 0
    min_margin = math.inf
    for i, (_anchor, _para, _dist) in enumerate(TRIOS):
        va, vp, vd = vecs[3 * i], vecs[3 * i + 1], vecs[3 * i + 2]
        s_para, s_dist = _cosine(va, vp), _cosine(va, vd)
        margin = s_para - s_dist
        ok = margin > 0
        passed += ok
        min_margin = min(min_margin, margin)
        mark = "✓" if ok else "✗"
        print(f"{i + 1:>2}  {s_para:>14.4f}  {s_dist:>14.4f}  {margin:>+8.4f}  {mark}")

    print("-" * 56)
    print(
        f"\nResultado: {passed}/{len(TRIOS)} trios ordenados corretamente · "
        f"dimensão efetiva {got_dim} · margem mínima {min_margin:+.4f}"
    )
    return 0 if passed == len(TRIOS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
