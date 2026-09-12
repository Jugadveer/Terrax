"""Google AI Studio. Free tier, no card required."""

from __future__ import annotations

import requests

from intelligence.providers.base import LLMProvider

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, timeout: int, max_tokens: int):
        super().__init__(model=model, timeout=timeout, max_tokens=max_tokens)
        self.api_key = api_key

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def _call(self, system: str, user: str, *, json_mode: bool) -> str:
        config: dict = {
            "temperature": 0.3,
            "maxOutputTokens": self.max_tokens,
        }
        if json_mode:
            config["responseMimeType"] = "application/json"

        response = requests.post(
            f"{BASE_URL}/models/{self.model}:generateContent",
            params={"key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": config,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        candidates = response.json().get("candidates") or []
        if not candidates:
            raise ValueError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(part.get("text", "") for part in parts)
