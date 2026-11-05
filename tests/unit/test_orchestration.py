"""Unit tests for multi-agent orchestration, async IPC, and cancellation cascades."""

import asyncio
import pytest

from airframe.ipc import AgentMessage, AgentState, EventBus, LifecycleManager
from airframe.isolation.context import CompressedHandoff
from airframe.orchestration import (
    EvaluatorOptimizerLoop,
    SubtaskSpec,
    SupervisorAgent,
)
from airframe.sensors import PythonASTSensor, SensorPipeline


def test_event_bus_point_to_point_and_broadcast():
    """Test EventBus delivers point-to-point and broadcast messages without busy polling."""
    bus = EventBus()
    bus.register("agent_a")
    bus.register("agent_b")
    bus.subscribe("agent_b", "alerts")

    # Direct message from A to B
    msg = AgentMessage(sender_id="agent_a", recipient_id="agent_b", payload={"data": 123})
    delivered = asyncio.run(bus.send(msg))
    assert delivered is True

    received = asyncio.run(bus.receive("agent_b", timeout=1.0))
    assert received.sender_id == "agent_a"
    assert received.payload["data"] == 123

    # Broadcast on topic 'alerts'
    bcast = AgentMessage(sender_id="agent_a", recipient_id="*", topic="alerts", payload="System update")
    asyncio.run(bus.send(bcast))

    bcast_received = asyncio.run(bus.receive("agent_b", timeout=1.0))
    assert bcast_received.payload == "System update"


def test_lifecycle_cancellation_cascade():
    """Test recursive cancellation cascade aborts child tasks and triggers cleanup hooks."""
    lifecycle = LifecycleManager()
    cleanup_executed = False

    # Hierarchy: supervisor -> worker -> subworker
    lifecycle.register_agent("supervisor")
    lifecycle.register_agent("worker", parent_id="supervisor")
    lifecycle.register_agent("subworker", parent_id="worker")

    async def mock_cleanup():
        nonlocal cleanup_executed
        cleanup_executed = True

    lifecycle.add_cleanup_hook("subworker", mock_cleanup)

    # Trigger cancellation cascade from supervisor
    cancelled = asyncio.run(lifecycle.cancel_cascade("supervisor"))

    # All descendants must be cancelled in bottom-up order
    assert "subworker" in cancelled
    assert "worker" in cancelled
    assert "supervisor" in cancelled
    assert lifecycle.get_state("supervisor") == AgentState.CANCELLED
    assert lifecycle.get_state("worker") == AgentState.CANCELLED
    assert lifecycle.get_state("subworker") == AgentState.CANCELLED
    assert cleanup_executed is True


def test_supervisor_subtask_delegation_and_shielding():
    """Test Supervisor delegates subtasks and collects compressed handoffs with token shielding."""
    supervisor = SupervisorAgent(supervisor_id="lead_planner")

    subtasks = [
        SubtaskSpec(task_id="t1", description="Search database", assigned_blueprint="researcher"),
        SubtaskSpec(task_id="t2", description="Format output", assigned_blueprint="formatter"),
    ]

    async def mock_worker(spec: SubtaskSpec) -> CompressedHandoff:
        # Simulate worker that used 20,000 tokens of intermediate search queries
        return CompressedHandoff(
            agent_id=f"worker_{spec.task_id}",
            status="SUCCESS",
            final_output=f"Output for {spec.task_id}",
            tokens_discarded=20_000,
            total_steps=5,
        )

    result = asyncio.run(supervisor.run_plan("Complete user research", subtasks, mock_worker))

    assert result.status == "SUCCESS"
    assert len(result.completed_subtasks) == 2
    assert result.total_tokens_shielded == 40_000  # 20k per worker shielded from supervisor context!


def test_evaluator_optimizer_loop_self_correction():
    """Test EvaluatorOptimizerLoop catches sensor failure and self-corrects on next iteration."""
    pipeline = SensorPipeline([PythonASTSensor()])
    loop = EvaluatorOptimizerLoop(sensor_pipeline=pipeline, max_iterations=3)

    iteration_count = 0

    async def mock_generator(prompt: str, critique: str | None) -> str:
        nonlocal iteration_count
        iteration_count += 1
        if iteration_count == 1:
            # Emits syntax error on first attempt
            return "def broken_code(:"
        # Corrects on second attempt informed by critique
        return "def fixed_code():\n    return 42\n"

    res = asyncio.run(loop.run("Write a python function", mock_generator))

    assert res.passed is True
    assert res.iterations == 2
    assert "SyntaxError" in res.history[0].critique
    assert res.history[1].passed is True
    assert "fixed_code" in res.final_candidate
