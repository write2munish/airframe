#!/usr/bin/env python3
"""Airframe 5-Minute Interactive Developer Showcase.

Runs 100% deterministically offline with zero API keys inside a temporary workspace,
demonstrating Airframe's 5 core pillars:
  Act 1: Engine swapping & Pydantic schema self-repair loop (FR-1, FR-2)
  Act 2: Zero-decorator tools, phase tool ceiling (<=5), and Tier 1 sensors (FR-3, FR-4)
  Act 3: Git worktree isolation, live-PID lock, SIGKILL orphan reaper, and fsync crash recovery (FR-9)
  Act 4: Long-horizon multi-shift git checkpoints & governance circuit breakers (FR-11, FR-13)
  Act 5: Multi-agent Evaluator-Optimizer + Orchestrator-Workers & W3C JSONL Trace Tree replay (FR-10, FR-14)

Usage:
  PYTHONPATH=src python3 examples/showcase_walkthrough.py
  PYTHONPATH=src python3 examples/showcase_walkthrough.py --live gemini-2.0-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# Support running directly without editable install
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from airframe.adapters import ModelAdapter, ModelResponse, UsageMetadata, resolve_adapter  # noqa: E402
from airframe.governance.circuit_breakers import StallDetectedError  # noqa: E402
from airframe.isolation import (  # noqa: E402
    ContextPartitioner,
    WorktreeConflictError,
    WorktreeManager,
)
from airframe.orchestration import ShiftTurnoverEngine  # noqa: E402
from airframe.runner import (  # noqa: E402
    AgentStepDecision,
    OrchestratorPlan,
    PlannedSubtask,
    WorkerStepReport,
    WorkflowRunner,
)
from airframe.state.disk import DiskStateManager, TaskItem  # noqa: E402
from airframe.telemetry import AirframeTracer, build_trace_tree, load_trace  # noqa: E402
from airframe.tools import ScopedToolInjector, ToolRegistry, ToolScopeExceededError  # noqa: E402
from airframe.workflows.loader import load_workflow  # noqa: E402

console = Console()


class ScriptedShowcaseAdapter(ModelAdapter):
    """Deterministic adapter yielding pre-scripted replies to showcase failure -> repair paths."""

    def __init__(self, responses: list[str | ModelResponse], model_name: str = "showcase-offline") -> None:
        super().__init__(model_name=model_name)
        self._responses = list(responses)
        self.prompts_seen: list[str] = []

    @property
    def provider_name(self) -> str:
        return "showcase-mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        self.prompts_seen.append(prompt)
        if not self._responses:
            raise RuntimeError("ScriptedShowcaseAdapter ran out of responses.")
        nxt = self._responses.pop(0)
        if isinstance(nxt, ModelResponse):
            return nxt
        return ModelResponse(
            content=nxt,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=UsageMetadata(
                input_tokens=150,
                output_tokens=75,
                total_tokens=225,
                estimated_cost_usd=0.0004,
            ),
            latency_ms=12.0,
        )


def _decision_json(decision: AgentStepDecision, tokens: int = 300) -> ModelResponse:
    return ModelResponse(
        content=decision.model_dump_json(),
        model_name="showcase-offline",
        provider="showcase-mock",
        usage=UsageMetadata(
            input_tokens=tokens // 2,
            output_tokens=tokens - (tokens // 2),
            total_tokens=tokens,
            estimated_cost_usd=0.001,
        ),
        latency_ms=10.0,
    )


def _init_temp_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ShowcaseBot"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "showcase@airframe.local"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("# Showcase Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)


def _render_trace_rich_tree(node: dict[str, Any], tree_widget: Tree | None = None) -> Tree:
    dur = max(0.0, (node["end_ns"] - node["start_ns"]) / 1_000_000.0)
    status_color = "green" if node["status"] == "OK" else "red"
    label = f"[bold]{node['name']}[/bold] ([cyan]{node['kind']}[/cyan]) [{status_color}]{node['status']}[/{status_color}] {dur:.1f}ms"
    if tree_widget is None:
        branch = Tree(label)
    else:
        branch = tree_widget.add(label)
    for child in node.get("children", []):
        _render_trace_rich_tree(child, branch)
    return branch


class SwarmShowcaseAdapter(ModelAdapter):
    """Concurrency-safe scripted adapter dispatching by Pydantic target_schema and turn state."""

    def __init__(self) -> None:
        super().__init__(model_name="showcase-swarm")

    @property
    def provider_name(self) -> str:
        return "showcase-mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        schema = kwargs.get("target_schema") or kwargs.get("schema")
        if schema is OrchestratorPlan:
            payload = OrchestratorPlan(
                goal="Audit auth and sql modules",
                subtasks=[
                    PlannedSubtask(
                        task_id="auth",
                        description="Inspect auth hashing: hashlib.sha256(val.encode()).hexdigest()",
                        assigned_blueprint="security-worker",
                    ),
                    PlannedSubtask(
                        task_id="sql",
                        description="Inspect SQL query safety: cursor.execute('SELECT * FROM users WHERE id=?', (uid,))",
                        assigned_blueprint="security-worker",
                    ),
                ],
            ).model_dump_json()
        elif schema is AgentStepDecision:
            if "[Observation]:" not in prompt:
                payload = AgentStepDecision(
                    thought="Run scan_diff_markers before concluding subtask audit.",
                    action="call_tool",
                    tool_name="scan_diff_markers",
                    arguments={"code_text": prompt[:180]},
                ).model_dump_json()
            else:
                finding = "Verified clean SHA-256 hashing" if "auth" in prompt.lower() else "Verified parameterized SQL"
                payload = AgentStepDecision(
                    thought="Tool scan confirmed clean code; returning WorkerStepReport.",
                    action="final_answer",
                    final_output=WorkerStepReport(summary=finding, status="SUCCESS").model_dump(),
                ).model_dump_json()
        else:
            payload = json.dumps({
                "summary": "2/2 worker audits verified cleanly with zero security blockers.",
                "risk_level": "LOW",
                "findings_count": 0,
                "passed": True,
            })

        return ModelResponse(
            content=payload,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=UsageMetadata(input_tokens=120, output_tokens=60, total_tokens=180, estimated_cost_usd=0.0003),
            latency_ms=9.0,
        )


async def act1_engine_swap_and_contract_repair(live_engine: str | None) -> None:
    console.rule("[bold cyan]Act 1 · Swappable Engines & Executable Contract Self-Repair (FR-1, FR-2)")

    class ServiceHealth(BaseModel):
        service: str
        healthy: bool
        latency_ms: float = Field(ge=0.0)

    if live_engine:
        adapter = resolve_adapter(live_engine)
        out, resp = await adapter.invoke_contract(
            prompt="Return ServiceHealth JSON for service='checkout-api', healthy=true, latency_ms=18.5.",
            schema=ServiceHealth,
        )
        console.print(f"[green]✓ Live engine '{resp.provider}/{resp.model_name}' verified contract:[/green] {out.model_dump()}")
        return

    # Demonstrate same contract + automatic self-repair when Attempt 1 is malformed
    repair_adapter = ScriptedShowcaseAdapter([
        "Sorry! Sure, here is the health check: service checkout-api is good.",  # Invalid JSON -> triggers repair
        '{"service": "checkout-api", "healthy": true, "latency_ms": 14.2}',    # Self-repaired!
    ])
    out, resp = await repair_adapter.invoke_contract(
        prompt="Check checkout-api status.",
        schema=ServiceHealth,
        max_retries=2,
    )
    console.print(f"[green]✓ Contract self-repaired automatically across {len(repair_adapter.prompts_seen)} attempts:[/green] {out.model_dump()}")

    # Show swappable provider resolution without changing orchestration code
    tbl = Table(title="Swappable Engine Matrix (Zero Workflow Changes)")
    tbl.add_column("Engine String", style="cyan")
    tbl.add_column("Resolved Adapter Class", style="magenta")
    tbl.add_column("Provider Key", style="green")
    for eng in ("gemini-2.0-flash", "claude-3-5-sonnet-latest", "gpt-4o-mini", "ollama/llama3.2"):
        resolved = resolve_adapter(eng, api_key="demo-placeholder")
        tbl.add_row(eng, type(resolved).__name__, resolved.provider_name)
    console.print(tbl)


async def act2_tools_scoping_and_sensors(tmp_dir: Path) -> None:
    console.rule("[bold cyan]Act 2 · Zero-Decorator Tools, 5-Tool Phase Ceiling & Sensors (FR-3, FR-4)")

    pack = load_workflow(REPO_ROOT / "workflows" / "basic-calculator")
    tracer = AirframeTracer(trace_dir=tmp_dir / "traces_act2")
    runner = WorkflowRunner(tracer=tracer)

    adapter = ScriptedShowcaseAdapter([
        _decision_json(
            AgentStepDecision(
                thought="Call pure Python calculate tool instead of guessing math.",
                action="call_tool",
                tool_name="calculate",
                arguments={"expression": "(450 * 12) + (1800 / 4)"},
            )
        ),
        _decision_json(
            AgentStepDecision(
                thought="Tool confirmed 5850.0. Emit CalculationResult.",
                action="final_answer",
                final_output={
                    "query": "(450 * 12) + (1800 / 4)",
                    "result": 5850.0,
                    "formatted_result": "5,850",
                    "steps": ["450 * 12 = 5400", "1800 / 4 = 450", "5400 + 450 = 5850"],
                    "status": "SUCCESS",
                },
            )
        ),
    ])

    res = await runner.run(pack, "Calculate (450 * 12) + (1800 / 4)", adapter)
    console.print(
        f"[green]✓ Executed '{pack.id}' in {res.turns_executed} turns[/green] | "
        f"tools_called={res.tools_called} | output.result=[bold]{res.output['result']}[/bold]"
    )

    # Prove 5-tool context-diet phase ceiling rejects bloat
    reg = ToolRegistry()
    for i in range(6):
        def _fn(x: str, idx: int = i) -> str:
            return f"{idx}:{x}"
        reg.register(_fn, name=f"tool_{i}")
    scoper = ScopedToolInjector(registry=reg, max_tools_per_phase=5)
    try:
        scoper.bind_phase("execution", [f"tool_{i}" for i in range(6)])
    except ToolScopeExceededError as err:
        console.print(f"[yellow]✓ Phase ceiling enforced cleanly:[/yellow] {err}")


async def act3_worktree_crash_and_transcript_recovery(tmp_dir: Path) -> None:
    console.rule("[bold cyan]Act 3 · Git Worktrees, Live-Peer Lock, SIGKILL Reaper & Fsync Transcripts (FR-9)")

    repo_dir = tmp_dir / "isolation_repo"
    _init_temp_git_repo(repo_dir)

    child_script = tmp_dir / "claim_and_sleep.py"
    child_script.write_text(
        "import asyncio, sys\n"
        f"sys.path.insert(0, {str(SRC_DIR)!r})\n"
        "from airframe.isolation import WorktreeManager\n"
        "async def main():\n"
        f"    await WorktreeManager().create_worktree({str(repo_dir)!r}, 'sigkill-victim')\n"
        "    print('READY', flush=True)\n"
        "    await asyncio.sleep(60)\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(child_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    ready_line = proc.stdout.readline().strip()
    assert ready_line == "READY", f"Child failed: {proc.stderr.read() if proc.stderr else ''}"

    mgr = WorktreeManager()

    # 1. Live sibling process refuses clobbering
    try:
        await mgr.create_worktree(repo_dir, "sigkill-victim")
    except WorktreeConflictError as conflict:
        console.print(f"[green]✓ Live sibling protected (WorktreeConflictError):[/green] {conflict}")

    # 2. Simulate sudden crash with SIGKILL (-9) and reap orphan
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)
    reaped = await mgr.reap_stale_worktrees(repo_dir)
    console.print(f"[green]✓ Crash orphan reaped cleanly after SIGKILL:[/green] reaped={reaped}")

    # 3. ContextPartitioner truncated crash tail recovery
    partitioner = ContextPartitioner(agent_id="crash-demo", storage_dir=tmp_dir / "transcripts")
    partitioner.log_turn(role="user", content="Turn 1 committed cleanly with fsync", tokens=20)
    partitioner.log_turn(role="assistant", content="Turn 2 committed cleanly with fsync", tokens=25)
    # Simulate mid-write truncated line
    with partitioner.transcript_file.open("a", encoding="utf-8") as f:
        f.write('{"step_index": 3, "role": "assistant", "content": "TRUNCA')

    recovered = partitioner.read_transcript(tolerate_corrupt_tail=True)
    console.print(
        f"[green]✓ Transcript tail quarantined to .corrupt_tail; recovered {len(recovered)} intact turns cleanly.[/green]"
    )


async def act4_shift_turnover_and_circuit_breakers(tmp_dir: Path) -> None:
    console.rule("[bold cyan]Act 4 · Multi-Shift Git Checkpoints & Governance Circuit Breakers (FR-11, FR-13)")

    repo_dir = tmp_dir / "shift_repo"
    _init_temp_git_repo(repo_dir)

    wf_dir = tmp_dir / "shift_wf"
    wf_dir.mkdir(parents=True)
    (wf_dir / "tools.py").write_text(
        "from pathlib import Path\n\n"
        "def write_note(name: str, text: str) -> dict:\n"
        "    \"\"\"Write a milestone file inside the agent worktree.\"\"\"\n"
        "    Path(name).write_text(text, encoding='utf-8')\n"
        "    return {'written': name, 'bytes': len(text)}\n\n"
        "def unstable_tool(code: int) -> dict:\n"
        "    \"\"\"Always fails to demonstrate StallDetector circuit breaker.\"\"\"\n"
        "    raise ValueError(f'Simulated failure for code={code}')\n",
        encoding="utf-8",
    )
    (wf_dir / "workflow.yaml").write_text(
        "id: shift-showcase\n"
        "topology: single-agent\n"
        "workspace_mode: branch\n"
        "entrypoint_agent: worker\n"
        "tools: [write_note]\n"
        "system_prompt: Write milestone notes across shifts cleanly.\n"
        "governance:\n"
        "  max_turns: 8\n"
        "  timeout_seconds: 60\n",
        encoding="utf-8",
    )

    state_mgr = DiskStateManager(tmp_dir / "disk_state")
    state_mgr.init_state(
        plan_markdown="# Long-Horizon Showcase Plan\n- Create part1.txt\n- Create part2.txt",
        tasks=[TaskItem(id="task-1", description="Write milestone notes", status="IN_PROGRESS")],
    )
    shift_engine = ShiftTurnoverEngine(context_limit_tokens=1000, turnover_threshold_ratio=0.65)
    runner = WorkflowRunner(tracer=AirframeTracer(trace_dir=tmp_dir / "traces_act4"))

    adapter = ScriptedShowcaseAdapter([
        _decision_json(
            AgentStepDecision(
                thought="TURN1_NOISE creating part1",
                action="call_tool",
                tool_name="write_note",
                arguments={"name": "part1.txt", "text": "shift 1 work"},
            ),
            tokens=700,  # Crosses 650 threshold! Next turn triggers Shift 1 -> Shift 2
        ),
        _decision_json(
            AgentStepDecision(
                thought="Running in Shift 2 after clean context bootload",
                action="call_tool",
                tool_name="write_note",
                arguments={"name": "part2.txt", "text": "shift 2 work"},
            ),
            tokens=200,
        ),
        _decision_json(
            AgentStepDecision(
                thought="All milestones complete",
                action="final_answer",
                final_output={"summary": "Completed part1 and part2 across 2 clean shifts.", "status": "SUCCESS"},
            ),
            tokens=120,
        ),
    ])

    res = await runner.run(
        load_workflow(wf_dir),
        "Build milestone notes",
        adapter,
        shift_engine=shift_engine,
        state_manager=state_mgr,
        workspace_repo_path=repo_dir,
        delete_workspace_on_exit=True,
    )
    shift2_prompt = adapter.prompts_seen[1]
    noise_wiped = "TURN1_NOISE" not in shift2_prompt and "[SHIFT 2]" in shift2_prompt
    console.print(
        f"[green]✓ Shifts executed: {res.shifts_executed} | Turns: {res.turns_executed} | "
        f"Shift 1 exploration noise wiped cleanly: {noise_wiped}[/green]"
    )

    # Demonstrate StallDetector tripping on 3 identical failures
    stall_wf_dir = tmp_dir / "stall_wf"
    stall_wf_dir.mkdir(parents=True)
    (stall_wf_dir / "tools.py").write_text(
        (wf_dir / "tools.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (stall_wf_dir / "workflow.yaml").write_text(
        "id: stall-showcase\n"
        "topology: single-agent\n"
        "tools: [unstable_tool]\n"
        "governance:\n"
        "  max_turns: 8\n"
        "  stall_threshold: 3\n",
        encoding="utf-8",
    )
    stall_adapter = ScriptedShowcaseAdapter([
        _decision_json(AgentStepDecision(thought="retry 1", action="call_tool", tool_name="unstable_tool", arguments={"code": 500})),
        _decision_json(AgentStepDecision(thought="retry 2", action="call_tool", tool_name="unstable_tool", arguments={"code": 500})),
        _decision_json(AgentStepDecision(thought="retry 3", action="call_tool", tool_name="unstable_tool", arguments={"code": 500})),
    ])
    try:
        await runner.run(load_workflow(stall_wf_dir), "Trigger stall check", stall_adapter)
    except StallDetectedError as err:
        console.print(f"[yellow]✓ StallDetector halted infinite retry loop cleanly:[/yellow] {err}")


async def act5_multi_agent_and_trace_tree(tmp_dir: Path) -> None:
    console.rule("[bold cyan]Act 5 · Evaluator-Optimizer, Orchestrator-Workers & W3C Trace Tree (FR-10, FR-14)")

    # 1. Evaluator-Optimizer self-correction on workflows/patch-verifier
    eo_pack = load_workflow(REPO_ROOT / "workflows" / "patch-verifier")
    eo_tracer = AirframeTracer(trace_dir=tmp_dir / "traces_act5_eo")
    eo_runner = WorkflowRunner(tracer=eo_tracer)
    eo_adapter = ScriptedShowcaseAdapter([
        # Iteration 1 emits unsafe eval(...) -> blocked by SafePythonPatchSensor!
        _decision_json(
            AgentStepDecision(
                thought="Drafting initial quick snippet",
                action="final_answer",
                final_output={
                    "summary": "Quick draft using eval",
                    "python_code": "def double(x: int) -> int:\n    return eval('x * 2')\n",
                    "safety_notes": "draft",
                    "passed": True,
                },
            )
        ),
        # Iteration 2 reads sensor critique and removes eval(...)
        _decision_json(
            AgentStepDecision(
                thought="Critique flagged unsafe eval(); rewriting cleanly.",
                action="final_answer",
                final_output={
                    "summary": "Rewrote double() using safe native arithmetic.",
                    "python_code": "def double(x: int) -> int:\n    return x * 2\n",
                    "safety_notes": "AST-safe arithmetic only.",
                    "passed": True,
                },
            )
        ),
    ])
    eo_res = await eo_runner.run(
        eo_pack,
        "Write safe double(x: int) -> int function.",
        eo_adapter,
        sensors=eo_pack.load_sensors(),
    )
    console.print(
        f"[green]✓ Evaluator-Optimizer ('{eo_pack.id}') repaired unsafe draft in "
        f"{eo_res.metadata['iterations']} iterations![/green]"
    )

    # 2. Orchestrator-Workers on workflows/code-auditor-swarm (parallel workers using scan_diff_markers tool)
    swarm_pack = load_workflow(REPO_ROOT / "workflows" / "code-auditor-swarm")
    swarm_tracer = AirframeTracer(trace_dir=tmp_dir / "traces_act5_swarm")
    swarm_runner = WorkflowRunner(tracer=swarm_tracer)
    swarm_adapter = SwarmShowcaseAdapter()
    swarm_res = await swarm_runner.run(
        swarm_pack,
        "Audit auth and database utilities.",
        swarm_adapter,
        sensors=swarm_pack.load_sensors(),
    )
    console.print(
        f"[green]✓ Orchestrator-Workers ('{swarm_pack.id}') entrypoint='{swarm_res.entrypoint_agent}' "
        f"completed {swarm_res.metadata['subtasks_completed']} parallel workers "
        f"(tools called: {swarm_res.tools_called})[/green]"
    )

    # 3. Render hierarchical W3C JSONL trace tree from disk
    spans = load_trace(swarm_tracer.trace_file)
    tree_dict = build_trace_tree(spans)
    root_node = tree_dict["roots"][0]
    console.print(_render_trace_rich_tree(root_node))
    console.print(f"[dim]Trace persisted cleanly ({tree_dict['span_count']} spans) -> {swarm_tracer.trace_file}[/dim]")


async def async_main(live_engine: str | None) -> None:
    console.print(
        Panel(
            "[bold cyan]Airframe Agent Operating System — Interactive Showcase[/bold cyan]\n"
            f"Mode: [green]{'LIVE (' + live_engine + ')' if live_engine else 'OFFLINE DETERMINISTIC (0 API Keys)'}[/green]",
            title="✈️  airframe showcase",
        )
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="airframe_showcase_") as tmp:
        tmp_dir = Path(tmp)
        await act1_engine_swap_and_contract_repair(live_engine)
        await act2_tools_scoping_and_sensors(tmp_dir)
        await act3_worktree_crash_and_transcript_recovery(tmp_dir)
        await act4_shift_turnover_and_circuit_breakers(tmp_dir)
        await act5_multi_agent_and_trace_tree(tmp_dir)

    elapsed = time.perf_counter() - started
    console.print(f"\n[bold green]✓ All 5 Showcase Acts completed cleanly in {elapsed:.2f}s.[/bold green]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the 5-Act Airframe Showcase.")
    parser.add_argument(
        "--live",
        default=None,
        help="Optional live engine name for Act 1 (e.g., gemini-2.0-flash, claude-3-5-sonnet, ollama/llama3).",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args.live))


if __name__ == "__main__":
    main()
