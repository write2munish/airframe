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
    # Match ```json ... ``` or ``` ... ```
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if code_block_match:
        return code_block_match.group(1).strip()
    # Or find outermost JSON object / array { ... } or [ ... ]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
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

        Args:
            prompt: Task prompt payload.
            schema: Target Pydantic model class.
            system_prompt: Optional steering instructions.
            max_retries: Maximum self-repair attempts on validation failure.
            **kwargs: Invocation overrides.

        Returns:
            Tuple of (validated_pydantic_instance, last_model_response).

        Raises:
            RuntimeError: If schema validation fails after max_retries.
        """
        json_schema_spec = json.dumps(schema.model_json_schema(), indent=2)
        guidance = (
            f"\n\n[CONTRACT REQUIREMENT]\n"
            f"You MUST format your response as valid JSON conforming strictly to this JSON Schema:\n"
            f"```json\n{json_schema_spec}\n```\n"
            f"Return ONLY valid JSON. No conversational preamble or trailing commentary."
        )

        current_prompt = prompt + guidance
        last_response: ModelResponse | None = None

        for attempt in range(max_retries + 1):
            last_response = await self.invoke(
                prompt=current_prompt,
                system_prompt=system_prompt,
                **kwargs,
            )

            clean_json = extract_json_payload(last_response.content)

            try:
                validated_obj = schema.model_validate_json(clean_json)
                return validated_obj, last_response
            except (ValidationError, json.JSONDecodeError) as err:
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Executable contract violation for schema '{schema.__name__}' "
                        f"after {max_retries} self-repair retries.\n"
                        f"Final Error: {err}\n"
                        f"Raw Model Output:\n{last_response.content}"
                    ) from err

                # Formulate targeted self-repair feedback prompt
                current_prompt = (
                    f"{prompt}\n\n"
                    f"[VALIDATION FAILED - RETRY ATTEMPT {attempt + 1}/{max_retries}]\n"
                    f"Your previous output failed validation against the schema with the following error:\n"
                    f"```\n{err}\n```\n"
                    f"Raw payload received:\n"
                    f"```\n{last_response.content}\n```\n"
                    f"Please correct the error and return ONLY valid JSON matching this schema:\n"
                    f"```json\n{json_schema_spec}\n```"
                )

        raise RuntimeError("Unexpected exit from contract validation loop.")
