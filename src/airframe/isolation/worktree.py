"""Git Worktree Manager: provides physical filesystem isolation, crash compensation,
live-sibling conflict protection, and automated stale orphan reaping for parallel workers.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable, Generator

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

from airframe.state.disk import atomic_write_text


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""
    pass


class WorktreeConflictError(WorktreeError):
    """Raised when attempting to claim a worktree held by a live peer process."""
    pass


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists, owned by another user
        return True
    except OSError:
        return False


_IN_PROCESS_LOCKS: dict[Path, threading.Lock] = {}
_IN_PROCESS_LOCKS_GUARD = threading.Lock()


def _get_in_process_lock(lock_path: Path) -> threading.Lock:
    with _IN_PROCESS_LOCKS_GUARD:
        if lock_path not in _IN_PROCESS_LOCKS:
            _IN_PROCESS_LOCKS[lock_path] = threading.Lock()
        return _IN_PROCESS_LOCKS[lock_path]


class WorktreeManager:
    """Creates, inspects, compensates, and reaps isolated Git Worktrees for agents."""

    def __init__(self, base_worktree_dir: str | Path = ".airframe/worktrees") -> None:
        self.base_dir = Path(base_worktree_dir)

    def _registry_path(self, repo_root: Path) -> Path:
        return (repo_root / self.base_dir / "worktree_registry.json").resolve()

    def _lock_path(self, repo_root: Path) -> Path:
        return (repo_root / self.base_dir / ".registry.lock").resolve()

    @contextmanager
    def _registry_lock(self, repo_root: Path) -> Generator[None, None, None]:
        """Synchronize registry access across threads, coroutines, and OS processes."""
        lock_file = self._lock_path(repo_root)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        thread_lock = _get_in_process_lock(lock_file)

        with thread_lock:
            if fcntl is None:
                yield
                return
            with open(lock_file, "a+", encoding="utf-8") as lock_fd:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
                    yield
                finally:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    def _read_registry_unlocked(self, repo_root: Path) -> dict[str, Any]:
        reg_file = self._registry_path(repo_root)
        if not reg_file.is_file():
            return {}
        try:
            data = json.loads(reg_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_registry_unlocked(self, repo_root: Path, registry: dict[str, Any]) -> None:
        reg_file = self._registry_path(repo_root)
        atomic_write_text(reg_file, json.dumps(registry, indent=2) + "\n", make_backup=False)

    def _read_registry(self, repo_root: Path) -> dict[str, Any]:
        with self._registry_lock(repo_root):
            return self._read_registry_unlocked(repo_root)

    def _write_registry(self, repo_root: Path, registry: dict[str, Any]) -> None:
        with self._registry_lock(repo_root):
            self._write_registry_unlocked(repo_root, registry)

    def _mutate_registry(self, repo_root: Path, mutator: Callable[[dict[str, Any]], None]) -> None:
        with self._registry_lock(repo_root):
            registry = self._read_registry_unlocked(repo_root)
            mutator(registry)
            self._write_registry_unlocked(repo_root, registry)

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
        """Detect and reap orphaned worktrees left behind by crashed or terminated processes."""
        root = Path(repo_path).resolve()
        registry = self._read_registry(root)
        reaped: list[str] = []

        # 1. Inspect registry entries
        for agent_id, info in list(registry.items()):
            pid = info.get("pid")
            is_stale = force or (pid is not None and not is_pid_alive(int(pid)))
            if is_stale:
                await self.remove_worktree(root, agent_id, delete_branch=True)
                reaped.append(agent_id)

        # 2. Inspect physical base_dir for orphaned directories not in registry
        base_path = root / self.base_dir
        if base_path.exists():
            current_registry = self._read_registry(root)
            for child in base_path.iterdir():
                if (
                    child.is_dir()
                    and child.name not in current_registry
                    and not child.name.startswith(".")
                ):
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
        force: bool = False,
    ) -> Path:
        """Create a dedicated Git Worktree on an isolated branch for the given agent.

        Enforces live-sibling conflict protection (WorktreeConflictError), tracks
        base_commit SHA for accurate diffs, and rolls back cleanly on mid-creation errors.
        """
        root = Path(repo_path).resolve()
        worktree_dir = (root / self.base_dir / agent_id).resolve()
        branch_name = f"airframe/{agent_id}"

        # 1. Check live sibling collision before touching any files or branches
        registry = self._read_registry(root)
        if agent_id in registry and not force:
            existing_pid = registry[agent_id].get("pid")
            if existing_pid is not None:
                pid_int = int(existing_pid)
                if is_pid_alive(pid_int) and pid_int != os.getpid():
                    raise WorktreeConflictError(
                        f"Worktree for agent '{agent_id}' is actively held by live PID {pid_int}."
                    )

        # 2. Reclaim stale worktree / branch if present
        if reclaim_stale or force:
            branch_exists = False
            try:
                branches = await self._run_git(root, "branch", "--list", branch_name)
                branch_exists = bool(branches.strip())
            except Exception:
                pass

            if worktree_dir.exists() or branch_exists or agent_id in registry:
                await self.remove_worktree(root, agent_id, delete_branch=True)

        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        # 3. Resolve deterministic base commit SHA
        base_commit = await self._run_git(root, "rev-parse", base_ref)

        # 4. Create worktree with dedicated branch inside compensation transaction
        try:
            await self._run_git(
                root,
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_dir),
                base_commit,
            )

            def _record(reg: dict[str, Any]) -> None:
                reg[agent_id] = {
                    "pid": os.getpid(),
                    "path": str(worktree_dir),
                    "branch": branch_name,
                    "base_commit": base_commit,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

            self._mutate_registry(root, _record)
            return worktree_dir

        except Exception as exc:
            await self.remove_worktree(root, agent_id, delete_branch=True)
            raise WorktreeError(f"Failed to create worktree for '{agent_id}': {exc}") from exc

    async def get_diff(self, worktree_dir: str | Path, base_ref: str | None = None) -> str:
        """Extract all committed, staged, and uncommitted changes produced in the worktree."""
        path = Path(worktree_dir).resolve()

        resolved_base = base_ref
        if resolved_base is None:
            # Locate base_commit from registry via git common-dir or ancestor walk
            try:
                common_dir = await self._run_git(path, "rev-parse", "--git-common-dir")
                repo_root = Path(common_dir).resolve().parent
                registry = self._read_registry(repo_root)
                entry = registry.get(path.name)
                if entry and entry.get("base_commit"):
                    resolved_base = entry["base_commit"]
            except Exception:
                pass

        diff_parts: list[str] = []
        if resolved_base:
            diff_out = await self._run_git(path, "diff", resolved_base)
            if diff_out:
                diff_parts.append(diff_out)
        else:
            # Fallback if created outside registry: compare working tree + HEAD~1 if present
            working_diff = await self._run_git(path, "diff", "HEAD")
            if working_diff:
                diff_parts.append(working_diff)
            try:
                commit_diff = await self._run_git(path, "diff", "HEAD~1..HEAD")
                if commit_diff:
                    diff_parts.append(commit_diff)
            except WorktreeError:
                pass

        # Include untracked files summary so new uncommitted files are never invisible
        status_out = await self.get_status(path)
        untracked = [line[3:] for line in status_out.splitlines() if line.startswith("?? ")]
        if untracked:
            diff_parts.append("Untracked files:\n" + "\n".join(f"+ {f}" for f in untracked))

        return "\n".join(diff_parts).strip()

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

        if worktree_dir.exists():
            try:
                await self._run_git(root, "worktree", "remove", "--force", str(worktree_dir))
            except Exception:
                shutil.rmtree(worktree_dir, ignore_errors=True)
                try:
                    await self._run_git(root, "worktree", "prune")
                except Exception:
                    pass

        if delete_branch:
            try:
                await self._run_git(root, "branch", "-D", branch_name)
            except Exception:
                pass

        def _delete(reg: dict[str, Any]) -> None:
            reg.pop(agent_id, None)

        self._mutate_registry(root, _delete)
