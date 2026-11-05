"""Externalized state management on disk (plan.md, task_queue.json, progress.log)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["PENDING", "IN_PROGRESS", "DONE", "FAILED"]


class TaskItem(BaseModel):
    """An individual atomic work item in the task queue."""

    id: str
    description: str
    status: TaskStatus = "PENDING"
    assigned_agent: str | None = None
    result_summary: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None


class TaskQueue(BaseModel):
    """Backlog of work items persisted on disk."""

    tasks: list[TaskItem] = Field(default_factory=list)


class DiskStateManager:
    """Manages persistent state on the filesystem to eliminate amnesia across sessions."""

    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.plan_file = self.workspace_dir / "plan.md"
        self.queue_file = self.workspace_dir / "task_queue.json"
        self.log_file = self.workspace_dir / "progress.log"

    def init_state(self, plan_markdown: str, tasks: list[TaskItem] | None = None) -> None:
        """Initialize plan.md, task_queue.json, and progress.log."""
        self.plan_file.write_text(plan_markdown.strip() + "\n", encoding="utf-8")

        initial_queue = TaskQueue(tasks=tasks or [])
        self.queue_file.write_text(
            initial_queue.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

        init_log_entry = (
            f"[{datetime.now(timezone.utc).isoformat()}] [SYSTEM] "
            f"State initialized with {len(initial_queue.tasks)} tasks.\n"
        )
        self.log_file.write_text(init_log_entry, encoding="utf-8")

    def read_plan(self) -> str:
        """Read high-level milestone plan from plan.md."""
        if not self.plan_file.is_file():
            return ""
        return self.plan_file.read_text(encoding="utf-8")

    def read_task_queue(self) -> TaskQueue:
        """Read structured backlog from task_queue.json."""
        if not self.queue_file.is_file():
            return TaskQueue()
        return TaskQueue.model_validate_json(self.queue_file.read_text(encoding="utf-8"))

    def write_task_queue(self, queue: TaskQueue) -> None:
        """Write task queue to task_queue.json atomically."""
        self.queue_file.write_text(queue.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result_summary: str | None = None,
        assigned_agent: str | None = None,
    ) -> TaskItem:
        """Update a specific task's status and write to disk."""
        queue = self.read_task_queue()
        target_task: TaskItem | None = None

        for task in queue.tasks:
            if task.id == task_id:
                task.status = status
                if result_summary is not None:
                    task.result_summary = result_summary
                if assigned_agent is not None:
                    task.assigned_agent = assigned_agent
                if status in ("DONE", "FAILED"):
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                target_task = task
                break

        if target_task is None:
            raise KeyError(f"Task '{task_id}' not found in task queue.")

        self.write_task_queue(queue)
        self.append_progress_log(
            f"Task '{task_id}' transitioned to {status}. Summary: {result_summary or 'N/A'}",
            author=assigned_agent or "agent",
        )
        return target_task

    def get_next_pending_task(self) -> TaskItem | None:
        """Find the first task with status 'PENDING'."""
        queue = self.read_task_queue()
        for task in queue.tasks:
            if task.status == "PENDING":
                return task
        return None

    def append_progress_log(self, message: str, author: str = "agent") -> None:
        """Append a timestamped handoff entry to progress.log."""
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] [{author.upper()}] {message}\n"
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(line)

    def read_progress_log(self, max_lines: int | None = None) -> list[str]:
        """Read historical log lines."""
        if not self.log_file.is_file():
            return []
        lines = [line.strip() for line in self.log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if max_lines is not None:
            return lines[-max_lines:]
        return lines
