"""Integration tests validating end-to-end multi-agent orchestration, worktree isolation,
state externalization durability, crash compensation, and governance circuit breakers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import pytest

from airframe.governance import BudgetExceededError, BudgetTracker, StallDetector, StallDetectedError
from airframe.ipc import EventBus, LifecycleManager
from airframe.isolation import WorktreeManager, get_workspace
from airframe.orchestration import (
    EvaluatorOptimizerLoop,
    SubtaskSpec,
    SupervisorAgent,
)
from airframe.sensors import PythonASTSensor, RegexSensor, SensorPipeline
from airframe.state import DiskStateManager, TaskItem


def setup_git_repo(path: Path) -> Path:
    """Helper to initialize a clean git repository."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Integration Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "integration@test.local"], cwd=path, check=True)
    (path / "README.md").write_text("# Master Project\nInitial baseline.\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, check=True, capture_output=True)
    return path


def test_end_to_end_supervisor_with_worktree_and_disk_state(tmp_path: Path):
    """Verify Supervisor coordinates workers in isolated Git worktrees with crash-safe state updates."""
    repo_dir = setup_git_repo(tmp_path / "integration_repo")
    state_dir = tmp_path / "shared_state"

    # 1. Initialize persistent state
    state_mgr = DiskStateManager(workspace_dir=state_dir)
    tasks = [
        TaskItem(id="task-01", description="Create auth module"),
        TaskItem(id="task-02", description="Add rate limiting middleware"),
    ]
    state_mgr.init_state(plan_markdown="# Production Roadmap", tasks=tasks)

    # 2. Initialize Supervisor with state tracking and lifecycle bus
    bus = EventBus()
    lifecycle = LifecycleManager()
    supervisor = SupervisorAgent(
        supervisor_id="lead_orchestrator",
        bus=bus,
        lifecycle=lifecycle,
        state_manager=state_mgr,
    )

    # 3. Define worker runner using WorktreeWorkspace isolation
    async def worktree_worker_runner(subtask: SubtaskSpec):
        ws = get_workspace(
            mode="branch",
            agent_id=f"worker-{subtask.task_id}",
            repo_path=repo_dir,
        )
        with ws as worktree_path:
            # Worker performs physical edits in its isolated branch
            file_name = f"{subtask.task_id.replace('-', '_')}.py"
            (worktree_path / file_name).write_text(
                f"# Implementation for {subtask.description}\ndef run():\n    return '{subtask.task_id}'\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", file_name], cwd=worktree_path, check=True)
            subprocess.run(["git", "commit", "-m", f"Complete {subtask.task_id}"], cwd=worktree_path, check=True, capture_output=True)

        from airframe.isolation.context import CompressedHandoff
        return CompressedHandoff(
            agent_id=f"worker-{subtask.task_id}",
            status="SUCCESS",
            final_output=f"Created {file_name} with verified implementation",
            tokens_discarded=1500,
            total_steps=3,
        )

    # 4. Execute supervisor plan
    subtasks = [
        SubtaskSpec(task_id="task-01", description="Create auth module"),
        SubtaskSpec(task_id="task-02", description="Add rate limiting middleware"),
    ]

    result = asyncio.run(supervisor.run_plan("Build core security", subtasks, worktree_worker_runner))

    # 5. Assertions: supervisor result, shielded tokens, and disk state
    assert result.status == "SUCCESS"
    assert len(result.completed_subtasks) == 2
    assert result.total_tokens_shielded == 3000

    # Verify task queue persisted to disk
    updated_queue = state_mgr.read_task_queue()
    assert all(t.status == "DONE" for t in updated_queue.tasks)
    assert "Created task_01.py" in updated_queue.tasks[0].result_summary

    # Verify progress log has fsync entries
    logs = state_mgr.read_progress_log()
    assert len(logs) >= 4  # init + 2 transitions (IN_PROGRESS, DONE) for each task
    assert any("task-01" in l and "DONE" in l for l in logs)
    assert any("task-02" in l and "DONE" in l for l in logs)

    # Verify worktrees were cleanly pruned from disk
    worktree_base = repo_dir / ".airframe" / "worktrees"
    if worktree_base.exists():
        remaining = [d.name for d in worktree_base.iterdir() if d.is_dir() and not d.name.startswith(".")]
        assert len(remaining) == 0


