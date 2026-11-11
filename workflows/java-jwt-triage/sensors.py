"""Tier 1 deterministic sensor verifying predicted Java file paths exist in auth0/java-jwt."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from airframe.sensors.base import Sensor, SensorResult

_INDEX_PATH = Path(__file__).resolve().parent / "repo_index.json"


class RealJavaJwtPathSensor(Sensor):
    """Tier 1 Deterministic Sensor: rejects hallucinated file paths and subsystem mismatches."""

    def __init__(self, index_path: Path | None = None) -> None:
        target = index_path or _INDEX_PATH
        raw = json.loads(target.read_text(encoding="utf-8"))
        self._files: dict[str, dict[str, str]] = dict(raw.get("files", {}))

    @property
    def name(self) -> str:
        return "real_java_jwt_path_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        if hasattr(candidate, "model_dump"):
            data = candidate.model_dump()
        elif isinstance(candidate, dict):
            data = candidate
        else:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Candidate payload must be a BugTriageReport model or dict.",
            )

        suspected = str(data.get("suspected_file", "")).strip()
        subsystem = str(data.get("subsystem", "")).strip()

        if suspected not in self._files:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Hallucinated or invalid repository path: '{suspected}' not in auth0/java-jwt.",
                diagnostics={"suspected_file": suspected},
            )

        actual_subsystem = self._files[suspected]["subsystem"]
        if subsystem != actual_subsystem:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=(
                    f"Subsystem mismatch: '{suspected}' belongs to '{actual_subsystem}', "
                    f"not '{subsystem}'."
                ),
                diagnostics={"suspected_file": suspected, "actual_subsystem": actual_subsystem},
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message=f"Verified real auth0/java-jwt path '{suspected}' in subsystem '{subsystem}'.",
            diagnostics={"suspected_file": suspected, "subsystem": subsystem},
        )
