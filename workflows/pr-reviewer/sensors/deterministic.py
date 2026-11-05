"""Deterministic verification sensors for PR code review reports."""

from airframe.sensors.base import Sensor, SensorResult


class BlockerConsistencySensor(Sensor):
    """Ensures report 'passed' boolean strictly matches absence of BLOCKER issues."""

    @property
    def name(self) -> str:
        return "blocker_consistency"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate, context=None) -> SensorResult:
        # Candidate is expected to be a CodeReviewReport or dict
        if hasattr(candidate, "issues"):
            issues = candidate.issues
            passed = candidate.passed
        elif isinstance(candidate, dict):
            issues = candidate.get("issues", [])
            passed = candidate.get("passed", True)
        else:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Candidate cannot be inspected for review issues.",
            )

        has_blockers = any(
            (getattr(i, "severity", None) or i.get("severity")) == "BLOCKER"
            for i in issues
        )

        if has_blockers and passed:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Inconsistent report: report marked passed=True but BLOCKER issues exist.",
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message="Report passed status is strictly consistent with issue severities.",
        )
