"""Dynamic Agent Registry for storing, retrieving, and discovering blueprints."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from airframe.blueprints.schema import AgentBlueprint


class BlueprintNotFoundError(KeyError):
    """Raised when an agent blueprint cannot be resolved in the registry."""
    pass


class AgentRegistry:
    """In-memory and file-backed catalog of AgentBlueprints."""

    def __init__(self) -> None:
        self._blueprints: dict[str, AgentBlueprint] = {}

    def register(self, blueprint: AgentBlueprint, overwrite: bool = False) -> None:
        """Register an AgentBlueprint."""
        if blueprint.name in self._blueprints and not overwrite:
            raise ValueError(
                f"Blueprint '{blueprint.name}' is already registered. Set overwrite=True to replace."
            )
        self._blueprints[blueprint.name] = blueprint

    def get(self, name: str) -> AgentBlueprint:
        """Retrieve a registered AgentBlueprint by name."""
        if name not in self._blueprints:
            raise BlueprintNotFoundError(
                f"Agent blueprint '{name}' not found. Registered: {list(self._blueprints.keys())}"
            )
        return self._blueprints[name]

    def list_all(self) -> list[AgentBlueprint]:
        """List all registered blueprints."""
        return list(self._blueprints.values())

    def __contains__(self, name: str) -> bool:
        return name in self._blueprints

    def __iter__(self) -> Iterator[AgentBlueprint]:
        return iter(self._blueprints.values())

    def load_directory(self, dir_path: str | Path, recursive: bool = True) -> int:
        """Discover and register all YAML blueprint manifests in a directory.

        Args:
            dir_path: Target directory containing .yaml or .yml files.
            recursive: Whether to search subdirectories.

        Returns:
            Number of newly loaded blueprints.
        """
        path = Path(dir_path)
        if not path.is_dir():
            raise NotADirectoryError(f"Directory not found: '{dir_path}'")

        pattern = "**/*.y*ml" if recursive else "*.y*ml"
        loaded_count = 0

        for file_path in path.glob(pattern):
            if file_path.is_file():
                try:
                    blueprint = AgentBlueprint.load_file(file_path)
                    self.register(blueprint, overwrite=True)
                    loaded_count += 1
                except Exception:
                    # Skip non-blueprint YAML files
                    continue

        return loaded_count
