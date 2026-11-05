"""Agent lifecycle states, hierarchy trees, and recursive cancellation cascades."""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Callable, Coroutine, Any

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    """Lifecycle states of an Airframe agent instance."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentNode(BaseModel):
    """Represents a node in the agent hierarchy tree."""

    agent_id: str
    parent_id: str | None = None
    state: AgentState = AgentState.PENDING
    children_ids: list[str] = Field(default_factory=list)


class LifecycleManager:
    """Tracks agent hierarchy and manages cancellation cascades and resource cleanup."""

    def __init__(self) -> None:
        self._nodes: dict[str, AgentNode] = {}
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cleanup_hooks: dict[str, list[Callable[[], Coroutine[Any, Any, None]]]] = {}

    def register_agent(self, agent_id: str, parent_id: str | None = None) -> AgentNode:
        """Register a new agent in the lifecycle DAG."""
        node = AgentNode(agent_id=agent_id, parent_id=parent_id, state=AgentState.PENDING)
        self._nodes[agent_id] = node

        if parent_id and parent_id in self._nodes:
            self._nodes[parent_id].children_ids.append(agent_id)

        self._cleanup_hooks[agent_id] = []
        return node

    def set_task(self, agent_id: str, task: asyncio.Task[Any]) -> None:
        """Bind an active asyncio.Task to an agent."""
        self._active_tasks[agent_id] = task
        if agent_id in self._nodes:
            self._nodes[agent_id].state = AgentState.RUNNING

    def add_cleanup_hook(self, agent_id: str, hook: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register an async cleanup callback (e.g. removing git worktree or sandbox)."""
        if agent_id not in self._cleanup_hooks:
            self._cleanup_hooks[agent_id] = []
        self._cleanup_hooks[agent_id].append(hook)

    def set_state(self, agent_id: str, state: AgentState) -> None:
        """Update an agent's lifecycle state."""
        if agent_id in self._nodes:
            self._nodes[agent_id].state = state

    def get_state(self, agent_id: str) -> AgentState | None:
        """Get the current lifecycle state of an agent."""
        node = self._nodes.get(agent_id)
        return node.state if node else None

    async def cancel_cascade(self, root_agent_id: str, reason: str = "Parent cancelled") -> list[str]:
        """Recursively cancel an agent and all of its descendants, executing cleanup hooks.

        Args:
            root_agent_id: Root agent to terminate.
            reason: Diagnostic cancellation rationale.

        Returns:
            List of all agent IDs cancelled in the cascade.
        """
        cancelled_agents: list[str] = []

        async def _cancel_recursive(agent_id: str) -> None:
            node = self._nodes.get(agent_id)
            if not node:
                return

            # Recursively cancel all children first (bottom-up)
            for child_id in list(node.children_ids):
                await _cancel_recursive(child_id)

            # Cancel active asyncio task
            task = self._active_tasks.pop(agent_id, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Execute registered cleanup hooks (e.g. worktrees)
            hooks = self._cleanup_hooks.pop(agent_id, [])
            for hook in hooks:
                try:
                    await hook()
                except Exception:
                    pass

            node.state = AgentState.CANCELLED
            cancelled_agents.append(agent_id)

        await _cancel_recursive(root_agent_id)
        return cancelled_agents
