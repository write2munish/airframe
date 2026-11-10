"""Dual-layer isolation modules: context window partitioning, git worktrees, and sandboxes."""

from airframe.isolation.context import (
    CompressedHandoff,
    ContextPartitioner,
    CorruptedTranscriptError,
    TranscriptEntry,
)
from airframe.isolation.sandbox import EphemeralSandbox
from airframe.isolation.workspace import (
    SandboxWorkspace,
    SharedWorkspace,
    WorkspaceStrategy,
    WorktreeWorkspace,
    get_workspace,
)
from airframe.isolation.worktree import (
    WorktreeConflictError,
    WorktreeError,
    WorktreeManager,
)

__all__ = [
    "CompressedHandoff",
    "ContextPartitioner",
    "CorruptedTranscriptError",
    "EphemeralSandbox",
    "SandboxWorkspace",
    "SharedWorkspace",
    "TranscriptEntry",
    "WorkspaceStrategy",
    "WorktreeConflictError",
    "WorktreeError",
    "WorktreeManager",
    "WorktreeWorkspace",
    "get_workspace",
]
