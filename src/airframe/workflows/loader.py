"""Workflow pack loader, manifest parser, and scaffolding generator."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from airframe.blueprints.schema import AgentBlueprint


class GovernanceSpec(BaseModel):
    """Runtime governance limits specified in workflow manifest."""

    max_turns: int = Field(default=20)
    timeout_seconds: int = Field(default=600)
    max_budget_usd: float | None = Field(default=None)
    stall_threshold: int = Field(default=3)


class WorkflowManifest(BaseModel):
    """The master manifest specification for a workflow (workflow.yaml)."""

    id: str = Field(description="Unique workflow identifier (e.g., 'pr-code-reviewer').")
    version: str = Field(default="1.0.0")
    description: str = Field(default="")
    topology: Literal["single-agent", "orchestrator-workers", "evaluator-optimizer"] = Field(
        default="single-agent"
    )
    workspace_mode: Literal["inherit", "branch", "sandbox"] = Field(default="inherit")
    governance: GovernanceSpec = Field(default_factory=GovernanceSpec)
    entrypoint_agent: str = Field(default="supervisor")
    agents: list[str] = Field(default_factory=list, description="Relative paths to agent blueprint YAMLs.")
    testbed: str | None = Field(default="testbed/smoke.yaml", description="Relative path to testbed dataset.")


class WorkflowPack:
    """Represents a validated, self-contained workflow pack on disk."""

    def __init__(self, root_dir: Path, manifest: WorkflowManifest) -> None:
        self.root_dir = root_dir
        self.manifest = manifest

    @property
    def id(self) -> str:
        return self.manifest.id

    def get_guide_path(self, filename: str = "task_spec.md") -> Path:
        """Resolve path to a guide document in guides/."""
        return self.root_dir / "guides" / filename

    def get_testbed_path(self) -> Path | None:
        """Resolve path to the testbed file."""
        if not self.manifest.testbed:
            return None
        path = self.root_dir / self.manifest.testbed
        return path if path.is_file() else None

    def load_blueprints(self) -> list[AgentBlueprint]:
        """Load and parse all agent blueprints referenced in the manifest."""
        blueprints: list[AgentBlueprint] = []
        for rel_path in self.manifest.agents:
            full_path = (self.root_dir / rel_path).resolve()
            if full_path.is_file():
                blueprints.append(AgentBlueprint.load_file(full_path))
        return blueprints


def load_workflow(workflow_dir: str | Path) -> WorkflowPack:
    """Load and validate a workflow pack from a directory containing workflow.yaml."""
    path = Path(workflow_dir).resolve()
    manifest_file = path / "workflow.yaml"

    if not manifest_file.is_file():
        raise FileNotFoundError(
            f"Invalid workflow pack: 'workflow.yaml' not found in '{path}'"
        )

    content = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    manifest = WorkflowManifest.model_validate(content)
    return WorkflowPack(root_dir=path, manifest=manifest)


def init_workflow_scaffold(target_dir: str | Path, name: str) -> Path:
    """Scaffold a new self-contained Workflow Pack directory structure."""
    root = Path(target_dir).resolve() / name
    if root.exists():
        raise FileExistsError(f"Target workflow directory already exists: '{root}'")

    # Create directory tree
    (root / "blueprints").mkdir(parents=True)
    (root / "guides").mkdir(parents=True)
    (root / "guards").mkdir(parents=True)
    (root / "sensors").mkdir(parents=True)
    (root / "tools").mkdir(parents=True)
    (root / "testbed").mkdir(parents=True)

    # 1. workflow.yaml
    manifest = {
        "id": name,
        "version": "1.0.0",
        "description": f"Workflow pack for {name}",
        "topology": "single-agent",
        "workspace_mode": "inherit",
        "entrypoint_agent": "worker",
        "agents": ["blueprints/worker.yaml"],
        "testbed": "testbed/smoke.yaml",
    }
    (root / "workflow.yaml").write_text(yaml.dump(manifest, sort_keys=False), encoding="utf-8")

    # 2. blueprints/worker.yaml
    worker_bp = {
        "name": "worker",
        "role": f"{name.capitalize()} Specialist",
        "system_prompt": "guides/task_spec.md",
        "engine_binding": "gemini-2.0-flash",
        "allowed_tools": [],
        "workspace_mode": "inherit",
    }
    (root / "blueprints" / "worker.yaml").write_text(yaml.dump(worker_bp, sort_keys=False), encoding="utf-8")

    # 3. guides/task_spec.md
    (root / "guides" / "task_spec.md").write_text(
        f"# Task Specification: {name}\n\nDefine task rules and canonical instructions here.\n",
        encoding="utf-8",
    )

    # 4. guards/contracts.py
    (root / "guards" / "contracts.py").write_text(
        "from pydantic import BaseModel\n\n\nclass OutputContract(BaseModel):\n    summary: str\n    status: str = 'SUCCESS'\n",
        encoding="utf-8",
    )

    # 5. testbed/smoke.yaml
    smoke_cases = [
        {"id": "smoke-01", "description": "Baseline sanity check", "input": {"query": "hello"}}
    ]
    (root / "testbed" / "smoke.yaml").write_text(yaml.dump(smoke_cases, sort_keys=False), encoding="utf-8")

    return root
