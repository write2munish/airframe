"""Workflow pack loader, manifest parser, and scaffolding generator."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from airframe.blueprints.schema import AgentBlueprint
from airframe.governance.circuit_breakers import Budget

# Alias for backward compatibility
GovernanceSpec = Budget


class WorkflowManifest(BaseModel):
    """The master manifest specification for a workflow (workflow.yaml).

    Supports both lightweight single-file declarations (with inline prompt and tools)
    and full multi-agent directory packs.
    """

    id: str = Field(description="Unique workflow identifier (e.g., 'pr-code-reviewer').")
    version: str = Field(default="1.0.0")
    description: str = Field(default="")
    topology: Literal["single-agent", "orchestrator-workers", "evaluator-optimizer"] = Field(
        default="single-agent"
    )
    workspace_mode: Literal["inherit", "branch", "sandbox"] = Field(default="inherit")
    governance: Budget = Field(default_factory=Budget)
    entrypoint_agent: str = Field(default="worker")

    # Full multi-agent workflow fields
    agents: list[str] = Field(default_factory=list, description="Relative paths to agent blueprint YAMLs.")
    testbed: str | None = Field(default=None, description="Relative path to testbed dataset.")

    # Lightweight single-agent inline fields (avoids mandatory 6 subdirectories)
    system_prompt: str | None = Field(
        default=None, description="Inline system prompt instructions for single-agent workflows."
    )
    tools: list[str] = Field(
        default_factory=list, description="Whitelisted tools for single-agent inline workflows."
    )
    engine_binding: str = Field(
        default="gemini-2.0-flash", description="Model engine identifier for inline workflows."
    )
    schema_contract: str | None = Field(
        default=None, description="Optional inline Pydantic schema import path or description."
    )


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
            # Check default locations
            for candidate in ("testbed/smoke.yaml", "testbed.yaml", "smoke.yaml"):
                p = self.root_dir / candidate
                if p.is_file():
                    return p
            return None
        path = self.root_dir / self.manifest.testbed
        return path if path.is_file() else None

    def load_blueprints(self) -> list[AgentBlueprint]:
        """Load agent blueprints referenced in manifest, or synthesize one for lightweight packs."""
        blueprints: list[AgentBlueprint] = []
        for rel_path in self.manifest.agents:
            full_path = (self.root_dir / rel_path).resolve()
            if full_path.is_file():
                blueprints.append(AgentBlueprint.load_file(full_path))

        # If no blueprint files specified, synthesize a blueprint from inline manifest definition
        if not blueprints:
            prompt = self.manifest.system_prompt or (
                f"You are an autonomous agent executing the '{self.manifest.id}' workflow."
            )
            # Check if system_prompt points to a file
            if "\n" not in prompt and len(prompt) < 255:
                try:
                    candidate_file = self.root_dir / prompt
                    if candidate_file.is_file():
                        prompt = candidate_file.read_text(encoding="utf-8")
                except OSError:
                    pass

            synthetic_bp = AgentBlueprint(
                name=self.manifest.entrypoint_agent or "worker",
                role=self.manifest.description or f"{self.manifest.id} agent",
                description=self.manifest.description,
                system_prompt=prompt,
                engine_binding=self.manifest.engine_binding,
                allowed_tools=self.manifest.tools,
                workspace_mode=self.manifest.workspace_mode,
                budget=self.manifest.governance,
            )
            blueprints.append(synthetic_bp)

        return blueprints


def load_workflow(workflow_target: str | Path) -> WorkflowPack:
    """Load and validate a workflow pack from either a directory or a direct workflow.yaml file."""
    path = Path(workflow_target).resolve()

    if path.is_file() and path.suffix in (".yaml", ".yml"):
        manifest_file = path
        root_dir = path.parent
    elif path.is_dir():
        manifest_file = path / "workflow.yaml"
        root_dir = path
        if not manifest_file.is_file():
            # Check for alternative manifest naming
            candidates = list(path.glob("*workflow*.yaml")) + list(path.glob("*workflow*.yml"))
            if candidates:
                manifest_file = candidates[0]
            else:
                raise FileNotFoundError(
                    f"Invalid workflow pack: 'workflow.yaml' not found in '{path}'"
                )
    else:
        raise FileNotFoundError(f"Path does not exist: '{path}'")

    content = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    manifest = WorkflowManifest.model_validate(content)
    return WorkflowPack(root_dir=root_dir, manifest=manifest)


def init_workflow_scaffold(target_dir: str | Path, name: str, lightweight: bool = False) -> Path:
    """Scaffold a new self-contained Workflow Pack.

    Args:
        target_dir: Destination parent directory.
        name: Workflow identifier name.
        lightweight: When True, generates a minimal single-file structure (workflow.yaml + tools.py).
                     When False, generates full multi-agent subdirectories.
    """
    root = Path(target_dir).resolve() / name
    if root.exists():
        raise FileExistsError(f"Target workflow directory already exists: '{root}'")

    root.mkdir(parents=True)

    if lightweight:
        # Minimal single-file / lightweight scaffold (no mandatory 6 empty subdirs!)
        manifest = {
            "id": name,
            "version": "1.0.0",
            "description": f"Lightweight workflow for {name}",
            "topology": "single-agent",
            "system_prompt": f"You are a dedicated specialist handling {name}. Use the provided tools precisely.",
            "engine_binding": "gemini-2.0-flash",
            "tools": ["sample_tool"],
            "governance": {
                "max_turns": 10,
                "timeout_seconds": 300,
            },
            "testbed": "testbed.yaml",
        }
        (root / "workflow.yaml").write_text(yaml.dump(manifest, sort_keys=False), encoding="utf-8")

        tools_code = (
            '"""Custom tool implementations for this workflow."""\n\n'
            'def sample_tool(param: str) -> str:\n'
            '    """Sample tool demonstration."""\n'
            '    return f"Processed: {param}"\n'
        )
        (root / "tools.py").write_text(tools_code, encoding="utf-8")

        testbed_cases = [
            {"id": "smoke-01", "description": "Baseline check", "input": {"param": "test"}}
        ]
        (root / "testbed.yaml").write_text(yaml.dump(testbed_cases, sort_keys=False), encoding="utf-8")

    else:
        # Full multi-agent directory structure
        (root / "blueprints").mkdir(parents=True)
        (root / "guides").mkdir(parents=True)
        (root / "guards").mkdir(parents=True)
        (root / "sensors").mkdir(parents=True)
        (root / "tools").mkdir(parents=True)
        (root / "testbed").mkdir(parents=True)

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

        worker_bp = {
            "name": "worker",
            "role": f"{name.capitalize()} Specialist",
            "system_prompt": "guides/task_spec.md",
            "engine_binding": "gemini-2.0-flash",
            "allowed_tools": [],
            "workspace_mode": "inherit",
        }
        (root / "blueprints" / "worker.yaml").write_text(yaml.dump(worker_bp, sort_keys=False), encoding="utf-8")

        (root / "guides" / "task_spec.md").write_text(
            f"# Task Specification: {name}\n\nDefine task rules and canonical instructions here.\n",
            encoding="utf-8",
        )

        (root / "guards" / "contracts.py").write_text(
            "from pydantic import BaseModel\n\n\nclass OutputContract(BaseModel):\n    summary: str\n    status: str = 'SUCCESS'\n",
            encoding="utf-8",
        )

        smoke_cases = [
            {"id": "smoke-01", "description": "Baseline sanity check", "input": {"query": "hello"}}
        ]
        (root / "testbed" / "smoke.yaml").write_text(yaml.dump(smoke_cases, sort_keys=False), encoding="utf-8")

    return root
