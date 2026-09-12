"""
The default. Declines every request so that callers take their local path.

Having an explicit do-nothing provider means the calling code has one shape
regardless of configuration: there is no `if settings.LLM_ENABLED` scattered
through the services layer.
"""

from __future__ import annotations

from intelligence.providers.base import LLMProvider, LLMUnavailable


class NullProvider(LLMProvider):
    name = "none"

    def __init__(self) -> None:
        super().__init__(model="", timeout=0, max_tokens=0)

    @property
    def is_available(self) -> bool:
        return False

    def _call(self, system: str, user: str, *, json_mode: bool) -> str:
        raise LLMUnavailable("No language model is configured")
