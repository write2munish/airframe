"""Anthropic Claude model adapter."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata


class AnthropicAdapter(ModelAdapter):
    """Adapter for Anthropic Claude models via the /v1/messages REST API."""

    def __init__(
        self,
        model_name: str = "claude-3-5-sonnet-latest",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_name=model_name,
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY", ""),
            base_url=(base_url or "https://api.anthropic.com/v1").rstrip("/"),
            timeout=timeout,
            temperature=temperature,
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        messages = [
            {"role": "user", "content": prompt},
        ]

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
        }

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", self.temperature),
        }

        if system_prompt:
            payload["system"] = system_prompt

        url = f"{self.base_url}/messages"

        start_time = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Extract text content
        content_blocks = data.get("content", [])
        content = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )

        # Extract normalized usage
        raw_usage = data.get("usage", {})
        usage = UsageMetadata(
            input_tokens=raw_usage.get("input_tokens", 0),
            output_tokens=raw_usage.get("output_tokens", 0),
            total_tokens=raw_usage.get("input_tokens", 0) + raw_usage.get("output_tokens", 0),
        )

        return ModelResponse(
            content=content,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=usage,
            latency_ms=round(latency_ms, 2),
            raw=data,
        )
