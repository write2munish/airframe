"""Read-only codebase inspection tools backed by the auth0/java-jwt repository index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_INDEX_PATH = Path(__file__).resolve().parent / "repo_index.json"


def _load_index() -> dict[str, dict[str, str]]:
    data = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    return dict(data.get("files", {}))


def list_subsystems() -> dict[str, int]:
    """Return each auth0/java-jwt subsystem name mapped to its source file count."""
    counts: dict[str, int] = {}
    for meta in _load_index().values():
        sub = meta["subsystem"]
        counts[sub] = counts.get(sub, 0) + 1
    return counts


def inspect_java_jwt_package(subsystem: str) -> list[dict[str, Any]]:
    """List exact Java file paths and class responsibilities for a given auth0/java-jwt subsystem."""
    entries: list[dict[str, Any]] = []
    for rel_path, meta in _load_index().items():
        if meta["subsystem"] == subsystem:
            entries.append({"path": rel_path, "role": meta["role"]})
    if not entries:
        valid = sorted(list_subsystems().keys())
        return [{"error": f"Unknown subsystem '{subsystem}'. Valid subsystems: {valid}"}]
    return entries
