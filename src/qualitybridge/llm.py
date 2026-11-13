"""Clientes mínimos de modelos de linguagem usados pelo módulo RCA.

O agente funciona sem LLM. Um cliente só é criado quando o usuário configura
explicitamente as variáveis de ambiente abaixo e pede ``qualitybridge rca --llm``.

- ``QUALITYBRIDGE_LLM_PROVIDER``: ``anthropic`` ou ``openai`` (qualquer API
  compatível com OpenAI: OpenAI, Groq, Gemini, Ollama local etc.).
- ``QUALITYBRIDGE_LLM_MODEL``: nome do modelo (obrigatório para ``openai``).
- ``QUALITYBRIDGE_LLM_BASE_URL``: endpoint da API compatível com OpenAI.
- ``QUALITYBRIDGE_LLM_API_KEY``, ``ANTHROPIC_API_KEY`` ou ``OPENAI_API_KEY``.

Somente a biblioteca padrão é usada, para não adicionar dependências.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Protocol

ANTHROPIC_DEFAULT_MODEL = "claude-haiku-5-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_DEFAULT_URL = "https://api.openai.com/v1"


class LLMError(RuntimeError):
    """Falha de configuração, rede ou formato na chamada ao modelo."""


class LLMClient(Protocol):
    """Contrato usado pelo RCA; permite trocar o provedor ou usar um dublê em testes."""

    name: str

    def complete(self, system: str, user: str) -> str: ...


def _post_json(url: str, headers: dict[str, str], body: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"O provedor respondeu HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"Falha de comunicação com o provedor: {exc}") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise LLMError("O provedor retornou uma resposta que não é JSON.") from exc


class AnthropicClient:
    def __init__(
        self,
        api_key: str,
        model: str = ANTHROPIC_DEFAULT_MODEL,
        timeout: int = 60,
        max_tokens: int = 1500,
    ):
        if not api_key:
            raise LLMError("Chave de API da Anthropic ausente.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.name = f"anthropic:{model}"

    def complete(self, system: str, user: str) -> str:
        data = _post_json(
            ANTHROPIC_URL,
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            self.timeout,
        )
        try:
            return "".join(
                block.get("text", "") for block in data["content"] if block.get("type") == "text"
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise LLMError("Resposta da Anthropic em formato inesperado.") from exc


class OpenAICompatibleClient:
    def __init__(
        self,
        model: str,
        base_url: str = OPENAI_DEFAULT_URL,
        api_key: str = "",
        timeout: int = 60,
    ):
        if not model:
            raise LLMError("Defina QUALITYBRIDGE_LLM_MODEL para o provedor compatível com OpenAI.")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.name = f"openai-compatible:{model}"

    def complete(self, system: str, user: str) -> str:
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post_json(
            f"{self.base_url}/chat/completions",
            headers,
            {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            self.timeout,
        )
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Resposta compatível com OpenAI em formato inesperado.") from exc


def client_from_env(env: Mapping[str, str] | None = None) -> LLMClient | None:
    """Cria o cliente configurado ou retorna ``None`` quando não há configuração."""

    env = os.environ if env is None else env
    provider = env.get("QUALITYBRIDGE_LLM_PROVIDER", "").strip().lower()
    generic_key = env.get("QUALITYBRIDGE_LLM_API_KEY", "")
    model = env.get("QUALITYBRIDGE_LLM_MODEL", "").strip()

    if not provider:
        if env.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif env.get("OPENAI_API_KEY") or env.get("QUALITYBRIDGE_LLM_BASE_URL"):
            provider = "openai"
        else:
            return None

    if provider == "anthropic":
        return AnthropicClient(
            api_key=generic_key or env.get("ANTHROPIC_API_KEY", ""),
            model=model or ANTHROPIC_DEFAULT_MODEL,
        )
    if provider == "openai":
        return OpenAICompatibleClient(
            model=model,
            base_url=env.get("QUALITYBRIDGE_LLM_BASE_URL", OPENAI_DEFAULT_URL),
            api_key=generic_key or env.get("OPENAI_API_KEY", ""),
        )
    raise LLMError("QUALITYBRIDGE_LLM_PROVIDER deve ser 'anthropic' ou 'openai'.")
