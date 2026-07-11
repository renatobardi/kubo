"""Testes RED do cliente de embedding REST (ADR-0013 §I) — kubo/embedding.py.

Cobrem: `from_env` (leitura de `GEMINI_API_KEY`, ausência levanta `ConfigError`),
`embed` (lista vazia sem chamada HTTP, corpo/headers do POST batchEmbedContents,
propagação de erro HTTP sem vazar a api_key, guardas de contagem e dimensão).
LLM/rede sempre mockados via respx — nenhum teste faz chamada real.
"""

import json

import httpx
import pytest
import respx

from kubo.embedding import GeminiEmbedder
from kubo.errors import ConfigError, EmbeddingError

_BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"


def _vector(dim: int, fill: float) -> list[float]:
    """Constrói um vetor de dimensão `dim` preenchido com `fill`, para fixtures de resposta."""
    return [fill] * dim


def _embeddings_response(vectors: list[list[float]]) -> httpx.Response:
    """Monta uma resposta 200 no formato do batchEmbedContents a partir de vetores prontos."""
    return httpx.Response(200, json={"embeddings": [{"values": v} for v in vectors]})


def test_from_env_sem_key_levanta_config_error(monkeypatch):
    """GEMINI_API_KEY ausente do ambiente levanta ConfigError, não KeyError/None silencioso."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ConfigError):
        GeminiEmbedder.from_env()


def test_from_env_com_key_vazia_levanta_config_error(monkeypatch):
    """GEMINI_API_KEY setada como string vazia é tratada como ausente."""
    monkeypatch.setenv("GEMINI_API_KEY", "")

    with pytest.raises(ConfigError):
        GeminiEmbedder.from_env()


def test_from_env_com_key_le_variavel_de_ambiente(monkeypatch):
    """GEMINI_API_KEY presente é lida e passada ao __init__ do embedder construído."""
    monkeypatch.setenv("GEMINI_API_KEY", "env-key-para-teste-nao-real")

    embedder = GeminiEmbedder.from_env(client=httpx.Client())

    assert embedder.api_key == "env-key-para-teste-nao-real"
    assert embedder.model == "gemini-embedding-001"
    assert embedder.dim == 768
    assert embedder.task_type == "SEMANTIC_SIMILARITY"


@respx.mock
def test_embed_lista_vazia_retorna_lista_vazia_sem_chamada_http():
    """texts=[] retorna [] imediatamente, sem qualquer chamada HTTP ao Gemini."""
    embedder = GeminiEmbedder(api_key="fake-key-nao-real", client=httpx.Client())

    result = embedder.embed([])

    assert result == []
    assert len(respx.calls) == 0


@respx.mock
def test_embed_de_n_textos_retorna_n_vetores_na_ordem_de_envio():
    """3 textos geram 3 vetores no retorno, na mesma ordem em que a API os devolveu."""
    textos = ["primeiro texto", "segundo texto", "terceiro texto"]
    vetores = [_vector(768, 0.1), _vector(768, 0.2), _vector(768, 0.3)]
    respx.post(_BATCH_URL).mock(return_value=_embeddings_response(vetores))
    embedder = GeminiEmbedder(api_key="fake-key-nao-real", client=httpx.Client())

    result = embedder.embed(textos)

    assert result == vetores


@respx.mock
def test_embed_envia_um_unico_post_com_um_request_por_texto_no_corpo():
    """O corpo do POST tem um item em 'requests' por texto, com model/taskType/dimensionality."""
    textos = ["texto alfa", "texto beta"]
    respx.post(_BATCH_URL).mock(
        return_value=_embeddings_response([_vector(768, 0.1), _vector(768, 0.2)])
    )
    embedder = GeminiEmbedder(
        api_key="fake-key-nao-real", dim=768, task_type="SEMANTIC_SIMILARITY", client=httpx.Client()
    )

    embedder.embed(textos)

    assert len(respx.calls) == 1
    body = json.loads(respx.calls[0].request.content)
    assert body == {
        "requests": [
            {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": "texto alfa"}]},
                "taskType": "SEMANTIC_SIMILARITY",
                "outputDimensionality": 768,
            },
            {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": "texto beta"}]},
                "taskType": "SEMANTIC_SIMILARITY",
                "outputDimensionality": 768,
            },
        ]
    }


@respx.mock
def test_embed_envia_header_x_goog_api_key_com_a_credencial():
    """O POST carrega a api_key no header x-goog-api-key (não em querystring nem body)."""
    respx.post(_BATCH_URL).mock(return_value=_embeddings_response([_vector(768, 0.1)]))
    embedder = GeminiEmbedder(api_key="minha-key-de-teste", client=httpx.Client())

    embedder.embed(["um texto"])

    assert respx.calls[0].request.headers["x-goog-api-key"] == "minha-key-de-teste"


@respx.mock
def test_embed_envia_content_type_json():
    """O POST declara Content-Type: application/json."""
    respx.post(_BATCH_URL).mock(return_value=_embeddings_response([_vector(768, 0.1)]))
    embedder = GeminiEmbedder(api_key="fake-key-nao-real", client=httpx.Client())

    embedder.embed(["um texto"])

    assert "application/json" in respx.calls[0].request.headers["content-type"]


@respx.mock
def test_embed_com_erro_http_levanta_embedding_error():
    """Resposta HTTP não-2xx da API vira EmbeddingError, não exceção crua do httpx."""
    respx.post(_BATCH_URL).mock(return_value=httpx.Response(500, text="internal error"))
    embedder = GeminiEmbedder(api_key="fake-key-nao-real", client=httpx.Client())

    with pytest.raises(EmbeddingError):
        embedder.embed(["um texto"])


@respx.mock
def test_embed_com_erro_http_nao_vaza_api_key_na_mensagem():
    """A api_key nunca aparece na mensagem da EmbeddingError, mesmo com corpo de erro rico."""
    sentinela = "SENTINEL_KEY_DO_NOT_LEAK"
    respx.post(_BATCH_URL).mock(
        return_value=httpx.Response(
            403, text=f"request rejected for key {sentinela}: permission denied"
        )
    )
    embedder = GeminiEmbedder(api_key=sentinela, client=httpx.Client())

    with pytest.raises(EmbeddingError) as exc_info:
        embedder.embed(["um texto"])

    assert sentinela not in str(exc_info.value)


@respx.mock
def test_embed_com_contagem_de_vetores_diferente_da_de_textos_levanta_embedding_error():
    """API devolvendo menos embeddings do que textos enviados é erro, nunca resultado parcial."""
    respx.post(_BATCH_URL).mock(
        return_value=_embeddings_response([_vector(768, 0.1)])
    )  # 1 vetor para 2 textos
    embedder = GeminiEmbedder(api_key="fake-key-nao-real", client=httpx.Client())

    with pytest.raises(EmbeddingError):
        embedder.embed(["texto um", "texto dois"])


@respx.mock
def test_embed_com_dimensao_de_vetor_diferente_da_tripla_pinada_levanta_embedding_error():
    """Vetor com dimensão != dim pinado (768) é erro — corromperia o espaço de busca."""
    respx.post(_BATCH_URL).mock(return_value=_embeddings_response([_vector(512, 0.1)]))
    embedder = GeminiEmbedder(api_key="fake-key-nao-real", dim=768, client=httpx.Client())

    with pytest.raises(EmbeddingError):
        embedder.embed(["um texto"])