def test_crash_recovery_and_worktree_compensation(tmp_path: Path):
    """Verify orphaned worktrees left by crashed workers are reaped and state survives cleanly."""
    repo_dir = setup_git_repo(tmp_path / "crash_recovery_repo")
    state_dir = tmp_path / "crash_recovery_state"

    manager = WorktreeManager(base_worktree_dir=".airframe/worktrees")
    state_mgr = DiskStateManager(workspace_dir=state_dir)
    state_mgr.init_state(
        plan_markdown="# Resilient Plan",
        tasks=[TaskItem(id="crash-task", description="Simulate crashing agent")],
    )

    # 1. Simulate an agent that crashes mid-turn inside an active worktree
    orphan_wt = asyncio.run(manager.create_worktree(repo_dir, agent_id="aborted-worker"))
    assert orphan_wt.exists()

    # Manually tamper registry to simulate dead process (PID 9999999)
    reg = manager._read_registry(repo_dir)
    reg["aborted-worker"]["pid"] = 9999999
    manager._write_registry(repo_dir, reg)

    # Corrupt the state file partially (simulating power failure mid-write)
    state_mgr.queue_file.write_text("{ incomplete json truncated", encoding="utf-8")

    # 2. Automated Recovery Triggered:
    # A. Stale worktree reaper runs
    reaped = asyncio.run(manager.reap_stale_worktrees(repo_dir))
    assert "aborted-worker" in reaped
    assert not orphan_wt.exists()

    # B. State manager recovers without amnesia from .bak
    recovered_queue = state_mgr.read_task_queue()
    assert len(recovered_queue.tasks) == 1
    assert recovered_queue.tasks[0].id == "crash-task"

    # C. Re-creating the same worktree agent ID succeeds idempotently without collision
    new_wt = asyncio.run(manager.create_worktree(repo_dir, agent_id="aborted-worker", reclaim_stale=True))
    assert new_wt.exists()
    asyncio.run(manager.remove_worktree(repo_dir, agent_id="aborted-worker"))
    assert not new_wt.exists()


def test_evaluator_optimizer_with_deterministic_sensors():
    """Verify EvaluatorOptimizerLoop iteratively repairs invalid Python code using real AST sensors."""
    sensors = SensorPipeline([
        PythonASTSensor(),
        RegexSensor(name="no_exec", pattern=r"\bexec\(", must_match=False),
    ])
    loop = EvaluatorOptimizerLoop(sensor_pipeline=sensors, max_iterations=3)

    attempts = 0

    async def code_generator(prompt: str, critique: str | None) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Iteration 1: SyntaxError (unclosed paren)
            return "def calculate_sum(a, b:\n    return a + b"
        elif attempts == 2:
            # Iteration 2: Syntax valid, but triggers forbidden regex sensor 'no_exec'
            return "def calculate_sum(a, b):\n    exec('pass')\n    return a + b\n"
        else:
            # Iteration 3: Clean, passes all sensors
            return "def calculate_sum(a, b):\n    return a + b\n"

    result = asyncio.run(loop.run("Generate sum function", code_generator))

    assert result.passed is True
    assert result.iterations == 3
    assert len(result.history) == 3
    assert result.history[0].passed is False
    assert "SyntaxError" in result.history[0].critique
    assert result.history[1].passed is False
    assert "no_exec" in result.history[1].critique
    assert result.history[2].passed is True
    assert "calculate_sum" in result.final_candidate


def test_governance_budget_breaker_halts_execution():
    """Verify BudgetTracker halts runaway agents when dollar caps are breached."""
    tracker = BudgetTracker(max_cost_usd=0.01)

    # Initial safe invocation
    tracker.record_usage(input_tokens=100, output_tokens=50, cost_usd=0.005)
    assert tracker.total_cost_usd == 0.005

    # Breaching invocation: must raise BudgetExceededError immediately
    with pytest.raises(BudgetExceededError, match="Cost budget exceeded"):
        tracker.record_usage(input_tokens=200, output_tokens=100, cost_usd=0.008)
