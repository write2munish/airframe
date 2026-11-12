"""Supervisor agent: decomposes goals, assigns subtasks, tracks disk state, and collects compressed handoffs."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from airframe.governance.circuit_breakers import (
    BudgetExceededError,
    BudgetReservation,
    BudgetTracker,
    RecursionLimiter,
)
from airframe.ipc.bus import AgentMessage, EventBus
from airframe.ipc.lifecycle import AgentState, LifecycleManager
from airframe.isolation.context import CompressedHandoff, ContextPartitioner
from airframe.state.disk import DiskStateManager
from airframe.telemetry.tracer import AirframeTracer, get_active_tracer


class SubtaskSpec(BaseModel):
    """Specification for an atomic subtask delegated to a worker agent."""

    task_id: str
    description: str
    assigned_blueprint: str = "default_worker"
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
    """Orchestrates worker agents, enforcing context shielding, lifecycle states, and state persistence."""

    def __init__(
        self,
        supervisor_id: str = "supervisor",
        bus: EventBus | None = None,
        lifecycle: LifecycleManager | None = None,
        state_manager: DiskStateManager | None = None,
        max_depth: int = 2,
        tracer: AirframeTracer | None = None,
        budget_tracker: BudgetTracker | None = None,
        default_worker_reserve_tokens: int = 64,
        default_worker_reserve_cost_usd: float = 0.0,
    ) -> None:
        self.supervisor_id = supervisor_id
        self.bus = bus or EventBus()
        self.lifecycle = lifecycle or LifecycleManager()
        self.state_manager = state_manager
        self.recursion_limiter = RecursionLimiter(max_depth=max_depth)
        self.tracer = tracer
        self.budget_tracker = budget_tracker
        self.default_worker_reserve_tokens = max(0, int(default_worker_reserve_tokens))
        self.default_worker_reserve_cost_usd = max(0.0, float(default_worker_reserve_cost_usd))
        self.bus.register(self.supervisor_id)
        self.lifecycle.register_agent(self.supervisor_id)

    def _resolve_worker_reservation(
        self,
        *,
        subtask: SubtaskSpec,
        reserve_tokens: int | None = None,
        reserve_cost_usd: float | None = None,
    ) -> tuple[int, float]:
        """Compute pre-flight token and USD reservation amounts for a delegated worker task."""
        payload = subtask.input_payload if isinstance(subtask.input_payload, dict) else {}
        if reserve_tokens is not None:
            est_tokens = int(reserve_tokens)
        elif "estimated_tokens" in payload:
            est_tokens = int(payload["estimated_tokens"])
        else:
            est_tokens = self.default_worker_reserve_tokens

        if reserve_cost_usd is not None:
            est_cost = float(reserve_cost_usd)
        elif "estimated_cost_usd" in payload:
            est_cost = float(payload["estimated_cost_usd"])
        else:
            est_cost = self.default_worker_reserve_cost_usd

        if est_cost == 0.0 and self.budget_tracker is not None and self.budget_tracker.max_cost_usd is not None:
            if self.budget_tracker.total_tokens > 0 and self.budget_tracker.total_cost_usd > 0.0:
                observed_rate = self.budget_tracker.total_cost_usd / self.budget_tracker.total_tokens
                est_cost = max(0, est_tokens) * observed_rate

        return max(0, est_tokens), max(0.0, est_cost)

    async def _default_worker_runner(self, subtask: SubtaskSpec) -> CompressedHandoff:
        """Default autonomous execution engine for a subtask if no custom runner is provided."""
        partitioner = ContextPartitioner(agent_id=f"worker_{subtask.task_id}")
        partitioner.log_turn(role="user", content=subtask.description, tokens=50)

        # Simulate isolated tool work and reasoning
        partitioner.log_turn(
            role="assistant",
            content=f"Completed {subtask.description} successfully.",
            tokens=120,
        )

        return partitioner.compress_handoff(
            final_output=f"Executed task '{subtask.task_id}': {subtask.description}",
            status="SUCCESS",
            final_output_tokens=30,
        )

    async def delegate_subtask(
        self,
        subtask: SubtaskSpec,
        worker_runner: Callable[[SubtaskSpec], Coroutine[Any, Any, CompressedHandoff]] | None = None,
        depth: int = 1,
        *,
        reserve_tokens: int | None = None,
        reserve_cost_usd: float | None = None,
    ) -> CompressedHandoff:
        """Spawn and delegate a subtask to an isolated worker, enforcing recursion depth and span tracing."""
        self.recursion_limiter.check_depth(depth)

        active_tracer = self.tracer or get_active_tracer()

        async def _execute_delegation() -> CompressedHandoff:
            worker_id = f"worker_{subtask.task_id}"
            runner = worker_runner or self._default_worker_runner

            # Register child in lifecycle tree
            self.lifecycle.register_agent(worker_id, parent_id=self.supervisor_id)
            self.bus.register(worker_id)

            # Update disk state if state manager is wired
            if self.state_manager is not None:
                try:
                    self.state_manager.update_task_status(
                        subtask.task_id,
                        status="IN_PROGRESS",
                        assigned_agent=worker_id,
                    )
                except KeyError:
                    pass

            # Notify via event bus
            await self.bus.send(
                AgentMessage(
                    sender_id=self.supervisor_id,
                    recipient_id=worker_id,
                    topic="task_assignment",
                    payload=subtask.model_dump(),
                )
            )

            # Reserve worker budget capacity synchronously right before spawning task
            reservation: BudgetReservation | None = None
            if self.budget_tracker is not None:
                reserve_tok, reserve_cost = self._resolve_worker_reservation(
                    subtask=subtask,
                    reserve_tokens=reserve_tokens,
                    reserve_cost_usd=reserve_cost_usd,
                )
                try:
                    reservation = self.budget_tracker.reserve(
                        estimated_tokens=reserve_tok,
                        estimated_cost_usd=reserve_cost,
                        label=f"supervisor.delegate:{worker_id}",
                    )
                except BudgetExceededError as err:
                    self.lifecycle.set_state(worker_id, AgentState.FAILED)
                    if self.state_manager is not None:
                        try:
                            self.state_manager.update_task_status(
                                subtask.task_id,
                                status="FAILED",
                                result_summary=f"Budget exceeded before worker spawn: {err}",
                                assigned_agent=worker_id,
                            )
                        except KeyError:
                            pass
                    raise

            try:
                # Execute worker asynchronously (contextvars propagate parent span_id to task)
                task = asyncio.create_task(runner(subtask))
                self.lifecycle.set_task(worker_id, task)

                try:
                    handoff = await task
                    self.lifecycle.set_state(worker_id, AgentState.COMPLETED)
                    if self.state_manager is not None:
                        try:
                            self.state_manager.update_task_status(
                                subtask.task_id,
                                status="DONE",
                                result_summary=handoff.final_output,
                                assigned_agent=worker_id,
                            )
                        except KeyError:
                            pass
                    return handoff
                except asyncio.CancelledError:
                    self.lifecycle.set_state(worker_id, AgentState.CANCELLED)
                    if self.state_manager is not None:
                        try:
                            self.state_manager.update_task_status(
                                subtask.task_id,
                                status="FAILED",
                                result_summary="Task cancelled by supervisor",
                                assigned_agent=worker_id,
                            )
                        except KeyError:
                            pass
                    raise
                except BudgetExceededError as err:
                    self.lifecycle.set_state(worker_id, AgentState.FAILED)
                    if self.state_manager is not None:
                        try:
                            self.state_manager.update_task_status(
                                subtask.task_id,
                                status="FAILED",
                                result_summary=f"Worker halted: {err}",
                                assigned_agent=worker_id,
                            )
                        except KeyError:
                            pass
                    raise
                except Exception as err:
                    self.lifecycle.set_state(worker_id, AgentState.FAILED)
                    if self.state_manager is not None:
                        try:
                            self.state_manager.update_task_status(
                                subtask.task_id,
                                status="FAILED",
                                result_summary=f"Worker failed: {err}",
                                assigned_agent=worker_id,
                            )
                        except KeyError:
                            pass
                    return CompressedHandoff(
                        agent_id=worker_id,
                        status="FAILED",
                        final_output=f"Worker failed: {err}",
                        tokens_discarded=0,
                        total_steps=1,
                    )
            finally:
                if self.budget_tracker is not None and reservation is not None:
                    self.budget_tracker.release(reservation)

        if active_tracer is not None:
            async with active_tracer.start_span(
                f"worker.{subtask.task_id}",
                kind="worker",
                attributes={"task_id": subtask.task_id, "depth": depth, "blueprint": subtask.assigned_blueprint},
            ):
                return await _execute_delegation()
        return await _execute_delegation()

    async def run_plan(
        self,
        goal: str,
        subtasks: list[SubtaskSpec],
        worker_runner: Callable[[SubtaskSpec], Coroutine[Any, Any, CompressedHandoff]] | None = None,
        depth: int = 1,
    ) -> SupervisorResult:
        """Execute a sequence of subtasks, collecting handoffs and shielding context."""
        active_tracer = self.tracer or get_active_tracer()

        async def _run_subtasks() -> SupervisorResult:
            completed: list[CompressedHandoff] = []
            failed: list[str] = []
            tokens_shielded = 0

            for subtask in subtasks:
                handoff = await self.delegate_subtask(subtask, worker_runner, depth=depth)
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

        if active_tracer is not None:
            async with active_tracer.start_span(
                f"supervisor.{self.supervisor_id}",
                kind="supervisor",
                attributes={"goal": goal[:160], "subtask_count": len(subtasks)},
            ):
                return await _run_subtasks()
        return await _run_subtasks()
