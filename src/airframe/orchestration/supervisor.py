"""Supervisor agent: decomposes goals, assigns subtasks, and collects compressed handoffs."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from airframe.adapters.base import ModelAdapter
from airframe.blueprints.schema import AgentBlueprint
from airframe.ipc.bus import AgentMessage, EventBus
from airframe.ipc.lifecycle import AgentState, LifecycleManager
from airframe.isolation.context import CompressedHandoff, ContextPartitioner


class SubtaskSpec(BaseModel):
    """Specification for an atomic subtask delegated to a worker agent."""

    task_id: str
    description: str
    assigned_blueprint: str
    input_payload: dict[str, Any] = Field(default_factory=dict)


class SupervisorResult(BaseModel):
    """Final aggregated result emitted by the Supervisor."""

    goal: str
    status: str
    completed_subtasks: list[CompressedHandoff] = Field(default_factory=list)
    failed_subtasks: list[str] = Field(default_factory=list)
    total_tokens_shielded: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupervisorAgent:
    """Orchestrates worker agents, enforcing context shielding and task tracking."""

    def __init__(
        self,
        supervisor_id: str = "supervisor",
        bus: EventBus | None = None,
        lifecycle: LifecycleManager | None = None,
    ) -> None:
        self.supervisor_id = supervisor_id
        self.bus = bus or EventBus()
        self.lifecycle = lifecycle or LifecycleManager()
        self.bus.register(self.supervisor_id)
        self.lifecycle.register_agent(self.supervisor_id)

    async def delegate_subtask(
        self,
        subtask: SubtaskSpec,
        worker_runner: Callable[[SubtaskSpec], Coroutine[Any, Any, CompressedHandoff]],
    ) -> CompressedHandoff:
        """Spawn and delegate a subtask to an isolated worker, receiving a compressed handoff."""
        worker_id = f"worker_{subtask.task_id}"

        # Register child in lifecycle tree
        self.lifecycle.register_agent(worker_id, parent_id=self.supervisor_id)
        self.bus.register(worker_id)

        # Notify via event bus
        await self.bus.send(
            AgentMessage(
                sender_id=self.supervisor_id,
                recipient_id=worker_id,
                topic="task_assignment",
                payload=subtask.model_dump(),
            )
        )

        # Execute worker asynchronously
        task = asyncio.create_task(worker_runner(subtask))
        self.lifecycle.set_task(worker_id, task)

        try:
            handoff = await task
            self.lifecycle.set_state(worker_id, AgentState.COMPLETED)
            return handoff
        except asyncio.CancelledError:
            self.lifecycle.set_state(worker_id, AgentState.CANCELLED)
            raise
        except Exception as err:
            self.lifecycle.set_state(worker_id, AgentState.FAILED)
            return CompressedHandoff(
                agent_id=worker_id,
                status="FAILED",
                final_output=f"Worker failed: {err}",
                tokens_discarded=0,
                total_steps=1,
            )

    async def run_plan(
        self,
        goal: str,
        subtasks: list[SubtaskSpec],
        worker_runner: Callable[[SubtaskSpec], Coroutine[Any, Any, CompressedHandoff]],
    ) -> SupervisorResult:
        """Execute a sequence of subtasks, collecting handoffs and shielding context."""
        completed: list[CompressedHandoff] = []
        failed: list[str] = []
        tokens_shielded = 0

        for subtask in subtasks:
            handoff = await self.delegate_subtask(subtask, worker_runner)
            tokens_shielded += handoff.tokens_discarded

            if handoff.status == "SUCCESS":
                completed.append(handoff)
            else:
                failed.append(subtask.task_id)

        overall_status = "SUCCESS" if not failed else "PARTIAL_FAILURE"
        return SupervisorResult(
            goal=goal,
            status=overall_status,
            completed_subtasks=completed,
            failed_subtasks=failed,
            total_tokens_shielded=tokens_shielded,
        )
