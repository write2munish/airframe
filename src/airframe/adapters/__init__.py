"""Swappable model engine adapters and factory functions."""

from __future__ import annotations

from typing import Any

from airframe.adapters.anthropic import AnthropicAdapter
from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata
from airframe.adapters.gemini import GeminiAdapter
from airframe.adapters.openai_compatible import OpenAICompatibleAdapter
from airframe.adapters.profiler import PromptProfiler

__all__ = [
    "ModelAdapter",
    "ModelResponse",
    "UsageMetadata",
    "OpenAICompatibleAdapter",
    "GeminiAdapter",
    "AnthropicAdapter",
    "PromptProfiler",
    "get_adapter",
    "resolve_adapter",
]


def get_adapter(
    provider: str,
    model_name: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> ModelAdapter:
    """Factory to retrieve a configured model engine adapter.

    Args:
        provider: Provider identifier ('openai', 'openai_compatible', 'ollama', 'gemini', 'anthropic').
        model_name: Name of the target model (e.g. 'gemini-2.0-flash', 'gpt-4o', 'claude-3-5-sonnet-latest').
        api_key: Optional provider API key.
        base_url: Optional provider base URL.
        **kwargs: Extra parameters passed to the adapter constructor.

    Returns:
        Configured ModelAdapter instance.
    """
    provider_normalized = provider.lower().replace("-", "_")

    if provider_normalized in ("openai", "openai_compatible", "ollama", "vllm", "lmstudio"):
        import os

        if provider_normalized == "ollama":
            base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            api_key = api_key or "ollama"
            if "timeout" not in kwargs:
                kwargs["timeout"] = 180.0
        elif provider_normalized == "vllm":
            base_url = base_url or os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
            api_key = api_key or "vllm"
        elif provider_normalized == "lmstudio":
            base_url = base_url or os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
            api_key = api_key or "lmstudio"

        return OpenAICompatibleAdapter(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
    elif provider_normalized in ("gemini", "google"):
        return GeminiAdapter(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
    elif provider_normalized in ("anthropic", "claude"):
        return AnthropicAdapter(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
    else:
        raise ValueError(
            f"Unsupported provider: '{provider}'. "
            f"Supported providers: 'openai', 'gemini', 'anthropic', 'ollama', 'vllm'."
        )


def resolve_adapter(
    engine: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> ModelAdapter:
    """Resolve an engine identifier string into an instantiated ModelAdapter.

    Supports formats like:
      - 'gemini-2.0-flash' -> provider: 'gemini'
      - 'gpt-4o', 'gpt-4o-mini' -> provider: 'openai'
      - 'claude-3-5-sonnet' -> provider: 'anthropic'
      - 'provider/model' (e.g. 'ollama/llama3', 'openai/gpt-4o')
    """
    engine_clean = engine.strip()
    if "/" in engine_clean:
        provider, model_name = engine_clean.split("/", 1)
        return get_adapter(provider=provider, model_name=model_name, api_key=api_key, base_url=base_url, **kwargs)

    lower = engine_clean.lower()
    if lower.startswith("gemini"):
        return get_adapter(provider="gemini", model_name=engine_clean, api_key=api_key, base_url=base_url, **kwargs)
    elif lower.startswith("claude"):
        return get_adapter(provider="anthropic", model_name=engine_clean, api_key=api_key, base_url=base_url, **kwargs)
    elif lower.startswith("ollama"):
        return get_adapter(provider="ollama", model_name=engine_clean, api_key=api_key, base_url=base_url, **kwargs)
    elif lower.startswith("gpt") or lower.startswith("o1") or lower.startswith("o3") or lower.startswith("o4"):
        return get_adapter(provider="openai", model_name=engine_clean, api_key=api_key, base_url=base_url, **kwargs)
    else:
        return get_adapter(provider="openai_compatible", model_name=engine_clean, api_key=api_key, base_url=base_url, **kwargs)
