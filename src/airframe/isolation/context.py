"""Context Window Partitioner: manages private transcripts and token-discarding handoffs.

Guarantees crash-safe fsync logging and tail-tolerant transcript recovery.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

from pydantic import BaseModel, Field

from airframe.state.disk import atomic_write_text


class CorruptedTranscriptError(RuntimeError):
    """Raised when non-terminal transcript entries are corrupted on disk."""
    pass


class TranscriptEntry(BaseModel):
    """An individual event or turn in an agent's private execution transcript."""

    step_index: int
    role: str = Field(description="'system', 'user', 'assistant', or 'tool'.")
    content: str
    tokens: int = Field(default=0, description="Estimated or reported token count.")
    timestamp: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompressedHandoff(BaseModel):
    """Structured, compressed summary returned from a sub-agent to the supervisor."""

    agent_id: str
    status: str = Field(description="'SUCCESS', 'FAILED', or 'BLOCKED'")
    final_output: str
    tokens_discarded: int = Field(description="Intermediate exploration tokens shielded from parent.")
    total_steps: int
    artifacts: list[str] = Field(default_factory=list, description="File paths or artifacts produced.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextPartitioner:
    """Isolates sub-agent message history to prevent context window bloat and rule fatigue."""

    def __init__(self, agent_id: str, storage_dir: str | Path | None = None) -> None:
        self.agent_id = agent_id
        self.storage_dir = Path(storage_dir or f".airframe/transcripts/{agent_id}")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_file = self.storage_dir / "transcript.jsonl"
        self.corrupt_tail_file = self.storage_dir / "transcript.jsonl.corrupt_tail"
        self._step_counter = 0
        self._total_tokens_consumed = 0

    def log_turn(
        self,
        role: str,
        content: str,
        tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> TranscriptEntry:
        """Append an action or message to the agent's private transcript file with immediate fsync."""
        self._step_counter += 1
        self._total_tokens_consumed += tokens

        entry = TranscriptEntry(
            step_index=self._step_counter,
            role=role,
            content=content,
            tokens=tokens,
            metadata=metadata or {},
        )

        with self.transcript_file.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

        return entry

    def read_transcript(self, tolerate_corrupt_tail: bool = True) -> list[TranscriptEntry]:
        """Read all historical turns from disk, safely quarantining a partial crash-truncated tail."""
        if not self.transcript_file.is_file():
            return []

        raw_lines = [
            line.strip()
            for line in self.transcript_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not raw_lines:
            return []

        entries: list[TranscriptEntry] = []
        valid_lines: list[str] = []
        tail_quarantined = False

        for idx, line in enumerate(raw_lines):
            is_last = idx == len(raw_lines) - 1
            try:
                entries.append(TranscriptEntry.model_validate_json(line))
                valid_lines.append(line)
            except Exception as parse_err:
                if tolerate_corrupt_tail and is_last:
                    # Quarantine truncated/incomplete final line and repair file cleanly
                    atomic_write_text(self.corrupt_tail_file, line + "\n", make_backup=False)
                    tail_quarantined = True
                else:
                    raise CorruptedTranscriptError(
                        f"Corrupted transcript entry at line {idx + 1} in '{self.transcript_file}': {parse_err}"
                    ) from parse_err

        if tail_quarantined:
            repaired_content = ("\n".join(valid_lines) + "\n") if valid_lines else ""
            atomic_write_text(self.transcript_file, repaired_content, make_backup=False)

        return entries

    def compress_handoff(
        self,
        final_output: str,
        status: str = "SUCCESS",
        artifacts: list[str] | None = None,
        final_output_tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> CompressedHandoff:
        """Generate a lean handoff packet, discarding all intermediate exploration tokens.

        This ensures the supervisor context remains pristine and immune to context rot.
        """
        discarded = max(0, self._total_tokens_consumed - final_output_tokens)

        self.log_turn(
            role="assistant",
            content=final_output,
            tokens=final_output_tokens,
            metadata={"type": "final_handoff", "status": status},
        )

        return CompressedHandoff(
            agent_id=self.agent_id,
            status=status,
            final_output=final_output,
            tokens_discarded=discarded,
            total_steps=self._step_counter,
            artifacts=artifacts or [],
            metadata=metadata or {},
        )
