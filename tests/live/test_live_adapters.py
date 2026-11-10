"""Opt-in live vendor adapter and tool-loop smoke tests (C6).

Skipped automatically during offline CI runs unless AIRFRAME_LIVE_TESTS=1 and provider
credentials (or a reachable local Ollama instance) are present.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from airframe.adapters import AnthropicAdapter, GeminiAdapter, OpenAICompatibleAdapter
from airframe.runner import WorkflowRunner
from airframe.telemetry import AirframeTracer
from airframe.workflows.loader import load_workflow

LIVE_ENABLED = os.environ.get("AIRFRAME_LIVE_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="Opt-in live vendor smoke tests require AIRFRAME_LIVE_TESTS=1",
)


class LiveEchoContract(BaseModel):
    answer: str
    status: str = "SUCCESS"


def _ollama_reachable(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=1.5)
        return resp.status_code == 200
    except Exception:
        return False


def test_live_gemini_contract_and_tool_loop(tmp_path: Path) -> None:
    """Verify real Gemini HTTP call via invoke_contract and WorkflowRunner tool loop."""
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")

    adapter = GeminiAdapter(
        model_name=os.environ.get("AIRFRAME_LIVE_GEMINI_MODEL", "gemini-2.0-flash"),
        timeout=30.0,
    )
    out, resp = asyncio.run(
        adapter.invoke_contract(
            prompt="Return answer='4' and status='SUCCESS' for 2+2.",
            schema=LiveEchoContract,
        )
    )
    assert out.status
    assert resp.usage.total_tokens > 0

    pack = load_workflow(Path("workflows/basic-calculator"))
    runner = WorkflowRunner(tracer=AirframeTracer(trace_dir=tmp_path / "traces"))
    res = asyncio.run(runner.run(pack, "Calculate compound interest on 10000 at 5% for 3 years.", adapter))
    assert abs(float(res.output["result"]) - 11576.25) <= 1.0


def test_live_anthropic_contract_and_tool_loop(tmp_path: Path) -> None:
    """Verify real Anthropic HTTP call via invoke_contract and WorkflowRunner tool loop."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    adapter = AnthropicAdapter(
        model_name=os.environ.get("AIRFRAME_LIVE_ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
        timeout=30.0,
    )
    out, resp = asyncio.run(
        adapter.invoke_contract(
            prompt="Return answer='4' and status='SUCCESS' for 2+2.",
            schema=LiveEchoContract,
        )
    )
    assert out.status
    assert resp.usage.total_tokens > 0

    pack = load_workflow(Path("workflows/basic-calculator"))
    runner = WorkflowRunner(tracer=AirframeTracer(trace_dir=tmp_path / "traces"))
    res = asyncio.run(runner.run(pack, "Calculate 450 * 12.", adapter))
    assert abs(float(res.output["result"]) - 5400.0) <= 1.0


def test_live_openai_contract_and_tool_loop(tmp_path: Path) -> None:
    """Verify real OpenAI HTTP call via invoke_contract and WorkflowRunner tool loop."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    adapter = OpenAICompatibleAdapter(
        model_name=os.environ.get("AIRFRAME_LIVE_OPENAI_MODEL", "gpt-4o-mini"),
        timeout=30.0,
    )
    out, resp = asyncio.run(
        adapter.invoke_contract(
            prompt="Return answer='4' and status='SUCCESS' for 2+2.",
            schema=LiveEchoContract,
        )
    )
    assert out.status
    assert resp.usage.total_tokens > 0

    pack = load_workflow(Path("workflows/basic-calculator"))
    runner = WorkflowRunner(tracer=AirframeTracer(trace_dir=tmp_path / "traces"))
    res = asyncio.run(runner.run(pack, "Calculate 450 * 12.", adapter))
    assert abs(float(res.output["result"]) - 5400.0) <= 1.0


def test_live_ollama_contract_and_tool_loop(tmp_path: Path) -> None:
    """Verify local Ollama endpoint via OpenAICompatibleAdapter when reachable."""
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not _ollama_reachable(ollama_host):
        pytest.skip(f"Ollama server not reachable at {ollama_host}")

    adapter = OpenAICompatibleAdapter(
        model_name=os.environ.get("AIRFRAME_LIVE_OLLAMA_MODEL", "llama3.2"),
        base_url=f"{ollama_host.rstrip('/')}/v1",
        api_key="ollama",
        timeout=60.0,
    )
    out, _ = asyncio.run(
        adapter.invoke_contract(
            prompt="Return answer='4' and status='SUCCESS' for 2+2.",
            schema=LiveEchoContract,
        )
    )
    assert out.status
