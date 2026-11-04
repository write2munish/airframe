"""Base definitions and pipeline orchestrator for Airframe sensors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class SensorResult(BaseModel):
    """Result emitted by a sensor evaluation."""

    sensor_name: str
    tier: int = Field(description="Tier 1 (Deterministic), Tier 2 (Semantic), or Tier 3 (Judge).")
    passed: bool
    message: str
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class Sensor(ABC):
    """Abstract base class for all Airframe sensors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique sensor identifier."""
        pass

    @property
    @abstractmethod
    def tier(self) -> int:
        """Sensor tier: 1 (Deterministic), 2 (Semantic), or 3 (Judge)."""
        pass

    @abstractmethod
    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        """Evaluate candidate output and return SensorResult."""
        pass


class SensorPipeline:
    """Orchestrates multi-tier sensor evaluations with fast-fail short-circuiting."""

    def __init__(self, sensors: list[Sensor] | None = None) -> None:
        self.sensors: list[Sensor] = sensors or []

    def add_sensor(self, sensor: Sensor) -> None:
        """Register a sensor in the pipeline."""
        self.sensors.append(sensor)

    async def run(
        self,
        candidate: Any,
        context: dict[str, Any] | None = None,
        short_circuit: bool = True,
    ) -> list[SensorResult]:
        """Execute sensors in strict tier order: Tier 1 -> Tier 2 -> Tier 3.

        Args:
            candidate: Output object, string, or execution trace to evaluate.
            context: Additional contextual data (e.g. golden outputs, testbed params).
            short_circuit: If True, aborts immediately if any Tier 1 (deterministic)
                           sensor fails, preventing costly Tier 3 LLM judge invocations.

        Returns:
            List of SensorResults collected during evaluation.
        """
        # Sort sensors by tier ascending (1 -> 2 -> 3)
        sorted_sensors = sorted(self.sensors, key=lambda s: s.tier)
        results: list[SensorResult] = []

        current_tier = 1
        tier1_failed = False

        for sensor in sorted_sensors:
            if short_circuit and tier1_failed and sensor.tier > 1:
                # Skip higher-tier sensors if deterministic baseline failed
                results.append(
                    SensorResult(
                        sensor_name=sensor.name,
                        tier=sensor.tier,
                        passed=False,
                        message=f"Skipped due to prior Tier 1 failure in '{results[0].sensor_name}'.",
                        diagnostics={"skipped": True},
                    )
                )
                continue

            result = await sensor.evaluate(candidate, context)
            results.append(result)

            if sensor.tier == 1 and not result.passed:
                tier1_failed = True

        return results
