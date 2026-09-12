"""A model running on the same machine. No key, no quota, works offline."""

from __future__ import annotations

import requests

from intelligence.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, *, base_url: str, model: str, timeout: int, max_tokens: int):
        super().__init__(model=model, timeout=timeout, max_tokens=max_tokens)
        self.base_url = base_url.rstrip("/")

    @property
    def is_available(self) -> bool:
        """A local daemon that is not running is indistinguishable from absent."""
        try:
            requests.get(f"{self.base_url}/api/tags", timeout=1.5).raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _call(self, system: str, user: str, *, json_mode: bool) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0.3, "num_predict": self.max_tokens},
        }
        if json_mode:
            payload["format"] = "json"

        response = requests.post(
            f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
