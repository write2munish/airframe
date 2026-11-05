"""Unified workspace isolation strategies and dispatcher.

Maps declarative blueprint.workspace_mode ('inherit', 'branch', 'sandbox')
to physical filesystem isolation contexts.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from airframe.isolation.sandbox import EphemeralSandbox
from airframe.isolation.worktree import WorktreeManager


class WorkspaceStrategy(ABC):
    """Abstract base class for physical workspace isolation strategies."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.path: Path | None = None

    @abstractmethod
    async def enter(self) -> Path:
        """Asynchronously initialize the workspace and return its root directory."""
        pass

    @abstractmethod
    async def exit(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
        """Asynchronously tear down or prune the workspace."""
        pass

    async def __aenter__(self) -> Path:
        self.path = await self.enter()
        return self.path

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.exit(exc_type, exc_val, exc_tb)

    def __enter__(self) -> Path:
        try:
            loop = asyncio.get_running_loop()
            # If called inside an existing running event loop from sync context
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                self.path = pool.submit(asyncio.run, self.enter()).result()
        except RuntimeError:
            self.path = asyncio.run(self.enter())
        return self.path

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, self.exit(exc_type, exc_val, exc_tb)).result()
        except RuntimeError:
            asyncio.run(self.exit(exc_type, exc_val, exc_tb))


class SharedWorkspace(WorkspaceStrategy):
    """'inherit' mode: shares parent repository/directory directly with zero isolation overhead."""

    def __init__(self, path: str | Path = ".") -> None:
        super().__init__(mode="inherit")
        self.target_path = Path(path).resolve()

    async def enter(self) -> Path:
        self.path = self.target_path
        return self.path

    async def exit(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
        pass


class WorktreeWorkspace(WorkspaceStrategy):
    """'branch' mode: creates an isolated Git worktree on a dedicated branch and prunes it on exit."""

    def __init__(
        self,
        repo_path: str | Path,
        agent_id: str,
        base_ref: str = "HEAD",
        delete_on_exit: bool = True,
    ) -> None:
        super().__init__(mode="branch")
        self.repo_path = Path(repo_path).resolve()
        self.agent_id = agent_id
        self.base_ref = base_ref
        self.delete_on_exit = delete_on_exit
        self.manager = WorktreeManager()

    async def enter(self) -> Path:
        self.path = await self.manager.create_worktree(
            repo_path=self.repo_path,
            agent_id=self.agent_id,
            base_ref=self.base_ref,
        )
        return self.path

    async def exit(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
        if self.path and self.delete_on_exit:
            await self.manager.remove_worktree(
                repo_path=self.repo_path,
                agent_id=self.agent_id,
                delete_branch=True,
            )


class SandboxWorkspace(WorkspaceStrategy):
    """'sandbox' mode: creates an ephemeral temporary directory cleaned up completely on exit."""

    def __init__(self, prefix: str = "airframe_sandbox_", seed_dir: str | Path | None = None) -> None:
        super().__init__(mode="sandbox")
        self.sandbox = EphemeralSandbox(prefix=prefix, seed_dir=seed_dir)

    async def enter(self) -> Path:
        self.path = await self.sandbox.__aenter__()
        return self.path

    async def exit(self, exc_type: Any = None, exc_val: Any = None, exc_tb: Any = None) -> None:
        await self.sandbox.__aexit__(exc_type, exc_val, exc_tb)


def get_workspace(
    mode: str = "inherit",
    agent_id: str = "default",
    repo_path: str | Path = ".",
    seed_dir: str | Path | None = None,
    delete_on_exit: bool = True,
) -> WorkspaceStrategy:
    """Factory dispatcher returning the appropriate WorkspaceStrategy for the requested mode.

    Args:
        mode: 'inherit' (shared repo), 'branch' (git worktree), or 'sandbox' (ephemeral tempdir).
        agent_id: Identifier for the agent instance.
        repo_path: Path to root git repository (used for branch/inherit modes).
        seed_dir: Optional source files to seed into a sandbox directory.
        delete_on_exit: Whether to automatically clean up branches/worktrees on exit.
    """
    normalized_mode = mode.strip().lower()
    if normalized_mode in ("inherit", "shared"):
        return SharedWorkspace(path=repo_path)
    elif normalized_mode in ("branch", "worktree"):
        return WorktreeWorkspace(
            repo_path=repo_path,
            agent_id=agent_id,
            delete_on_exit=delete_on_exit,
        )
    elif normalized_mode in ("sandbox", "ephemeral"):
        return SandboxWorkspace(
            prefix=f"airframe_{agent_id}_",
            seed_dir=seed_dir,
        )
    else:
        raise ValueError(
            f"Unsupported workspace_mode: '{mode}'. Supported modes: 'inherit', 'branch', 'sandbox'."
        )
