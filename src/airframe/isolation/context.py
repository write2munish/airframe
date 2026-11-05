"""Context Window Partitioner: manages private transcripts and token-discarding handoffs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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
        self._step_counter = 0
        self._total_tokens_consumed = 0

    def log_turn(
        self,
        role: str,
        content: str,
        tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> TranscriptEntry:
        """Append an action or message to the agent's private transcript file."""
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

        return entry

    def read_transcript(self) -> list[TranscriptEntry]:
        """Read all historical turns from disk."""
        if not self.transcript_file.is_file():
            return []

        entries: list[TranscriptEntry] = []
        with self.transcript_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(TranscriptEntry.model_validate_json(line))
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
        # Calculate tokens discarded = total tokens consumed minus the finalized payload
        discarded = max(0, self._total_tokens_consumed - final_output_tokens)

        # Log completion in private transcript
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
