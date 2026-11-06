"""Externalized state management on disk (plan.md, task_queue.json, progress.log).

Guarantees crash-safe atomicity, fsync persistence, advisory file locking (re-entrant),
and automatic corrupted state recovery from backups.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import uuid
from typing import Generator, Literal

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, Field

TaskStatus = Literal["PENDING", "IN_PROGRESS", "DONE", "FAILED"]


class CorruptedStateError(RuntimeError):
    """Raised when persistent state on disk is unparseable and cannot be recovered."""
    pass


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


def atomic_write_text(file_path: Path, content: str, make_backup: bool = True) -> None:
    """Write string content to a file atomically via tempfile, fsync, and os.replace.

    Guarantees that a crash or kill mid-write will never leave a truncated or 0-byte file.
    Optionally updates a corresponding .bak backup file before overwriting.
    """
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_file = path.parent / f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        # If original file exists, create a .bak copy first
        if make_backup and path.is_file():
            bak_file = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, bak_file)
            except OSError:
                pass

        # POSIX atomic replacement
        os.replace(temp_file, path)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


class DiskStateManager:
    """Manages persistent, crash-safe state on the filesystem with locking and backup recovery."""

    def __init__(self, workspace_dir: str | Path) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.plan_file = self.workspace_dir / "plan.md"
        self.queue_file = self.workspace_dir / "task_queue.json"
        self.queue_bak_file = self.workspace_dir / "task_queue.json.bak"
        self.log_file = self.workspace_dir / "progress.log"
        self.lock_file = self.workspace_dir / ".state.lock"
        self._lock_fd: Any = None
        self._lock_depth: int = 0

    @contextmanager
    def lock(self, exclusive: bool = True) -> Generator[None, None, None]:
        """Re-entrant advisory file lock context manager preventing concurrent read-modify-write races."""
        if fcntl is None:
            yield
            return

        # Re-entrant check: already holding lock on this manager instance
        if self._lock_depth > 0:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return

        lock_flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        self._lock_fd = open(self.lock_file, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_fd.fileno(), lock_flags)
            self._lock_depth = 1
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0 and self._lock_fd is not None:
                try:
                    fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    self._lock_fd.close()
                except OSError:
                    pass
                self._lock_fd = None

    def init_state(self, plan_markdown: str, tasks: list[TaskItem] | None = None) -> None:
        """Initialize plan.md, task_queue.json, and progress.log atomically."""
        with self.lock(exclusive=True):
            atomic_write_text(self.plan_file, plan_markdown.strip() + "\n", make_backup=False)

            initial_queue = TaskQueue(tasks=tasks or [])
            queue_json = initial_queue.model_dump_json(indent=2) + "\n"
            atomic_write_text(self.queue_file, queue_json, make_backup=True)
            # Ensure an initial valid backup exists immediately
            atomic_write_text(self.queue_bak_file, queue_json, make_backup=False)

            init_log_entry = (
                f"[{datetime.now(timezone.utc).isoformat()}] [SYSTEM] "
                f"State initialized with {len(initial_queue.tasks)} tasks.\n"
            )
            atomic_write_text(self.log_file, init_log_entry, make_backup=False)

    def read_plan(self) -> str:
        """Read high-level milestone plan from plan.md."""
        with self.lock(exclusive=False):
            if not self.plan_file.is_file():
                return ""
            return self.plan_file.read_text(encoding="utf-8")

    def read_task_queue(self) -> TaskQueue:
        """Read structured backlog from task_queue.json with automated backup recovery."""
        with self.lock(exclusive=False):
            if not self.queue_file.is_file():
                # Check if backup exists even if main file is missing
                if self.queue_bak_file.is_file():
                    try:
                        content = self.queue_bak_file.read_text(encoding="utf-8")
                        queue = TaskQueue.model_validate_json(content)
                        atomic_write_text(self.queue_file, content, make_backup=False)
                        return queue
                    except Exception:
                        pass
                return TaskQueue()

            raw_text = self.queue_file.read_text(encoding="utf-8")
            try:
                return TaskQueue.model_validate_json(raw_text)
            except Exception as parse_err:
                # Corrupted state detected (e.g. truncated or invalid JSON)
                if self.queue_bak_file.is_file():
                    try:
                        bak_content = self.queue_bak_file.read_text(encoding="utf-8")
                        recovered_queue = TaskQueue.model_validate_json(bak_content)
                        atomic_write_text(self.queue_file, bak_content, make_backup=False)
                        return recovered_queue
                    except Exception:
                        pass
                raise CorruptedStateError(
                    f"Task queue at '{self.queue_file}' is corrupted and no valid backup was found: {parse_err}"
                ) from parse_err

    def write_task_queue(self, queue: TaskQueue) -> None:
        """Write task queue to task_queue.json atomically with physical fsync and .bak rotation."""
        with self.lock(exclusive=True):
            atomic_write_text(
                self.queue_file,
                queue.model_dump_json(indent=2) + "\n",
                make_backup=True,
            )

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result_summary: str | None = None,
        assigned_agent: str | None = None,
    ) -> TaskItem:
        """Update a specific task's status under exclusive lock and write to disk."""
        with self.lock(exclusive=True):
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
        with self.lock(exclusive=False):
            queue = self.read_task_queue()
            for task in queue.tasks:
                if task.status == "PENDING":
                    return task
            return None

    def append_progress_log(self, message: str, author: str = "agent") -> None:
        """Append a timestamped handoff entry to progress.log with immediate fsync."""
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] [{author.upper()}] {message}\n"
        with self.lock(exclusive=True):
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def read_progress_log(self, max_lines: int | None = None) -> list[str]:
        """Read historical log lines under shared lock."""
        with self.lock(exclusive=False):
            if not self.log_file.is_file():
                return []
            lines = [
                line.strip()
                for line in self.log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if max_lines is not None:
                return lines[-max_lines:]
            return lines
