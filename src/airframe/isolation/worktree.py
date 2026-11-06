"""Git Worktree Manager: provides physical filesystem isolation, crash compensation,
and automated stale orphan reaping for parallel workers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""
    pass


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class WorktreeManager:
    """Creates, inspects, compensates, and reaps isolated Git Worktrees for agents."""

    def __init__(self, base_worktree_dir: str | Path = ".airframe/worktrees") -> None:
        self.base_dir = Path(base_worktree_dir)

    def _registry_path(self, repo_root: Path) -> Path:
        return (repo_root / self.base_dir / "worktree_registry.json").resolve()

    def _read_registry(self, repo_root: Path) -> dict[str, Any]:
        reg_file = self._registry_path(repo_root)
        if not reg_file.is_file():
            return {}
        try:
            return json.loads(reg_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_registry(self, repo_root: Path, registry: dict[str, Any]) -> None:
        reg_file = self._registry_path(repo_root)
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = reg_file.parent / f".reg.tmp.{os.getpid()}"
        try:
            temp_file.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
            os.replace(temp_file, reg_file)
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

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

    async def reap_stale_worktrees(
        self,
        repo_path: str | Path,
        force: bool = False,
    ) -> list[str]:
        """Detect and reap orphaned worktrees left behind by crashed or terminated processes.

        Args:
            repo_path: Path to the root Git repository.
            force: If True, reap all tracked worktrees regardless of PID status.

        Returns:
            List of agent IDs whose worktrees were successfully reaped.
        """
        root = Path(repo_path).resolve()
        registry = self._read_registry(root)
        reaped: list[str] = []

        # 1. Inspect registry entries
        for agent_id, info in list(registry.items()):
            pid = info.get("pid")
            is_stale = force or (pid is not None and not is_pid_alive(pid))
            if is_stale:
                await self.remove_worktree(root, agent_id, delete_branch=True)
                reaped.append(agent_id)

        # 2. Inspect physical base_dir for orphaned directories not in registry
        base_path = root / self.base_dir
        if base_path.exists():
            for child in base_path.iterdir():
                if child.is_dir() and child.name not in registry and not child.name.startswith("."):
                    # Unregistered worktree directory
                    agent_id = child.name
                    await self.remove_worktree(root, agent_id, delete_branch=True)
                    if agent_id not in reaped:
                        reaped.append(agent_id)

        # 3. Prune git internal worktree references
        try:
            await self._run_git(root, "worktree", "prune")
        except Exception:
            pass

        return reaped

    async def create_worktree(
        self,
        repo_path: str | Path,
        agent_id: str,
        base_ref: str = "HEAD",
        reclaim_stale: bool = True,
    ) -> Path:
        """Create a dedicated Git Worktree on an isolated branch for the given agent.

        Includes automatic compensation:
        - Reaps any stale worktrees left from previously crashed processes.
        - Automatically reclaims/resets orphaned branches or paths if they already exist.
        - Performs transactional rollback on mid-creation failure.

        Args:
            repo_path: Path to the root Git repository.
            agent_id: Unique sub-agent identifier.
            base_ref: Git reference to branch off of (default: 'HEAD').
            reclaim_stale: If True, automatically resets/cleans up any existing conflicting worktree.

        Returns:
            Path to the isolated working directory.
        """
        root = Path(repo_path).resolve()
        worktree_dir = (root / self.base_dir / agent_id).resolve()
        branch_name = f"airframe/{agent_id}"

        # 1. Harvest stale orphans
        if reclaim_stale:
            # Check if this specific worktree or branch is lingering from a previous crash
            branch_exists = False
            try:
                branches = await self._run_git(root, "branch", "--list", branch_name)
                branch_exists = bool(branches.strip())
            except Exception:
                pass

            if worktree_dir.exists() or branch_exists:
                await self.remove_worktree(root, agent_id, delete_branch=True)

        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # 2. Create worktree with dedicated branch inside compensation transaction
        try:
            await self._run_git(
                root,
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_dir),
                base_ref,
            )

            # 3. Record in persistent registry
            registry = self._read_registry(root)
            registry[agent_id] = {
                "pid": os.getpid(),
                "path": str(worktree_dir),
                "branch": branch_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._write_registry(root, registry)

            return worktree_dir

        except Exception as exc:
            # Transactional compensation: rollback worktree, directory, and branch
            await self.remove_worktree(root, agent_id, delete_branch=True)
            raise WorktreeError(f"Failed to create worktree for '{agent_id}': {exc}") from exc

    async def get_diff(self, worktree_dir: str | Path) -> str:
        """Extract uncommitted and committed changes produced in the isolated worktree."""
        path = Path(worktree_dir).resolve()
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
        """Prune the isolated worktree, delete its ephemeral branch, and update registry."""
        root = Path(repo_path).resolve()
        worktree_dir = (root / self.base_dir / agent_id).resolve()
        branch_name = f"airframe/{agent_id}"

        # 1. Remove worktree via git
        if worktree_dir.exists():
            try:
                await self._run_git(root, "worktree", "remove", "--force", str(worktree_dir))
            except Exception:
                # Fallback to physical deletion + git prune if git remove failed
                shutil.rmtree(worktree_dir, ignore_errors=True)
                try:
                    await self._run_git(root, "worktree", "prune")
                except Exception:
                    pass

        # 2. Delete the ephemeral branch
        if delete_branch:
            try:
                await self._run_git(root, "branch", "-D", branch_name)
            except Exception:
                pass

        # 3. Update persistent registry
        registry = self._read_registry(root)
        if agent_id in registry:
            del registry[agent_id]
            self._write_registry(root, registry)
