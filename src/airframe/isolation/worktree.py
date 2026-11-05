"""Git Worktree Manager: provides physical filesystem isolation for parallel workers."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""
    pass


class WorktreeManager:
    """Creates, inspects, and cleans up isolated Git Worktrees for sub-agents."""

    def __init__(self, base_worktree_dir: str | Path = ".airframe/worktrees") -> None:
        self.base_dir = Path(base_worktree_dir)

    async def _run_git(self, cwd: Path, *args: str) -> str:
        """Run a git command in the specified directory and return stdout."""
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            raise WorktreeError(f"git {' '.join(args)} failed (exit {process.returncode}): {error_msg}")
        return stdout.decode("utf-8", errors="replace").strip()

    async def create_worktree(
        self,
        repo_path: str | Path,
        agent_id: str,
        base_ref: str = "HEAD",
    ) -> Path:
        """Create a dedicated Git Worktree on an isolated branch for the given agent.

        Args:
            repo_path: Path to the root Git repository.
            agent_id: Unique sub-agent identifier.
            base_ref: Git reference to branch off of (default: 'HEAD').

        Returns:
            Path to the isolated working directory.
        """
        root = Path(repo_path).resolve()
        worktree_dir = (root / self.base_dir / agent_id).resolve()
        branch_name = f"airframe/{agent_id}"

        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # Create worktree with dedicated branch
        await self._run_git(
            root,
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_dir),
            base_ref,
        )

        return worktree_dir

    async def get_diff(self, worktree_dir: str | Path) -> str:
        """Extract uncommitted and committed changes produced in the isolated worktree."""
        path = Path(worktree_dir).resolve()
        # Combined diff against the initial branch point
        return await self._run_git(path, "diff", "HEAD~1..HEAD")

    async def get_status(self, worktree_dir: str | Path) -> str:
        """Get git status output in the worktree directory."""
        path = Path(worktree_dir).resolve()
        return await self._run_git(path, "status", "--porcelain")

    async def remove_worktree(
        self,
        repo_path: str | Path,
        agent_id: str,
        delete_branch: bool = True,
    ) -> None:
        """Prune the isolated worktree and delete its ephemeral branch.

        Args:
            repo_path: Path to the root Git repository.
            agent_id: Sub-agent identifier.
            delete_branch: Whether to delete the agent branch.
        """
        root = Path(repo_path).resolve()
        worktree_dir = (root / self.base_dir / agent_id).resolve()
        branch_name = f"airframe/{agent_id}"

        # 1. Remove the worktree via git
        if worktree_dir.exists():
            try:
                await self._run_git(root, "worktree", "remove", "--force", str(worktree_dir))
            except Exception:
                # Fallback to directory removal if git worktree remove fails
                shutil.rmtree(worktree_dir, ignore_errors=True)
                await self._run_git(root, "worktree", "prune")

        # 2. Delete the ephemeral branch
        if delete_branch:
            try:
                await self._run_git(root, "branch", "-D", branch_name)
            except Exception:
                pass
