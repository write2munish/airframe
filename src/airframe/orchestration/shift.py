"""Shift Turnover Engine: manages session transitions to prevent context rot on long tasks."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from airframe.state.disk import DiskStateManager


class ShiftTurnoverEngine:
    """Manages multi-shift execution cycles, context wipes, and disk bootloading."""

    def __init__(
        self,
        context_limit_tokens: int = 128_000,
        turnover_threshold_ratio: float = 0.65,
    ) -> None:
        self.context_limit_tokens = context_limit_tokens
        self.turnover_threshold_ratio = turnover_threshold_ratio
        self.turnover_token_ceiling = int(context_limit_tokens * turnover_threshold_ratio)

    def should_turnover(self, current_tokens_used: int) -> bool:
        """Check if active context consumption breaches the turnover threshold."""
        return current_tokens_used >= self.turnover_token_ceiling

    def _checkpoint_worktree(self, worktree_dir: str | Path, shift_id: int) -> str | None:
        """Stage and commit any dirty working tree changes before wiping shift context."""
        wt_path = Path(worktree_dir).resolve()
        if not (wt_path / ".git").exists():
            return None

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=wt_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0 or not status.stdout.strip():
            return None

        subprocess.run(
            ["git", "add", "-A"],
            cwd=wt_path,
            check=True,
            capture_output=True,
            text=True,
        )

        # Preserve configured repo author, supplying fallback only if unset
        name_cfg = subprocess.run(
            ["git", "config", "user.name"], cwd=wt_path, capture_output=True, text=True, check=False
        )
        email_cfg = subprocess.run(
            ["git", "config", "user.email"], cwd=wt_path, capture_output=True, text=True, check=False
        )

        cmd = ["git"]
        if not name_cfg.stdout.strip():
            cmd.extend(["-c", "user.name=Airframe"])
        if not email_cfg.stdout.strip():
            cmd.extend(["-c", "user.email=airframe@local"])
        commit_msg = f"airframe: shift {shift_id} checkpoint"
        cmd.extend(["commit", "-m", commit_msg])

        subprocess.run(cmd, cwd=wt_path, check=True, capture_output=True, text=True)
        head_rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=wt_path, check=True, capture_output=True, text=True
        )
        return head_rev.stdout.strip()

    def execute_turnover(
        self,
        state_manager: DiskStateManager,
        shift_id: int,
        accomplishments_summary: str,
        worktree_dir: str | Path | None = None,
    ) -> int:
        """Finalize the active shift, optionally checkpoint git worktree, record handoff memo, and advance shift.

        Args:
            state_manager: DiskStateManager managing state on filesystem.
            shift_id: The ending shift index.
            accomplishments_summary: Handoff memo summarizing changes.
            worktree_dir: Optional git worktree directory to commit dirty changes into before turnover.

        Returns:
            The next shift index (shift_id + 1).
        """
        checkpoint_sha: str | None = None
        if worktree_dir is not None:
            checkpoint_sha = self._checkpoint_worktree(worktree_dir=worktree_dir, shift_id=shift_id)

        next_shift_id = shift_id + 1
        checkpoint_note = f" [git_checkpoint={checkpoint_sha[:12]}]" if checkpoint_sha else ""
        log_entry = (
            f"=== SHIFT {shift_id} COMPLETED -> TRANSITIONING TO SHIFT {next_shift_id} ==={checkpoint_note}\n"
            f"Handoff Memo: {accomplishments_summary}"
        )
        state_manager.append_progress_log(log_entry, author=f"shift_{shift_id}")
        return next_shift_id

    def bootload_shift_prompt(
        self,
        state_manager: DiskStateManager,
        shift_id: int,
        extra_instructions: str = "",
    ) -> str:
        """Construct a pristine, lean context bootload prompt from disk state.

        Wipes away all prior conversational noise and loads strictly the master plan,
        active task queue, and recent progress notes.
        """
        plan = state_manager.read_plan()
        queue = state_manager.read_task_queue()
        recent_logs = state_manager.read_progress_log(max_lines=5)

        pending_tasks = [t for t in queue.tasks if t.status in ("PENDING", "IN_PROGRESS")]
        completed_tasks = [t for t in queue.tasks if t.status == "DONE"]

        tasks_summary = {
            "pending_or_active": [t.model_dump(include={"id", "description", "status"}) for t in pending_tasks],
            "total_completed": len(completed_tasks),
        }

        prompt = (
            f"You are the autonomous agent operating on [SHIFT {shift_id}].\n\n"
            f"[MASTER PLAN]\n{plan}\n\n"
            f"[ACTIVE TASK QUEUE]\n{json.dumps(tasks_summary, indent=2)}\n\n"
            f"[RECENT PROGRESS NOTES]\n" + "\n".join(recent_logs) + "\n\n"
            f"[OBJECTIVE]\n"
            f"Pick up the next pending task. Follow strict contracts and externalize progress to disk.\n"
        )
        if extra_instructions:
            prompt += f"\n[SPECIAL INSTRUCTIONS]\n{extra_instructions}\n"

        return prompt.strip()
