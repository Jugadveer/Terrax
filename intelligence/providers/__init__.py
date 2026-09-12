"""Provider selection. One environment variable decides the whole layer."""

from __future__ import annotations

import functools

from django.conf import settings

from intelligence.providers.base import LLMProvider, LLMResponse, LLMUnavailable
from intelligence.providers.gemini import GeminiProvider
from intelligence.providers.null import NullProvider
from intelligence.providers.ollama import OllamaProvider
from intelligence.providers.openai_compatible import GroqProvider, OpenRouterProvider

__all__ = ["LLMProvider", "LLMResponse", "LLMUnavailable", "get_provider"]


@functools.lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    """
    Build the configured provider once per process.

    An unknown or unconfigured value returns `NullProvider` rather than raising,
    because a missing key is a normal state for this project: every feature that
    calls a model has a local implementation to fall back on.
    """
    choice = (settings.LLM_PROVIDER or "none").lower()
    timeout = settings.LLM_TIMEOUT_SECONDS
    max_tokens = settings.LLM_MAX_OUTPUT_TOKENS

    if choice == "groq":
        return GroqProvider(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    if choice == "gemini":
        return GeminiProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    if choice == "openrouter":
        return OpenRouterProvider(
            api_key=settings.OPENROUTER_API_KEY,
            model=settings.OPENROUTER_MODEL,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    if choice == "ollama":
        return OllamaProvider(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=timeout,
            max_tokens=max_tokens,
        )
    return NullProvider()
