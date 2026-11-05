"""Unit tests for context window partitioning, ephemeral sandboxes, and git worktrees."""

import asyncio
import subprocess
from pathlib import Path
import pytest

from airframe.isolation import (
    ContextPartitioner,
    EphemeralSandbox,
    WorktreeManager,
)


def test_context_partitioner_token_shielding(tmp_path: Path):
    """Test ContextPartitioner records private transcripts and shields intermediate tokens."""
    partitioner = ContextPartitioner(agent_id="worker_001", storage_dir=tmp_path / "transcripts")

    # Simulate noisy intermediate exploration (reading 50k tokens of logs)
    partitioner.log_turn(role="user", content="Analyze logs for error code 500.", tokens=100)
    partitioner.log_turn(role="tool", content="[DUMP: 45,000 tokens of raw access logs...]", tokens=45_000)
    partitioner.log_turn(role="assistant", content="Found root cause in line 42.", tokens=200)

    # Verify private transcript on disk
    entries = partitioner.read_transcript()
    assert len(entries) == 3
    assert entries[1].tokens == 45_000

    # Compress handoff back to the supervisor
    handoff = partitioner.compress_handoff(
        final_output="Root cause: null pointer on user.profile at line 42.",
        status="SUCCESS",
        artifacts=["fix.patch"],
        final_output_tokens=50,
    )

    # Ensure supervisor is shielded from the 45k exploration tokens!
    assert handoff.status == "SUCCESS"
    assert handoff.final_output.startswith("Root cause:")
    assert handoff.tokens_discarded >= 45_000
    assert handoff.artifacts == ["fix.patch"]
    assert handoff.total_steps == 4  # 3 exploratory + 1 final handoff


def test_ephemeral_sandbox(tmp_path: Path):
    """Test EphemeralSandbox creates and guarantees cleanup of temporary workspaces."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "template.txt").write_text("Hello template", encoding="utf-8")

    sandbox_path: Path | None = None

    with EphemeralSandbox(seed_dir=seed_dir) as box_dir:
        sandbox_path = box_dir
        assert box_dir.exists()
        assert (box_dir / "template.txt").read_text(encoding="utf-8") == "Hello template"

        # Write temp output
        (box_dir / "output.bin").write_text("Temp data", encoding="utf-8")
        assert (box_dir / "output.bin").exists()

    # Outside the context manager, the directory must be cleaned up
    assert sandbox_path is not None
    assert not sandbox_path.exists()


def test_worktree_manager_lifecycle(tmp_path: Path):
    """Test WorktreeManager creates, inspects, and cleanly prunes isolated Git worktrees."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize a temporary git repository with initial commit
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

    init_file = repo_dir / "README.md"
    init_file.write_text("# Test Repo\nInitial content\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)

    manager = WorktreeManager(base_worktree_dir=".airframe/worktrees")

    # 1. Create worktree for agent-42
    worktree_path = asyncio.run(manager.create_worktree(repo_dir, agent_id="agent-42"))
    assert worktree_path.exists()
    assert (worktree_path / "README.md").exists()

    # 2. Modify a file inside the isolated worktree
    agent_file = worktree_path / "feature.py"
    agent_file.write_text("print('feature from isolated worktree')\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.py"], cwd=worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", "Add feature"], cwd=worktree_path, check=True, capture_output=True)

    # 3. Extract diff from the worktree
    diff = asyncio.run(manager.get_diff(worktree_path))
    assert "feature from isolated worktree" in diff

    # 4. Remove worktree and ephemeral branch
    asyncio.run(manager.remove_worktree(repo_dir, agent_id="agent-42"))
    assert not worktree_path.exists()

    # Verify branch was deleted
    branches = subprocess.run(["git", "branch"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout
    assert "airframe/agent-42" not in branches
