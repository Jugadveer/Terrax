"""
The contract every language-model backend implements.

Deliberately narrow: one method, one return type. Adding a provider means
writing `_call`, not touching anything that consumes the result. Nothing in the
application imports a concrete provider; everything goes through `get_provider`.
"""

from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class LLMUnavailable(Exception):
    """Raised when a provider cannot answer. Callers fall back to local output."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str

    def as_json(self) -> dict[str, Any]:
        """
        Parse the response as JSON, tolerating the fenced code block that most
        models wrap it in even when told not to.
        """
        raw = self.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
            raw = raw.removeprefix("json").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            raise LLMUnavailable("Response contained no JSON object")
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMUnavailable(f"Response was not valid JSON: {exc}") from exc


class LLMProvider(ABC):
    """Base class handling retries, timing and logging for every backend."""

    name: str = "base"

    def __init__(self, *, model: str, timeout: int, max_tokens: int) -> None:
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    @property
    def is_available(self) -> bool:
        """False when the provider has no credentials, so callers can skip it."""
        return True

    def complete(
        self, system: str, user: str, *, json_mode: bool = False
    ) -> LLMResponse:
        """
        Send one prompt and return the text.

        Retries on transport errors with exponential backoff and jitter, so a
        single dropped connection does not surface as a failed feature. Raises
        `LLMUnavailable` once the attempts are spent.
        """
        if not self.is_available:
            raise LLMUnavailable(f"{self.name} is not configured")

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                started = time.monotonic()
                text = self._call(system, user, json_mode=json_mode)
                elapsed = time.monotonic() - started
                logger.info(
                    "llm ok provider=%s model=%s attempt=%d %.2fs",
                    self.name,
                    self.model,
                    attempt,
                    elapsed,
                )
                return LLMResponse(text=text, provider=self.name, model=self.model)
            except Exception as exc:  # noqa: BLE001 - normalised below
                last_error = exc
                logger.warning(
                    "llm failed provider=%s attempt=%d: %s", self.name, attempt, exc
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep((2 ** (attempt - 1)) * 0.6 + random.random() * 0.3)

        raise LLMUnavailable(str(last_error))

    @abstractmethod
    def _call(self, system: str, user: str, *, json_mode: bool) -> str:
        """Provider-specific request. Raise on any non-success."""
