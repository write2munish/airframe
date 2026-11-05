"""Unit tests for AgentBlueprints and dynamic registry."""

from pathlib import Path
import pytest
import yaml

from airframe.blueprints import (
    AgentBlueprint,
    AgentBudget,
    AgentRegistry,
    BlueprintNotFoundError,
)


def test_agent_blueprint_creation():
    """Test creating an AgentBlueprint with defaults and custom attributes."""
    bp = AgentBlueprint(
        name="code-auditor",
        role="Security Engineer",
        system_prompt="Audit code for security vulnerabilities.",
        engine_binding="claude-3-5-sonnet",
        allowed_tools=["read_file", "search_code"],
        workspace_mode="branch",
        budget=AgentBudget(max_tokens=50_000, max_turns=10),
    )

    assert bp.name == "code-auditor"
    assert bp.role == "Security Engineer"
    assert bp.engine_binding == "claude-3-5-sonnet"
    assert bp.workspace_mode == "branch"
    assert bp.budget.max_tokens == 50_000
    assert bp.budget.max_turns == 10
    assert "read_file" in bp.allowed_tools


def test_blueprint_yaml_roundtrip(tmp_path: Path):
    """Test serializing to and loading from YAML files."""
    bp = AgentBlueprint(
        name="researcher",
        role="Academic Researcher",
        system_prompt="Extract relevant papers.",
        engine_binding="gemini-2.0-flash",
        allowed_tools=["web_search"],
    )

    yaml_str = bp.to_yaml()
    loaded_from_str = AgentBlueprint.from_yaml(yaml_str)
    assert loaded_from_str.name == bp.name
    assert loaded_from_str.role == bp.role

    file_path = tmp_path / "researcher.yaml"
    file_path.write_text(yaml_str, encoding="utf-8")

    loaded_from_file = AgentBlueprint.load_file(file_path)
    assert loaded_from_file.name == bp.name
    assert loaded_from_file.allowed_tools == ["web_search"]


def test_agent_registry_operations(tmp_path: Path):
    """Test registry storage, duplicate handling, and directory discovery."""
    registry = AgentRegistry()

    bp1 = AgentBlueprint(name="agent-1", role="Worker 1", system_prompt="Do task 1")
    bp2 = AgentBlueprint(name="agent-2", role="Worker 2", system_prompt="Do task 2")

    registry.register(bp1)
    registry.register(bp2)

    assert len(registry.list_all()) == 2
    assert registry.get("agent-1").role == "Worker 1"
    assert "agent-2" in registry

    # Disallow duplicates without overwrite=True
    with pytest.raises(ValueError, match="already registered"):
        registry.register(bp1)

    # Allow with overwrite=True
    bp1_modified = AgentBlueprint(name="agent-1", role="Senior Worker 1", system_prompt="Do task 1")
    registry.register(bp1_modified, overwrite=True)
    assert registry.get("agent-1").role == "Senior Worker 1"

    # Not found error
    with pytest.raises(BlueprintNotFoundError):
        registry.get("non-existent-agent")

    # Directory auto-discovery
    dir_path = tmp_path / "blueprints"
    dir_path.mkdir()
    bp3 = AgentBlueprint(name="agent-3", role="Worker 3", system_prompt="Do task 3")
    (dir_path / "agent3.yaml").write_text(bp3.to_yaml(), encoding="utf-8")

    count = registry.load_directory(dir_path)
    assert count == 1
    assert "agent-3" in registry
