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
