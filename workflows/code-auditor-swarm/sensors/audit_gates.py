"""Tier 1 deterministic completeness gate for code-auditor-swarm."""

from __future__ import annotations

from typing import Any

from airframe.sensors.base import Sensor, SensorResult


class AuditCompletenessSensor(Sensor):
    """Tier 1 Sensor: verifies SwarmAuditReport contains a valid risk level and summary."""

    @property
    def name(self) -> str:
        return "audit_completeness_gate"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        data = (
            candidate.model_dump()
            if hasattr(candidate, "model_dump")
            else (candidate if isinstance(candidate, dict) else {})
        )
        summary = str(data.get("summary", "")).strip()
        risk_level = data.get("risk_level")
        findings_count = data.get("findings_count")

        errors: list[str] = []
        if not summary:
            errors.append("summary must be non-empty")
        if risk_level not in ("LOW", "MEDIUM", "HIGH"):
            errors.append(f"invalid risk_level={risk_level!r}")
        if not isinstance(findings_count, int) or findings_count < 0:
            errors.append(f"findings_count must be >= 0 (got {findings_count!r})")

        if errors:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Audit report failed completeness check: " + "; ".join(errors),
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message=f"Audit report complete (risk_level={risk_level}, findings={findings_count}).",
        )
