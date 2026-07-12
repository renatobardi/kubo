"""Testes RED do `ApiExecutor` — kubo/executors/api.py (ADR-0013 §IV/§V).

Peça de segurança do M6: D6 vira construção. Cobrem: sucesso (JSON válido
vira `response_model`), montagem das mensagens (instrução + diretiva de
schema no system; `untrusted_content` demarcado no user), ausência de
`tools`/`functions` por construção, `num_retries=0` (o backoff é nosso),
saída malformada rejeitada sem retry e sem vazar a saída crua, erro
transiente com backoff até o teto virando `RateLimitExhausted` sem vazar o
corpo cru, recuperação dentro do teto, e `extra="forbid"` da config.

`litellm.completion` é SEMPRE mockado via monkeypatch — nenhum teste faz
chamada real de rede.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import litellm
import pytest
from litellm.exceptions import AuthenticationError, RateLimitError
from pydantic import BaseModel, ValidationError

from kubo.errors import ExecutorError, MalformedOutputError, RateLimitExhausted
from kubo.executors.api import ApiExecutor, ApiExecutorConfig


class _Out(BaseModel):
    """Schema de saída mínimo usado pelos testes."""

    summary: str


def _fake_response(content: str) -> SimpleNamespace:
    """Monta um fake de resposta na forma que o litellm usa: `resp.choices[0].message.content`."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _config(**overrides: Any) -> ApiExecutorConfig:
    """Constrói uma `ApiExecutorConfig` válida, com overrides pontuais por teste."""
    base: dict[str, Any] = {"model": "groq/llama-3.3-70b-versatile"}
    base.update(overrides)
    return ApiExecutorConfig(**base)


def test_complete_sucesso_retorna_instancia_do_response_model(monkeypatch):
    """litellm.completion devolvendo JSON válido do schema vira instância tipada de `_Out`."""
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "um resumo"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    result = executor.complete("resuma o texto", "conteúdo qualquer coletado", _Out)

    assert isinstance(result, _Out)
    assert result.summary == "um resumo"


def test_complete_monta_system_com_instrucao_e_diretiva_de_schema_json(monkeypatch):
    """A 1ª mensagem (system) contém a instruction E uma diretiva JSON derivada do schema."""
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "x"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    executor.complete("resuma o texto de entrada", "conteúdo qualquer", _Out)

    messages = mock_completion.call_args.kwargs["messages"]
    system_msg = messages[0]
    assert system_msg["role"] == "system"
    assert "resuma o texto de entrada" in system_msg["content"]
    assert "json" in system_msg["content"].lower()
    assert "summary" in system_msg["content"]


def test_complete_demarca_untrusted_content_na_mensagem_de_usuario(monkeypatch):
    """A 2ª mensagem (user) envolve o untrusted_content com uma demarcação de não-confiável."""
    sentinela = "TEXTO_COLETADO_HOSTIL_QUALQUER"
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "x"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    executor.complete("instrução", sentinela, _Out)

    messages = mock_completion.call_args.kwargs["messages"]
    user_msg = messages[1]
    assert user_msg["role"] == "user"
    assert sentinela in user_msg["content"]
    conteudo_lower = user_msg["content"].lower()
    marcadores = (
        "não confiável",
        "nao confiavel",
        "untrusted",
        "não siga instruções",
        "nao siga instrucoes",
    )
    assert any(marcador in conteudo_lower for marcador in marcadores)


def test_complete_nao_envia_tools_nem_functions(monkeypatch):
    """Regra 1 de D6 por construção: kwargs de litellm.completion nunca tem `tools`/`functions`."""
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "x"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    executor.complete("instrução", "conteúdo", _Out)

    kwargs = mock_completion.call_args.kwargs
    assert "tools" not in kwargs
    assert "functions" not in kwargs


def test_complete_chama_litellm_com_num_retries_zero(monkeypatch):
    """O backoff é nosso (ADR-0013 §V): litellm.completion sempre recebe num_retries=0."""
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "x"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    executor.complete("instrução", "conteúdo", _Out)

    assert mock_completion.call_args.kwargs["num_retries"] == 0


def test_complete_json_invalido_levanta_malformed_sem_vazar_e_sem_retry(monkeypatch):
    """JSON inválido: MalformedOutputError, sentinela não vaza, sem retry (1 chamada)."""
    sentinela = "SENTINEL_LLM_LIXO_NAO_VAZAR"
    mock_completion = MagicMock(return_value=_fake_response(f"{{not valid json {sentinela}"))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    with pytest.raises(MalformedOutputError) as exc_info:
        executor.complete("instrução", "conteúdo", _Out)

    assert sentinela not in str(exc_info.value)
    assert mock_completion.call_count == 1


