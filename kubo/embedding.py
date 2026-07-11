"""Cliente de embedding via REST direto ao Gemini (ADR-0013 §I).

Tripla `(gemini-embedding-001, 768, SEMANTIC_SIMILARITY)` provada por
evidência (ADR-0006). Caminho REST puro (httpx), não LiteLLM: o passthrough
de `taskType`/`outputDimensionality` pela LiteLLM é inverificável e seu modo
de falha (vetor válido porém incomparável) é silencioso — risco alto demais
para "consistência de stack" pagar.

`Embedder` é o seam (Protocol) que `RunContext` expõe a workers; testes
unitários usam fake, nunca o `GeminiEmbedder` concreto contra rede real.
"""

from __future__ import annotations

import os
from typing import Protocol, Sequence

import httpx

from kubo.errors import ConfigError, EmbeddingError


class Embedder(Protocol):
    """Seam de geração de embeddings exposto pelo RunContext a workers.

    `model`/`dim`/`task_type` são a proveniência da tripla (ADR-0006) que o
    chamador carimba no `Chunk` junto com o vetor — não são detalhe interno.
    """

    model: str
    dim: int
    task_type: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Gera um vetor por texto de entrada, preservando a ordem."""
        ...


class GeminiEmbedder:
    """Implementação concreta de `Embedder` via REST `batchEmbedContents` do Gemini."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-embedding-001",
        dim: int = 768,
        task_type: str = "SEMANTIC_SIMILARITY",
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        """Guarda a tripla pinada e a credencial; não faz chamada de rede aqui."""
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.task_type = task_type
        self.client = client if client is not None else httpx.Client()
        self.timeout = timeout

    @classmethod
    def from_env(cls, *, client: httpx.Client | None = None) -> "GeminiEmbedder":
        """Constrói a partir de `GEMINI_API_KEY`; levanta `ConfigError` se ausente/vazia."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ConfigError("GEMINI_API_KEY ausente no ambiente (invariante 8: key só por env).")
        return cls(api_key=api_key, client=client)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Chama `batchEmbedContents` e devolve um vetor por texto, na ordem de envio."""
        if not texts:
            return []

        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                    "taskType": self.task_type,
                    "outputDimensionality": self.dim,
                }
                for t in texts
            ]
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:batchEmbedContents"
        )

        response = self.client.post(
            url,
            json=body,
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )

        if not response.is_success:
            raise EmbeddingError(f"Gemini batchEmbedContents falhou: HTTP {response.status_code}")

        data = response.json()
        if "embeddings" not in data:
            raise EmbeddingError("resposta da API sem campo 'embeddings'.")
        embeddings = data["embeddings"]

        if len(embeddings) != len(texts):
            raise EmbeddingError(
                f"API devolveu {len(embeddings)} embeddings para {len(texts)} textos."
            )

        vectors = [emb["values"] for emb in embeddings]
        if any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(
                f"API honrou dimensão diferente da tripla pinada (esperado {self.dim})."
            )

        return vectors
