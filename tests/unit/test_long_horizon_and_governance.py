"""Unit tests for long-horizon persistence, shift turnover, and governance circuit breakers."""

from pathlib import Path
import pytest

from airframe.governance import (
    ApprovalRejectedError,
    BudgetExceededError,
    BudgetTracker,
    HumanApprovalGate,
    RecursionDepthExceededError,
    RecursionLimiter,
    StallDetectedError,
    StallDetector,
)
from airframe.orchestration import ShiftTurnoverEngine
from airframe.state import DiskStateManager, TaskItem


# ============================================================================
# State & Long-Horizon Persistence Tests
# ============================================================================

def test_disk_state_manager_lifecycle(tmp_path: Path):
    """Test DiskStateManager manages plan.md, task_queue.json, and progress.log."""
    manager = DiskStateManager(workspace_dir=tmp_path / "agent_state")

    tasks = [
        TaskItem(id="t1", description="Implement auth endpoints"),
        TaskItem(id="t2", description="Write integration tests"),
    ]

    manager.init_state(plan_markdown="# Milestone 1: Authentication System", tasks=tasks)

    # 1. Read plan
    assert "Milestone 1: Authentication System" in manager.read_plan()

    # 2. Get next pending task
    next_task = manager.get_next_pending_task()
    assert next_task is not None
    assert next_task.id == "t1"
    assert next_task.status == "PENDING"

    # 3. Update task status to DONE
    updated = manager.update_task_status("t1", status="DONE", result_summary="JWT endpoints merged")
    assert updated.status == "DONE"
    assert updated.result_summary == "JWT endpoints merged"
    assert updated.completed_at is not None

    # 4. Next pending should now be t2
    next_task_2 = manager.get_next_pending_task()
    assert next_task_2 is not None
    assert next_task_2.id == "t2"

    # 5. Check progress log
    logs = manager.read_progress_log()
    assert len(logs) >= 2
    assert any("JWT endpoints merged" in line for line in logs)


def test_shift_turnover_engine(tmp_path: Path):
    """Test ShiftTurnoverEngine triggers turnover at capacity and bootloads next shift."""
    engine = ShiftTurnoverEngine(context_limit_tokens=100_000, turnover_threshold_ratio=0.65)

    # Under 65% ceiling
    assert engine.should_turnover(current_tokens_used=50_000) is False

    # Over 65% ceiling
    assert engine.should_turnover(current_tokens_used=66_000) is True

    # State manager setup
    state_mgr = DiskStateManager(workspace_dir=tmp_path / "shift_state")
    state_mgr.init_state(
        plan_markdown="# Feature Plan",
        tasks=[TaskItem(id="t1", description="Pending work", status="PENDING")],
    )

    # Execute turnover from Shift 1 -> Shift 2
    next_shift = engine.execute_turnover(
        state_manager=state_mgr,
        shift_id=1,
        accomplishments_summary="Completed discovery phase; ready for implementation.",
    )
    assert next_shift == 2

    # Bootload fresh prompt for Shift 2
    bootload_prompt = engine.bootload_shift_prompt(state_manager=state_mgr, shift_id=2)
    assert "[SHIFT 2]" in bootload_prompt
    assert "# Feature Plan" in bootload_prompt
    assert "Pending work" in bootload_prompt
    assert "Completed discovery phase" in bootload_prompt


# ============================================================================
# Governance & Circuit Breaker Tests
# ============================================================================

def test_budget_tracker_enforces_ceilings():
    """Test BudgetTracker trips circuit breaker when token or cost limits are breached."""
    # Token ceiling
    token_tracker = BudgetTracker(max_tokens=1000)
    token_tracker.record_usage(input_tokens=400, output_tokens=400)
    assert token_tracker.total_tokens == 800

    with pytest.raises(BudgetExceededError, match="Token budget exceeded"):
        token_tracker.record_usage(input_tokens=150, output_tokens=100)  # 1050 > 1000

    # Dollar cost ceiling
    cost_tracker = BudgetTracker(max_cost_usd=0.05)
    cost_tracker.record_usage(input_tokens=10, output_tokens=10, cost_usd=0.04)

    with pytest.raises(BudgetExceededError, match="Cost budget exceeded"):
        cost_tracker.record_usage(input_tokens=10, output_tokens=10, cost_usd=0.02)  # 0.06 > 0.05


