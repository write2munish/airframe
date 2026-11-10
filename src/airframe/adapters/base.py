"""Base abstractions for swappable foundation model engines."""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, ValidationError

T = TypeVar("T", bound=BaseModel)


class UsageMetadata(BaseModel):
    """Normalized token and computational cost telemetry."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class ModelResponse(BaseModel):
    """Normalized output from any underlying model engine."""

    content: str
    model_name: str
    provider: str
    usage: UsageMetadata = Field(default_factory=UsageMetadata)
    latency_ms: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


def extract_json_payload(raw_text: str) -> str:
    """Extract clean JSON substring from model output, stripping markdown fencing if present."""
    text = raw_text.strip()

    # 1. Strip exact outer markdown fence first (safe when JSON strings contain nested backticks)
    outer_fence_match = re.match(r"^```(?:json)?\s*\n([\s\S]*)\n```\s*$", text)
    if outer_fence_match:
        inner = outer_fence_match.group(1).strip()
        try:
            json.loads(inner)
            return inner
        except Exception:
            pass

    # 2. Match outermost JSON object { ... } if it parses as valid JSON
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        brace_slice = text[first_brace : last_brace + 1]
        try:
            json.loads(brace_slice)
            return brace_slice
        except Exception:
            pass

    # 3. Fallback to fenced code block extraction
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if code_block_match:
        return code_block_match.group(1).strip()

    # 4. Fallback to outermost brace slice so callers/validators see exact JSON error details
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1]

    return text


class ModelAdapter(ABC):
    """Abstract interface for swappable foundation model engines."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.temperature = temperature
        self.extra_kwargs = kwargs

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g., 'gemini', 'anthropic', 'openai_compatible')."""
        pass

    @abstractmethod
    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Execute a completion against the model engine.

        Args:
            prompt: User or task prompt payload.
            system_prompt: Optional steering instructions.
            **kwargs: Provider-specific invocation overrides.

        Returns:
            Normalized ModelResponse with text content and telemetry.
        """
        pass

    async def invoke_contract(
        self,
        prompt: str,
        schema: type[T],
        system_prompt: str | None = None,
        max_retries: int = 2,
        **kwargs: Any,
    ) -> tuple[T, ModelResponse]:
        """Execute a completion bound to an executable Pydantic schema contract with self-repair.

        Delegates to the canonical run_with_retry feedback control loop and SchemaSensor.
        """
        from airframe.orchestration.evaluator_optimizer import run_with_retry
        from airframe.sensors.schema import SchemaSensor

        json_schema_spec = json.dumps(schema.model_json_schema(), indent=2)
        guidance = (
            f"\n\n[CONTRACT REQUIREMENT]\n"
            f"You MUST format your response as valid JSON conforming strictly to this JSON Schema:\n"
            f"```json\n{json_schema_spec}\n```\n"
            f"Return ONLY valid JSON. No conversational preamble or trailing commentary."
        )

        sensor = SchemaSensor(schema=schema)
        last_response: ModelResponse | None = None
        attempt_count = 0

        async def generator(base_prompt: str, critique: str | None) -> str:
            nonlocal last_response, attempt_count
            attempt_count += 1
            if critique is None:
                call_prompt = base_prompt + guidance
            else:
                call_prompt = (
                    f"{base_prompt}\n\n"
                    f"[VALIDATION FAILED - RETRY ATTEMPT {attempt_count - 1}/{max_retries}]\n"
                    f"Your previous output failed validation against the schema with the following error:\n"
                    f"```\n{critique}\n```\n"
                    f"Raw payload received:\n"
                    f"```\n{last_response.content if last_response else ''}\n```\n"
                    f"Please correct the error and return ONLY valid JSON matching this schema:\n"
                    f"```json\n{json_schema_spec}\n```"
                )

            invoke_kwargs = {"target_schema": schema, **kwargs}
            last_response = await self.invoke(
                prompt=call_prompt,
                system_prompt=system_prompt,
                **invoke_kwargs,
            )
            return last_response.content

        opt_result = await run_with_retry(
            initial_prompt=prompt,
            generator_fn=generator,
            sensors=sensor,
            max_retries=max_retries,
        )

        if not opt_result.passed or last_response is None:
            last_err = opt_result.history[-1].critique if opt_result.history else "Validation failed"
            raw_content = last_response.content if last_response else ""
            raise RuntimeError(
                f"Executable contract violation for schema '{schema.__name__}' "
                f"after {max_retries} self-repair retries.\n"
                f"Final Error: {last_err}\n"
                f"Raw Model Output:\n{raw_content}"
            )

        clean_json = extract_json_payload(opt_result.final_candidate)
        validated_obj = schema.model_validate_json(clean_json)
        return validated_obj, last_response