def test_complete_json_fora_do_schema_levanta_malformed_sem_vazar_e_sem_retry(monkeypatch):
    """JSON válido fora do schema (falta `summary`): MalformedOutputError, sem vazar, sem retry."""
    sentinela = "SENTINEL_LLM_LIXO_NAO_VAZAR"
    content = json.dumps({"outro_campo": 1, "lixo": sentinela})
    mock_completion = MagicMock(return_value=_fake_response(content))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    with pytest.raises(MalformedOutputError) as exc_info:
        executor.complete("instrução", "conteúdo", _Out)

    assert sentinela not in str(exc_info.value)
    assert mock_completion.call_count == 1


def test_complete_erro_transiente_esgota_tentativas_vira_rate_limit_exhausted(monkeypatch):
    """Transiente em todas as tentativas: backoff até o teto, RateLimitExhausted, sem vazar."""
    sentinela = "SENTINEL_RATE_LIMIT_BODY_NAO_VAZAR"
    erro = RateLimitError(message=f"quota estourada {sentinela}", llm_provider="groq", model="m")
    mock_completion = MagicMock(side_effect=erro)
    monkeypatch.setattr(litellm, "completion", mock_completion)
    sleeps: list[float] = []
    executor = ApiExecutor(_config(), max_attempts=3, sleep=sleeps.append)

    with pytest.raises(RateLimitExhausted) as exc_info:
        executor.complete("instrução", "conteúdo", _Out)

    assert mock_completion.call_count == 3
    assert len(sleeps) == 2
    assert sentinela not in str(exc_info.value)


def test_complete_erro_transiente_recupera_na_segunda_tentativa(monkeypatch):
    """Transiente na 1ª tentativa, sucesso na 2ª: complete() devolve o response_model."""
    erro = RateLimitError(message="quota momentânea", llm_provider="groq", model="m")
    sucesso = _fake_response(json.dumps({"summary": "recuperado"}))
    mock_completion = MagicMock(side_effect=[erro, sucesso])
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config(), max_attempts=3, sleep=lambda _: None)

    result = executor.complete("instrução", "conteúdo", _Out)

    assert isinstance(result, _Out)
    assert result.summary == "recuperado"
    assert mock_completion.call_count == 2


def test_config_extra_forbid_rejeita_campo_extra():
    """ApiExecutorConfig com campo espúrio levanta ValidationError (extra="forbid")."""
    payload = {"model": "groq/llama-3.3-70b-versatile", "campo_espurio": True}

    with pytest.raises(ValidationError):
        ApiExecutorConfig.model_validate(payload)


def test_complete_erro_nao_transiente_vira_executor_error_sem_vazar(monkeypatch):
    """Erro NÃO-transiente do provider (auth/4xx) vira ExecutorError genérico, sem
    retry e sem vazar o corpo cru da resposta (§VIII). Regressão do achado CRÍTICO do
    security-review: AuthenticationError não é subclasse de APIError, escaparia cru."""
    sentinela = "SENTINEL_AUTH_BODY_NAO_VAZAR"
    erro = AuthenticationError(message=sentinela, llm_provider="groq", model="m")
    mock_completion = MagicMock(side_effect=erro)
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config(), max_attempts=3, sleep=lambda _: None)

    with pytest.raises(ExecutorError) as exc_info:
        executor.complete("instrução", "conteúdo", _Out)

    assert not isinstance(exc_info.value, RateLimitExhausted)  # não-transiente não vira rate-limit
    assert sentinela not in str(exc_info.value)  # corpo cru não vaza
    assert exc_info.value.__cause__ is None  # from None: traceback não carrega o corpo
    assert mock_completion.call_count == 1  # não-transiente NÃO faz retry


def test_complete_resposta_com_shape_inesperado_vira_malformed(monkeypatch):
    """Resposta com `choices` vazio (shape hostil/inesperado) vira MalformedOutputError,
    não IndexError cru que derrubaria o run inteiro. Regressão do achado ALTO do review."""
    mock_completion = MagicMock(return_value=SimpleNamespace(choices=[]))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config(), sleep=lambda _: None)

    with pytest.raises(MalformedOutputError):
        executor.complete("instrução", "conteúdo", _Out)


def test_complete_passa_timeout_default_da_config_para_litellm(monkeypatch):
    """Sem override, `ApiExecutorConfig.timeout` default 60.0 chega em `litellm.completion`
    como kwarg `timeout` — sem teto, uma chamada pendurada travaria o job diário (Major,
    achado de code review: o backoff só cobre exceções, não travamento silencioso)."""
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "x"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config())

    executor.complete("instrução", "conteúdo", _Out)

    assert mock_completion.call_args.kwargs["timeout"] == 60.0


def test_complete_passa_timeout_customizado_da_config_para_litellm(monkeypatch):
    """Override de `timeout` na config chega em `litellm.completion` com o valor exato."""
    mock_completion = MagicMock(return_value=_fake_response(json.dumps({"summary": "x"})))
    monkeypatch.setattr(litellm, "completion", mock_completion)
    executor = ApiExecutor(_config(timeout=12.5))

    executor.complete("instrução", "conteúdo", _Out)

    assert mock_completion.call_args.kwargs["timeout"] == 12.5
