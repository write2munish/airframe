"""Google Gemini model adapter."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata


class GeminiAdapter(ModelAdapter):
    """Adapter for Google Gemini models via the Generative Language REST API."""

    def __init__(
        self,
        model_name: str = "gemini-2.0-flash",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_name=model_name,
            api_key=api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", ""),
            base_url=(base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
            timeout=timeout,
            temperature=temperature,
            **kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        contents = [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ]

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", self.temperature),
            },
        }

        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}],
            }

        if "max_tokens" in kwargs:
            payload["generationConfig"]["maxOutputTokens"] = kwargs["max_tokens"]

        url = f"{self.base_url}/models/{self.model_name}:generateContent?key={self.api_key}"

        start_time = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Extract text content
        candidates = data.get("candidates", [])
        if not candidates:
            content = ""
        else:
            parts = candidates[0].get("content", {}).get("parts", [])
            content = "".join(part.get("text", "") for part in parts)

        # Extract normalized usage metadata
        raw_usage = data.get("usageMetadata", {})
        usage = UsageMetadata(
            input_tokens=raw_usage.get("promptTokenCount", 0),
            output_tokens=raw_usage.get("candidatesTokenCount", 0),
            total_tokens=raw_usage.get("totalTokenCount", 0),
        )

        return ModelResponse(
            content=content,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=usage,
            latency_ms=round(latency_ms, 2),
            raw=data,
        )
