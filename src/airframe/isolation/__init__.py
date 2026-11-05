"""Dual-layer isolation modules: context window partitioning, git worktrees, and sandboxes."""

from airframe.isolation.context import (
    CompressedHandoff,
    ContextPartitioner,
    TranscriptEntry,
)
from airframe.isolation.sandbox import EphemeralSandbox
from airframe.isolation.worktree import WorktreeError, WorktreeManager

__all__ = [
    "CompressedHandoff",
    "ContextPartitioner",
    "EphemeralSandbox",
    "TranscriptEntry",
    "WorktreeError",
    "WorktreeManager",
]