def test_stall_detector_catches_infinite_loops():
    """Test StallDetector trips circuit breaker after repeating identical failure signatures."""
    detector = StallDetector(stall_threshold=3)

    # Different actions or successes reset counter
    detector.record_attempt(action_signature="edit_file(app.py)", success=False, error_message="SyntaxErr")
    detector.record_attempt(action_signature="read_file(app.py)", success=True)

    # 3 consecutive identical failures
    detector.record_attempt(action_signature="run_command(npm test)", success=False, error_message="Failing test")
    detector.record_attempt(action_signature="run_command(npm test)", success=False, error_message="Failing test")

    with pytest.raises(StallDetectedError, match="Agent stalled"):
        detector.record_attempt(action_signature="run_command(npm test)", success=False, error_message="Failing test")


def test_recursion_limiter():
    """Test RecursionLimiter enforces maximum sub-agent nesting depth."""
    limiter = RecursionLimiter(max_depth=2)

    limiter.check_depth(current_depth=1)
    limiter.check_depth(current_depth=2)

    with pytest.raises(RecursionDepthExceededError, match="exceeds maximum ceiling of 2"):
        limiter.check_depth(current_depth=3)


def test_human_approval_gate():
    """Test HumanApprovalGate flags high-risk actions and requires interactive confirmation."""
    gate = HumanApprovalGate()

    assert gate.requires_approval("rm -rf /var/data") is True
    assert gate.requires_approval("git push --force origin main") is True
    assert gate.requires_approval("cat README.md") is False

    # Approved case
    assert gate.request_approval("delete temp file", approval_callback=lambda desc: True) is True

    # Rejected case
    with pytest.raises(ApprovalRejectedError, match="rejected by human operator"):
        gate.request_approval("drop table users", approval_callback=lambda desc: False)


def test_disk_state_crash_safety_and_backup_recovery(tmp_path: Path):
    """Test DiskStateManager survives corruptions and automatically recovers from .bak."""
    from airframe.state import CorruptedStateError, atomic_write_text

    state_dir = tmp_path / "crash_test_state"
    manager = DiskStateManager(workspace_dir=state_dir)

    tasks = [
        TaskItem(id="task-1", description="Valid task 1"),
        TaskItem(id="task-2", description="Valid task 2"),
    ]
    manager.init_state(plan_markdown="# Crash Test Plan", tasks=tasks)

    # Verify initial files and backup
    assert manager.queue_file.exists()
    assert manager.queue_bak_file.exists()
    assert len(manager.read_task_queue().tasks) == 2

    # Simulate catastrophic crash mid-write: file truncated to 0 bytes or corrupted
    manager.queue_file.write_text("{ \"tasks\": [ INVALID JSON TRUNCATED ...", encoding="utf-8")

    # read_task_queue should detect corruption, recover from .bak, and self-heal
    recovered_queue = manager.read_task_queue()
    assert len(recovered_queue.tasks) == 2
    assert recovered_queue.tasks[0].id == "task-1"
    # Ensure queue_file was self-healed on disk
    assert "INVALID JSON" not in manager.queue_file.read_text(encoding="utf-8")

    # Simulate 0-byte file
    manager.queue_file.write_text("", encoding="utf-8")
    recovered_queue_2 = manager.read_task_queue()
    assert len(recovered_queue_2.tasks) == 2

    # Simulate unrecoverable corruption (both main and .bak corrupted)
    manager.queue_file.write_text("CORRUPTED", encoding="utf-8")
    manager.queue_bak_file.write_text("CORRUPTED_TOO", encoding="utf-8")

    with pytest.raises(CorruptedStateError, match="corrupted and no valid backup"):
        manager.read_task_queue()


def test_concurrent_state_locking(tmp_path: Path):
    """Test advisory file locking protects task queue updates across concurrent threads."""
    import concurrent.futures

    state_dir = tmp_path / "concurrent_state"
    manager = DiskStateManager(workspace_dir=state_dir)

    # Initialize 10 tasks
    tasks = [TaskItem(id=f"t{i}", description=f"Task {i}") for i in range(10)]
    manager.init_state(plan_markdown="# Concurrent Plan", tasks=tasks)

    def worker_update(task_id: str) -> None:
        local_mgr = DiskStateManager(workspace_dir=state_dir)
        local_mgr.update_task_status(task_id, status="DONE", result_summary=f"Finished {task_id}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker_update, f"t{i}") for i in range(10)]
        for fut in futures:
            fut.result()

    final_queue = manager.read_task_queue()
    assert len(final_queue.tasks) == 10
    assert all(t.status == "DONE" for t in final_queue.tasks)
    assert all(t.result_summary is not None for t in final_queue.tasks)

