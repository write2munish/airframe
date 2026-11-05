"""Declarative Agent Blueprint schema and budget definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

WorkspaceMode = Literal["inherit", "branch", "sandbox"]


class AgentBudget(BaseModel):
    """Resource quotas and circuit breakers for an agent instance."""

    max_tokens: int = Field(default=150_000, description="Maximum total tokens allowed before halting.")
    max_turns: int = Field(default=25, description="Maximum turn iterations allowed.")
    timeout_seconds: int = Field(default=600, description="Max wall-clock seconds before timing out.")
    max_cost_usd: float | None = Field(default=None, description="Maximum spend ceiling in USD.")


class AgentBlueprint(BaseModel):
    """Declarative specification for an AI agent worker or supervisor."""

    name: str = Field(description="Unique agent blueprint name (e.g., 'code-reviewer', 'researcher').")
    role: str = Field(description="Role description (e.g., 'Senior Staff Security Auditor').")
    description: str = Field(default="", description="High-level summary of what this agent does.")
    system_prompt: str = Field(description="Domain-specific steering instructions or file path.")
    engine_binding: str = Field(default="gemini-2.0-flash", description="Underlying model engine identifier.")
    allowed_tools: list[str] = Field(
        default_factory=list, description="Whitelisted tools this agent is permitted to execute."
    )
    workspace_mode: WorkspaceMode = Field(
        default="inherit", description="Workspace isolation: 'inherit' (shared), 'branch' (git worktree), 'sandbox' (tempdir)."
    )
    budget: AgentBudget = Field(default_factory=AgentBudget, description="Resource limits.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Custom metadata tags.")

    @classmethod
    def from_yaml(cls, yaml_content: str) -> AgentBlueprint:
        """Parse an AgentBlueprint from a YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls.model_validate(data)

    @classmethod
    def load_file(cls, file_path: str | Path) -> AgentBlueprint:
        """Load an AgentBlueprint from a YAML file on disk."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Agent blueprint file not found: '{file_path}'")
        content = path.read_text(encoding="utf-8")
        return cls.from_yaml(content)

    def to_yaml(self) -> str:
        """Serialize blueprint to a clean YAML string."""
        return yaml.dump(self.model_dump(), sort_keys=False)
