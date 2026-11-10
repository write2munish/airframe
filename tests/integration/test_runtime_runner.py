"""Integration tests for WorkflowRunner runtime engine and full governance/orchestration wiring (C4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata
from airframe.governance.circuit_breakers import (
    Budget,
    BudgetExceededError,
    GovernanceError,
    RecursionDepthExceededError,
    StallDetectedError,
)
from airframe.isolation import WorktreeManager
from airframe.orchestration import ShiftTurnoverEngine, SubtaskSpec, SupervisorAgent
from airframe.runner import (
    AgentStepDecision,
    MaxTurnsExceededError,
    OrchestratorPlan,
    PlannedSubtask,
    WorkerStepReport,
    WorkflowRunner,
)
from airframe.sensors.base import Sensor, SensorResult
from airframe.state.disk import DiskStateManager, TaskItem
from airframe.telemetry import AirframeTracer, build_trace_tree, load_trace
from airframe.workflows.loader import load_workflow


class ScriptedAdapter(ModelAdapter):
    """Test adapter returning queued responses and recording all received prompts."""

    def __init__(self, responses: list[ModelResponse], delay_seconds: float = 0.0) -> None:
        super().__init__(model_name="scripted-test-model")
        self._responses = list(responses)
        self._delay = delay_seconds
        self.recorded_prompts: list[str] = []
        self.recorded_system_prompts: list[str | None] = []

    @property
    def provider_name(self) -> str:
        return "mock-scripted"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        self.recorded_prompts.append(prompt)
        self.recorded_system_prompts.append(system_prompt)
        if not self._responses:
            raise AssertionError(f"ScriptedAdapter ran out of responses on prompt:\n{prompt[:200]}")
        return self._responses.pop(0)


def _setup_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "RunnerTester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "runner@test.local"], cwd=path, check=True)
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _decision_resp(decision: AgentStepDecision, tokens: int = 100, cost: float = 0.001) -> ModelResponse:
    return ModelResponse(
        content=decision.model_dump_json(),
        model_name="scripted-test-model",
        provider="mock-scripted",
        usage=UsageMetadata(
            input_tokens=tokens // 2,
            output_tokens=tokens - (tokens // 2),
            total_tokens=tokens,
            estimated_cost_usd=cost,
        ),
    )


def test_multiturn_react_tool_loop_basic_calculator(tmp_path: Path) -> None:
    """Verify WorkflowRunner executes ReAct tool call against workflows/basic-calculator/tools.py and traces spans."""
    pack = load_workflow(Path("workflows/basic-calculator"))
    tracer = AirframeTracer(trace_dir=tmp_path / "traces")
    runner = WorkflowRunner(tracer=tracer)

    adapter = ScriptedAdapter([
        _decision_resp(
            AgentStepDecision(
                thought="I should compute (10 + 5) * 2 using the calculate tool.",
                action="call_tool",
                tool_name="calculate",
                arguments={"expression": "(10 + 5) * 2"},
            )
        ),
        _decision_resp(
            AgentStepDecision(
                thought="The tool returned 30.0. Now emitting final CalculationResult.",
                action="final_answer",
                final_output={
                    "query": "(10 + 5) * 2",
                    "result": 30.0,
                    "formatted_result": "30",
                    "steps": ["10 + 5 = 15", "15 * 2 = 30"],
                    "status": "SUCCESS",
                },
            )
        ),
    ])

    result = asyncio.run(runner.run(pack, "Compute (10 + 5) * 2", adapter))

    assert result.workflow_id == "basic-calculator"
    assert result.entrypoint_agent == "calculator"
    assert result.turns_executed == 2
    assert result.tools_called == ["calculate"]
    assert result.output["result"] == 30.0
    assert result.total_tokens == 200

    # Verify durable trace tree
    spans = load_trace(tracer.trace_file)
    assert len(spans) == 4  # root + turn1 model + tool.calculate + turn2 model
    tree = build_trace_tree(spans)
    root = tree["roots"][0]
    assert root["name"] == "workflow.basic-calculator"
    child_names = [c["name"] for c in root["children"]]
    assert child_names == ["model.turn_1", "tool.calculate", "model.turn_2"]


def test_shift_turnover_continuity_and_git_checkpoint(tmp_path: Path) -> None:
    """Verify 4-turn workflow inside WorktreeWorkspace triggers shift turnover, commits git delta, wipes history, and bootloads."""
    repo_dir = _setup_git_repo(tmp_path / "repo")
    wf_dir = tmp_path / "shift_wf"
    wf_dir.mkdir(parents=True)

    (wf_dir / "tools.py").write_text(
        "from pathlib import Path\n\n"
        "def write_work_file(filename: str, content: str) -> dict:\n"
        "    \"\"\"Write a file inside the active agent workspace.\"\"\"\n"
        "    target = Path(filename)\n"
        "    target.write_text(content, encoding='utf-8')\n"
        "    return {'written': str(target.resolve()), 'bytes': len(content)}\n",
        encoding="utf-8",
    )
    (wf_dir / "workflow.yaml").write_text(
        yaml.dump({
            "id": "shift-turnover-wf",
            "topology": "single-agent",
            "workspace_mode": "branch",
            "entrypoint_agent": "worker",
            "tools": ["write_work_file"],
            "system_prompt": "Write milestone artifacts cleanly.",
            "governance": {"max_turns": 8, "timeout_seconds": 60},
        }),
        encoding="utf-8",
    )

    state_mgr = DiskStateManager(tmp_path / "disk_state")
    state_mgr.init_state(
        plan_markdown="# Build Multi-Step Feature",
        tasks=[TaskItem(id="m1", description="Create step1, step2, step3 artifacts", status="IN_PROGRESS")],
    )

    pack = load_workflow(wf_dir)
    shift_engine = ShiftTurnoverEngine(context_limit_tokens=1000, turnover_threshold_ratio=0.65)  # ceiling = 650
    tracer = AirframeTracer(trace_dir=tmp_path / "traces")
    runner = WorkflowRunner(tracer=tracer)

    adapter = ScriptedAdapter([
        # Turn 1: 400 tokens (cum=400 < 650)
        _decision_resp(
            AgentStepDecision(
                thought="TURN1_SECRET_MARKER creating step1",
                action="call_tool",
                tool_name="write_work_file",
                arguments={"filename": "step1.txt", "content": "alpha"},
            ),
            tokens=400,
        ),
        # Turn 2: 350 tokens (cum=750 >= 650) -> next turn MUST turnover!
        _decision_resp(
            AgentStepDecision(
                thought="creating step2 before turnover",
                action="call_tool",
                tool_name="write_work_file",
                arguments={"filename": "step2.txt", "content": "beta"},
            ),
            tokens=350,
        ),
        # Turn 3: executes in Shift 2 after context wipe
        _decision_resp(
            AgentStepDecision(
                thought="continuing in shift 2 with step3",
                action="call_tool",
                tool_name="write_work_file",
                arguments={"filename": "step3.txt", "content": "gamma"},
            ),
            tokens=250,
        ),
        # Turn 4: completes in Shift 2
        _decision_resp(
            AgentStepDecision(
                thought="all steps finished",
                action="final_answer",
                final_output={"summary": "Created step1, step2, and step3 across 2 shifts", "status": "SUCCESS"},
            ),
            tokens=150,
        ),
    ])

    result = asyncio.run(
        runner.run(
            pack,
            "Build feature artifacts",
            adapter,
            shift_engine=shift_engine,
            state_manager=state_mgr,
            workspace_repo_path=repo_dir,
            delete_workspace_on_exit=False,
        )
    )

    try:
        assert result.shifts_executed == 2
        assert result.turns_executed == 4
        assert result.tools_called == ["write_work_file", "write_work_file", "write_work_file"]
        assert result.output["status"] == "SUCCESS"

        # Turn 2 saw Turn 1 history, while Turn 3 prompt must have wiped Turn 1 raw history and bootloaded [SHIFT 2]
        assert len(adapter.recorded_prompts) == 4
        assert "TURN1_SECRET_MARKER" in adapter.recorded_prompts[1]
        turn3_prompt = adapter.recorded_prompts[2]
        assert "[SHIFT 2]" in turn3_prompt
        assert "# Build Multi-Step Feature" in turn3_prompt
        assert "TURN1_SECRET_MARKER" not in turn3_prompt

        # Verify git checkpoint commit exists inside the worktree branch
        wt_path = Path(result.metadata["workspace_path"])
        log_proc = subprocess.run(
            ["git", "log", "--oneline", "-n", "5"],
            cwd=wt_path,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "airframe: shift 1 checkpoint" in log_proc.stdout
        assert (wt_path / "step1.txt").is_file()
        assert (wt_path / "step2.txt").is_file()
        assert (wt_path / "step3.txt").is_file()
    finally:
        asyncio.run(WorktreeManager().remove_worktree(repo_dir, agent_id="worker", delete_branch=True))


def test_circuit_breakers_timeout_max_turns_stall_budget_recursion(tmp_path: Path) -> None:
    """Verify timeout_seconds, max_turns, StallDetector, BudgetTracker, and RecursionLimiter all trip properly."""
    # 1. Timeout breaker
    wf_timeout = tmp_path / "wf_timeout"
    wf_timeout.mkdir()
    (wf_timeout / "workflow.yaml").write_text(
        yaml.dump({"id": "timeout-wf", "topology": "single-agent", "system_prompt": "Slow task"}),
        encoding="utf-8",
    )
    pack_timeout = load_workflow(wf_timeout)
    runner = WorkflowRunner(tracer=AirframeTracer(trace_dir=tmp_path / "t1"))
    slow_adapter = ScriptedAdapter(
        [ModelResponse(content='{"summary": "ok", "status": "SUCCESS"}', model_name="m", provider="p")],
        delay_seconds=0.5,
    )
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(runner.run(pack_timeout, "go", slow_adapter, timeout_seconds=0.1))

    # 2. max_turns cap
    wf_turns = tmp_path / "wf_turns"
    wf_turns.mkdir()
    (wf_turns / "tools.py").write_text("def echo(x: str) -> str:\n    return x\n", encoding="utf-8")
    (wf_turns / "workflow.yaml").write_text(
        yaml.dump({
            "id": "turns-wf",
            "tools": ["echo"],
            "governance": {"max_turns": 2, "stall_threshold": 5},
        }),
        encoding="utf-8",
    )
    pack_turns = load_workflow(wf_turns)
    looping_adapter = ScriptedAdapter([
        _decision_resp(AgentStepDecision(thought="call 1", action="call_tool", tool_name="echo", arguments={"x": "a"})),
        _decision_resp(AgentStepDecision(thought="call 2", action="call_tool", tool_name="echo", arguments={"x": "b"})),
        _decision_resp(AgentStepDecision(thought="call 3", action="call_tool", tool_name="echo", arguments={"x": "c"})),
    ])
    with pytest.raises(MaxTurnsExceededError, match="max_turns ceiling"):
        asyncio.run(runner.run(pack_turns, "go", looping_adapter))

    # 3. StallDetector 3 repeated failing tool calls
    wf_stall = tmp_path / "wf_stall"
    wf_stall.mkdir()
    (wf_stall / "tools.py").write_text(
        "def flaky(x: int) -> int:\n    raise ValueError('bad input')\n",
        encoding="utf-8",
    )
    (wf_stall / "workflow.yaml").write_text(
        yaml.dump({
            "id": "stall-wf",
            "tools": ["flaky"],
            "governance": {"max_turns": 10, "stall_threshold": 3},
        }),
        encoding="utf-8",
    )
    pack_stall = load_workflow(wf_stall)
    stalling_adapter = ScriptedAdapter([
        _decision_resp(AgentStepDecision(thought="try 1", action="call_tool", tool_name="flaky", arguments={"x": 1})),
        _decision_resp(AgentStepDecision(thought="try 2", action="call_tool", tool_name="flaky", arguments={"x": 1})),
        _decision_resp(AgentStepDecision(thought="try 3", action="call_tool", tool_name="flaky", arguments={"x": 1})),
    ])
    with pytest.raises(StallDetectedError, match="Agent stalled"):
        asyncio.run(runner.run(pack_stall, "go", stalling_adapter))

    # 4. BudgetTracker dollar limit
    wf_budget = tmp_path / "wf_budget"
    wf_budget.mkdir()
    (wf_budget / "workflow.yaml").write_text(
        yaml.dump({"id": "budget-wf", "governance": {"max_cost_usd": 0.01}}),
        encoding="utf-8",
    )
    pack_budget = load_workflow(wf_budget)
    expensive_adapter = ScriptedAdapter([
        ModelResponse(
            content='{"summary": "expensive", "status": "SUCCESS"}',
            model_name="m",
            provider="p",
            usage=UsageMetadata(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_usd=0.05),
        )
    ])
    with pytest.raises(BudgetExceededError, match="Cost budget exceeded"):
        asyncio.run(runner.run(pack_budget, "go", expensive_adapter))

    # 5. SupervisorAgent RecursionLimiter blocks depth 3
    sup = SupervisorAgent(supervisor_id="root_sup", max_depth=2)
    with pytest.raises(RecursionDepthExceededError, match="exceeds maximum ceiling of 2"):
        asyncio.run(sup.delegate_subtask(SubtaskSpec(task_id="nested", description="too deep"), depth=3))


def test_evaluator_optimizer_and_orchestrator_workers_and_entrypoint(tmp_path: Path) -> None:
    """Verify evaluator-optimizer self-repair, orchestrator-workers dispatch, and strict entrypoint resolution."""
    # 1. Evaluator-optimizer retry with failing sensor
    wf_eo = tmp_path / "wf_eo"
    wf_eo.mkdir()
    (wf_eo / "workflow.yaml").write_text(
        yaml.dump({
            "id": "eo-wf",
            "topology": "evaluator-optimizer",
            "governance": {"max_turns": 4},
        }),
        encoding="utf-8",
    )

    class MustContainWordSensor(Sensor):
        @property
        def name(self) -> str:
            return "must_contain_verified"

        @property
        def tier(self) -> int:
            return 1

        async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
            text = json.dumps(candidate) if not isinstance(candidate, str) else candidate
            ok = "VERIFIED" in text
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=ok,
                message="VERIFIED keyword present" if ok else "Missing VERIFIED token",
            )

    eo_adapter = ScriptedAdapter([
        ModelResponse(
            content='{"summary": "draft answer without keyword", "status": "SUCCESS"}',
            model_name="m",
            provider="p",
            usage=UsageMetadata(input_tokens=20, output_tokens=10, total_tokens=30),
        ),
        ModelResponse(
            content='{"summary": "VERIFIED answer after critique", "status": "SUCCESS"}',
            model_name="m",
            provider="p",
            usage=UsageMetadata(input_tokens=25, output_tokens=15, total_tokens=40),
        ),
    ])
    runner = WorkflowRunner(tracer=AirframeTracer(trace_dir=tmp_path / "traces"))
    eo_res = asyncio.run(
        runner.run(
            load_workflow(wf_eo),
            "Produce verified summary",
            eo_adapter,
            sensors=[MustContainWordSensor()],
        )
    )
    assert eo_res.output["summary"] == "VERIFIED answer after critique"
    assert eo_res.metadata["iterations"] == 2
    assert "Missing VERIFIED token" in eo_adapter.recorded_prompts[1]

    # 2. Orchestrator-workers topology + strict entrypoint resolution (not blueprints[0])
    wf_ow = tmp_path / "wf_ow"
    (wf_ow / "blueprints").mkdir(parents=True)
    (wf_ow / "blueprints" / "helper.yaml").write_text(
        yaml.dump({
            "name": "helper",
            "role": "Specialist helper",
            "system_prompt": "Execute focused subtask as helper.",
            "engine_binding": "mock",
        }),
        encoding="utf-8",
    )
    (wf_ow / "blueprints" / "lead.yaml").write_text(
        yaml.dump({
            "name": "lead",
            "role": "Lead orchestrator",
            "system_prompt": "Coordinate helper workers and synthesize results.",
            "engine_binding": "mock",
        }),
        encoding="utf-8",
    )
    (wf_ow / "workflow.yaml").write_text(
        yaml.dump({
            "id": "ow-wf",
            "topology": "orchestrator-workers",
            "entrypoint_agent": "lead",
            "agents": ["blueprints/helper.yaml", "blueprints/lead.yaml"],
        }),
        encoding="utf-8",
    )
    ow_pack = load_workflow(wf_ow)
    ow_tracer = AirframeTracer(trace_dir=tmp_path / "ow_traces")
    ow_runner = WorkflowRunner(tracer=ow_tracer)

    ow_adapter = ScriptedAdapter([
        # 1. Plan decomposition
        ModelResponse(
            content=OrchestratorPlan(
                goal="Audit service",
                subtasks=[
                    PlannedSubtask(task_id="t1", description="Check auth", assigned_blueprint="helper"),
                    PlannedSubtask(task_id="t2", description="Check db", assigned_blueprint="helper"),
                ],
            ).model_dump_json(),
            model_name="m",
            provider="p",
        ),
        # 2. Worker t1 response
        ModelResponse(
            content=WorkerStepReport(summary="Auth is safe").model_dump_json(),
            model_name="m",
            provider="p",
        ),
        # 3. Worker t2 response
        ModelResponse(
            content=WorkerStepReport(summary="DB uses parameterized queries").model_dump_json(),
            model_name="m",
            provider="p",
        ),
        # 4. Final synthesis
        ModelResponse(
            content='{"summary": "Audit passed across auth and DB", "status": "SUCCESS"}',
            model_name="m",
            provider="p",
        ),
    ])

    ow_res = asyncio.run(ow_runner.run(ow_pack, "Audit service", ow_adapter))
    assert ow_res.entrypoint_agent == "lead"  # Selected 'lead' by entrypoint_agent, NOT 'helper' (blueprints[0])
    assert ow_adapter.recorded_system_prompts[0] == "Coordinate helper workers and synthesize results."
    assert ow_adapter.recorded_system_prompts[1] == "Execute focused subtask as helper."
    assert ow_res.metadata["subtasks_completed"] == 2
    assert ow_res.output["summary"] == "Audit passed across auth and DB"

    # Verify trace contains root -> supervisor -> worker spans
    ow_spans = load_trace(ow_tracer.trace_file)
    kinds = {s.kind for s in ow_spans}
    assert {"root", "supervisor", "worker", "model"}.issubset(kinds)

    # 3. Missing entrypoint_agent raises ValueError loudly
    (wf_ow / "workflow.yaml").write_text(
        yaml.dump({
            "id": "ow-wf",
            "topology": "orchestrator-workers",
            "entrypoint_agent": "nonexistent-lead",
            "agents": ["blueprints/helper.yaml", "blueprints/lead.yaml"],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Entrypoint agent 'nonexistent-lead'"):
        asyncio.run(ow_runner.run(load_workflow(wf_ow), "Audit service", ow_adapter))
