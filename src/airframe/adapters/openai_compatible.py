"""OpenAI-compatible model adapter (supports OpenAI, Ollama, vLLM, LM Studio)."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata


class OpenAICompatibleAdapter(ModelAdapter):
    """Adapter for endpoints implementing the OpenAI /chat/completions REST specification."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_name=model_name,
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            base_url=(base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
            timeout=timeout,
            temperature=temperature,
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        url = f"{self.base_url}/chat/completions"

        start_time = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        content = data["choices"][0]["message"]["content"] or ""

        # Extract normalized usage
        raw_usage = data.get("usage", {})
        usage = UsageMetadata(
            input_tokens=raw_usage.get("prompt_tokens", 0),
            output_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
        )

        return ModelResponse(
            content=content,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=usage,
            latency_ms=round(latency_ms, 2),
            raw=data,
        )
