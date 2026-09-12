"""
Groq and OpenRouter both speak the OpenAI chat-completions shape, so they share
one implementation and differ only in base URL, key and headers.
"""

from __future__ import annotations

import requests

from intelligence.providers.base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    base_url: str = ""
    extra_headers: dict[str, str] = {}

    def __init__(self, *, api_key: str, model: str, timeout: int, max_tokens: int):
        super().__init__(model=model, timeout=timeout, max_tokens=max_tokens)
        self.api_key = api_key

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def _call(self, system: str, user: str, *, json_mode: bool) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **self.extra_headers,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    # OpenRouter asks callers to identify themselves; it also unlocks the
    # higher free-tier rate limit.
    extra_headers = {
        "HTTP-Referer": "https://basix.local",
        "X-Title": "Basix",
    }
