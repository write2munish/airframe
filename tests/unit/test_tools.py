"""Unit tests for ToolRegistry, ScopedToolInjector, ContextBudgeter, and ToolRunner (FR-4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
import warnings

import httpx
import pytest

from airframe.governance.circuit_breakers import (
    ApprovalRejectedError,
    HumanApprovalGate,
    StallDetectedError,
    StallDetector,
)
from airframe.tools import (
    ContextBudgetExceededError,
    ContextBudgeter,
    ContextDietWarning,
    ScopedToolInjector,
    ToolNotFoundError,
    ToolRegistry,
    ToolRunner,
    ToolScopeExceededError,
)


def test_load_real_workflow_tools_and_execute():
    """Load unmodified workflow tools.py files and execute both sync and async tools."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    calc_tools_path = repo_root / "workflows" / "basic-calculator" / "tools.py"
    weather_tools_path = repo_root / "workflows" / "weather-forecaster" / "tools.py"

    registry = ToolRegistry()
    calc_specs = registry.load_from_file(calc_tools_path)
    calc_names = {s.name for s in calc_specs}
    assert {"calculate", "calculate_compound_interest"}.issubset(calc_names)
    assert "_safe_eval" not in registry

    weather_specs = registry.load_from_file(weather_tools_path)
    weather_names = {s.name for s in weather_specs}
    assert {"geocode_location", "get_hourly_forecast"}.issubset(weather_names)

    runner = ToolRunner(registry=registry)

    # 1. Execute sync tool
    calc_res = asyncio.run(runner.execute("calculate", {"expression": "(450 * 12) + (1800 / 4)"}))
    assert calc_res.success is True
    assert calc_res.output["result"] == 5850.0

    # 2. Execute async tool with injected httpx mock client
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "Bengaluru",
                        "country": "India",
                        "latitude": 12.9716,
                        "longitude": 77.5946,
                        "timezone": "Asia/Kolkata",
                    }
                ]
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    geo_res = asyncio.run(
        runner.execute(
            "geocode_location",
            {"location_name": "Bengaluru", "client": mock_client},
        )
    )
    assert geo_res.success is True
    assert geo_res.output["name"] == "Bengaluru"
    assert abs(geo_res.output["latitude"] - 12.9716) < 0.001


def test_scoped_tool_injector_enforces_max_phase_ceiling():
    """Verify ScopedToolInjector rejects binding more than 5 tools to a single phase."""
    registry = ToolRegistry()
    for i in range(6):
        def _fn(x: int) -> int:
            return x
        registry.register(_fn, name=f"tool_{i}")

    injector = ScopedToolInjector(registry=registry, max_tools_per_phase=5)

    # Binding 5 succeeds
    specs = injector.bind_phase("discovery", [f"tool_{i}" for i in range(5)])
    assert len(specs) == 5
    assert len(injector.get_tools_for_phase("discovery")) == 5

    # Binding 6 raises ToolScopeExceededError
    with pytest.raises(ToolScopeExceededError, match="exceeding the maximum ceiling of 5"):
        injector.bind_phase("execution", [f"tool_{i}" for i in range(6)])

    # Binding unknown tool raises ToolNotFoundError
    with pytest.raises(ToolNotFoundError):
        injector.bind_phase("verification", ["missing_tool"])


def test_context_budgeter_warnings_and_strict_mode():
    """Verify ContextBudgeter emits ContextDietWarning and raises in strict mode."""
    budgeter = ContextBudgeter(max_guide_tokens=10, strict=False)
    oversized_guide = "x" * 100  # ~25 tokens > 10

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tokens = budgeter.check_guide(oversized_guide, name="task_spec.md")
        assert tokens == 25
        assert any(issubclass(w.category, ContextDietWarning) for w in caught)

    strict_budgeter = ContextBudgeter(max_guide_tokens=10, strict=True)
    with pytest.raises(ContextBudgetExceededError, match="task_spec.md"):
        strict_budgeter.check_guide(oversized_guide, name="task_spec.md")


def test_tool_runner_governance_hitl_and_stall_detector():
    """Verify ToolRunner enforces HITL gate and trips StallDetector on recurring failures."""
    registry = ToolRegistry()

    def run_shell(command: str) -> str:
        return f"ran: {command}"

    def flaky_tool(arg: str) -> str:
        raise ValueError(f"Broken for {arg}")

    registry.register(run_shell)
    registry.register(flaky_tool)

    # 1. HITL blocks destructive pattern without approval
    runner_no_cb = ToolRunner(registry=registry, approval_gate=HumanApprovalGate())
    with pytest.raises(ApprovalRejectedError, match="requires interactive human approval"):
        asyncio.run(runner_no_cb.execute("run_shell", {"command": "rm -rf /important"}))

    # HITL rejects when callback returns False
    runner_reject = ToolRunner(
        registry=registry,
        approval_gate=HumanApprovalGate(),
        approval_callback=lambda desc: False,
    )
    with pytest.raises(ApprovalRejectedError, match="rejected by human operator"):
        asyncio.run(runner_reject.execute("run_shell", {"command": "git push --force"}))

    # HITL allows safe command
    safe_res = asyncio.run(runner_reject.execute("run_shell", {"command": "ls -la"}))
    assert safe_res.success is True

    # 2. Disallowed whitelist enforcement
    restricted_runner = ToolRunner(registry=registry, allowed_tools=["run_shell"])
    denied_res = asyncio.run(restricted_runner.execute("flaky_tool", {"arg": "test"}))
    assert denied_res.success is False
    assert "Disallowed tool" in (denied_res.error or "")

    # 3. StallDetector trips on 3 identical failures
    detector = StallDetector(stall_threshold=3)
    stall_runner = ToolRunner(registry=registry, stall_detector=detector)

    r1 = asyncio.run(stall_runner.execute("flaky_tool", {"arg": "same"}))
    r2 = asyncio.run(stall_runner.execute("flaky_tool", {"arg": "same"}))
    assert r1.success is False
    assert r2.success is False

    with pytest.raises(StallDetectedError, match="Agent stalled"):
        asyncio.run(stall_runner.execute("flaky_tool", {"arg": "same"}))
