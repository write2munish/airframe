"""Unit tests for Airframe model adapters and contract enforcement."""

import asyncio
import json
import pytest
from pydantic import BaseModel, Field

from airframe.adapters import (
    AnthropicAdapter,
    GeminiAdapter,
    OpenAICompatibleAdapter,
    PromptProfiler,
    get_adapter,
)
from airframe.adapters.base import ModelResponse, UsageMetadata


class MockUserReport(BaseModel):
    user_id: int
    name: str
    status: str = Field(description="'ACTIVE' or 'INACTIVE'")
    tags: list[str] = Field(default_factory=list)


def test_openai_compatible_adapter(monkeypatch):
    """Test OpenAICompatibleAdapter correctly parses chat completions and normalizes usage."""
    adapter = OpenAICompatibleAdapter(
        model_name="test-model",
        api_key="mock-key",
        base_url="http://mock-endpoint/v1",
    )

    async def mock_post(*args, **kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "id": "chatcmpl-123",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello from mock OpenAI"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 15,
                        "completion_tokens": 8,
                        "total_tokens": 23,
                    },
                }

        return MockResponse()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    response = asyncio.run(adapter.invoke(prompt="Say hello"))

    assert response.content == "Hello from mock OpenAI"
    assert response.provider == "openai_compatible"
    assert response.model_name == "test-model"
    assert response.usage.input_tokens == 15
    assert response.usage.output_tokens == 8
    assert response.usage.total_tokens == 23
    assert response.latency_ms >= 0


def test_gemini_adapter(monkeypatch):
    """Test GeminiAdapter parses generateContent format and extracts text and token counts."""
    adapter = GeminiAdapter(
        model_name="gemini-2.0-flash",
        api_key="mock-key",
    )

    async def mock_post(*args, **kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Hello from Gemini"}]
                            }
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 12,
                        "candidatesTokenCount": 5,
                        "totalTokenCount": 17,
                    },
                }

        return MockResponse()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    response = asyncio.run(adapter.invoke(prompt="Hello Gemini", system_prompt="Be concise"))

    assert response.content == "Hello from Gemini"
    assert response.provider == "gemini"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 5
    assert response.usage.total_tokens == 17


def test_anthropic_adapter(monkeypatch):
    """Test AnthropicAdapter parses /v1/messages responses."""
    adapter = AnthropicAdapter(
        model_name="claude-3-5-sonnet",
        api_key="mock-key",
    )

    async def mock_post(*args, **kwargs):
        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "content": [{"type": "text", "text": "Hello from Claude"}],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                    },
                }

        return MockResponse()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    response = asyncio.run(adapter.invoke(prompt="Hello Claude"))

    assert response.content == "Hello from Claude"
    assert response.provider == "anthropic"
    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 10
    assert response.usage.total_tokens == 30


def test_invoke_contract_success_first_try():
    """Test invoke_contract successfully parses valid JSON on the first try."""
    class DummyAdapter(OpenAICompatibleAdapter):
        async def invoke(self, prompt: str, **kwargs) -> ModelResponse:
            json_payload = json.dumps({
                "user_id": 42,
                "name": "Alice",
                "status": "ACTIVE",
                "tags": ["admin", "core"],
            })
            return ModelResponse(
                content=f"```json\n{json_payload}\n```",
                model_name="dummy",
                provider="dummy",
                usage=UsageMetadata(input_tokens=10, output_tokens=20, total_tokens=30),
            )

    adapter = DummyAdapter(model_name="dummy")
    result, resp = asyncio.run(adapter.invoke_contract(prompt="Get user 42", schema=MockUserReport))

    assert isinstance(result, MockUserReport)
    assert result.user_id == 42
    assert result.name == "Alice"
    assert result.status == "ACTIVE"
    assert result.tags == ["admin", "core"]


def test_invoke_contract_self_repair_loop():
    """Test invoke_contract catches validation error and successfully repairs on second try."""
    attempt_counter = 0

    class RepairingAdapter(OpenAICompatibleAdapter):
        async def invoke(self, prompt: str, **kwargs) -> ModelResponse:
            nonlocal attempt_counter
            attempt_counter += 1
            if attempt_counter == 1:
                # Malformed output: missing required 'name' field
                bad_json = json.dumps({"user_id": 101, "status": "ACTIVE"})
                return ModelResponse(content=bad_json, model_name="dummy", provider="dummy")
            else:
                # Corrected output on retry
                good_json = json.dumps({
                    "user_id": 101,
                    "name": "Bob",
                    "status": "ACTIVE",
                    "tags": ["repaired"],
                })
                return ModelResponse(content=good_json, model_name="dummy", provider="dummy")

    adapter = RepairingAdapter(model_name="dummy")
    result, resp = asyncio.run(
        adapter.invoke_contract(
            prompt="Get user 101",
            schema=MockUserReport,
            max_retries=2,
        )
    )

    assert attempt_counter == 2
    assert isinstance(result, MockUserReport)
    assert result.name == "Bob"
    assert result.tags == ["repaired"]


def test_invoke_contract_exhausts_retries():
    """Test invoke_contract raises RuntimeError when repair attempts are exhausted."""
    class FailingAdapter(OpenAICompatibleAdapter):
        async def invoke(self, prompt: str, **kwargs) -> ModelResponse:
            return ModelResponse(
                content="Not JSON at all! Just raw text.",
                model_name="dummy",
                provider="dummy",
            )

    adapter = FailingAdapter(model_name="dummy")
    with pytest.raises(RuntimeError, match="Executable contract violation"):
        asyncio.run(
            adapter.invoke_contract(
                prompt="Get user",
                schema=MockUserReport,
                max_retries=1,
            )
        )


def test_prompt_profiler():
    """Test PromptProfiler correctly applies provider-specific syntax."""
    profiler = PromptProfiler()

    # Anthropic compiles with XML tags
    claude_prompt = profiler.compile(
        provider="anthropic",
        prompt="Write a function",
        system_prompt="Be accurate",
        context="Python 3.11",
    )
    assert "<instructions>" in claude_prompt
    assert "<context>" in claude_prompt
    assert "<task>" in claude_prompt

    # Gemini compiles with directives
    gemini_prompt = profiler.compile(
        provider="gemini",
        prompt="Write a function",
        system_prompt="Be accurate",
    )
    assert "[SYSTEM DIRECTIVE]" in gemini_prompt
    assert "[TASK INSTRUCTION]" in gemini_prompt


def test_adapter_factory():
    """Test get_adapter factory resolves providers correctly."""
    gemini = get_adapter("gemini", "gemini-2.0-flash")
    assert isinstance(gemini, GeminiAdapter)

    claude = get_adapter("anthropic", "claude-3-5-sonnet")
    assert isinstance(claude, AnthropicAdapter)

    ollama = get_adapter("ollama", "llama3")
    assert isinstance(ollama, OpenAICompatibleAdapter)

    with pytest.raises(ValueError, match="Unsupported provider"):
        get_adapter("unknown-engine", "xyz")
