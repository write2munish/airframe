"""Deterministic sensors evaluating tool call order, geospatial precision, payload pruning, and zero-hallucination."""

from __future__ import annotations

from typing import Any

from airframe.sensors.base import Sensor, SensorResult


class ToolSequenceSensor(Sensor):
    """Tier 2 Sensor: asserts tool calls occurred in exact expected sequence."""

    def __init__(self, expected_sequence: list[str]) -> None:
        self.expected_sequence = expected_sequence

    @property
    def name(self) -> str:
        return "tool_sequence_validator"

    @property
    def tier(self) -> int:
        return 2

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        context = context or {}
        observed_sequence = candidate if isinstance(candidate, list) else context.get("tool_sequence", [])

        if observed_sequence == self.expected_sequence:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message=f"Tool sequence verified: {' -> '.join(observed_sequence)}",
                diagnostics={"observed": observed_sequence, "expected": self.expected_sequence},
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=False,
            message=f"Incorrect tool sequence. Expected {self.expected_sequence}, but observed {observed_sequence}",
            diagnostics={"observed": observed_sequence, "expected": self.expected_sequence},
        )


class CoordinateAccuracySensor(Sensor):
    """Tier 1 Sensor: asserts geocoded coordinates match target within geospatial tolerance."""

    def __init__(
        self,
        expected_lat: float,
        expected_lon: float,
        expected_hour: int,
        tolerance: float = 0.1,
    ) -> None:
        self.expected_lat = expected_lat
        self.expected_lon = expected_lon
        self.expected_hour = expected_hour
        self.tolerance = tolerance

    @property
    def name(self) -> str:
        return "coordinate_accuracy_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        data = candidate.model_dump() if hasattr(candidate, "model_dump") else (candidate if isinstance(candidate, dict) else {})

        lat = float(data.get("latitude", 0.0))
        lon = float(data.get("longitude", 0.0))
        hour = int(data.get("target_hour", -1))

        lat_diff = abs(lat - self.expected_lat)
        lon_diff = abs(lon - self.expected_lon)

        errors = []
        if lat_diff > self.tolerance:
            errors.append(f"Latitude {lat} diverged by {lat_diff:.4f} > {self.tolerance} from {self.expected_lat}")
        if lon_diff > self.tolerance:
            errors.append(f"Longitude {lon} diverged by {lon_diff:.4f} > {self.tolerance} from {self.expected_lon}")
        if hour != self.expected_hour:
            errors.append(f"Target hour {hour} != expected {self.expected_hour}")

        if errors:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Geospatial/Temporal parameter inaccuracy: " + "; ".join(errors),
                diagnostics={"lat_diff": lat_diff, "lon_diff": lon_diff, "hour": hour},
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message=f"Geospatial parameters verified within {self.tolerance}°: ({lat}, {lon}) at hour {hour}:00",
            diagnostics={"latitude": lat, "longitude": lon, "hour": hour},
        )


class PayloadPruningSensor(Sensor):
    """Tier 1 Sensor: enforces that tool responses were pruned and did not dump raw 168h arrays."""

    def __init__(self, max_tokens: int = 300) -> None:
        self.max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "payload_pruning_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        context = context or {}
        estimated_tokens = context.get("tool_tokens") or (len(str(candidate)) // 4)

        if isinstance(candidate, dict):
            if "hourly" in candidate or (isinstance(candidate.get("time"), list) and len(candidate.get("time")) > 24):
                return SensorResult(
                    sensor_name=self.name,
                    tier=self.tier,
                    passed=False,
                    message="Unpruned 168-hour array detected in tool response! Context window would be bloated.",
                    diagnostics={"unpruned_detected": True},
                )

        if estimated_tokens > self.max_tokens:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Tool response size ({estimated_tokens} tokens) exceeded maximum pruned ceiling ({self.max_tokens} tokens).",
                diagnostics={"tokens": estimated_tokens, "max_tokens": self.max_tokens},
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message=f"Tool response successfully pruned ({estimated_tokens} tokens < {self.max_tokens} ceiling).",
            diagnostics={"tokens": estimated_tokens},
        )


class ZeroHallucinationWeatherSensor(Sensor):
    """Tier 1 Sensor: enforces that final reported weather matches tool truth with zero hallucination."""

    @property
    def name(self) -> str:
        return "zero_hallucination_weather_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        context = context or {}
        tool_data = context.get("tool_truth", {})

        if not tool_data:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Missing 'tool_truth' in evaluation context.",
            )

        report = candidate.model_dump() if hasattr(candidate, "model_dump") else candidate

        reported_temp = float(report.get("temperature_celsius", 0))
        reported_precip = float(report.get("precipitation_mm", 0))
        reported_prob = int(report.get("precipitation_probability_pct", 0))
        reported_rain = bool(report.get("will_it_rain", False))

        truth_temp = float(tool_data.get("temperature_celsius", 0))
        truth_precip = float(tool_data.get("precipitation_mm", 0))
        truth_prob = int(tool_data.get("precipitation_probability_pct", 0))
        truth_rain = bool(tool_data.get("will_it_rain", False))

        discrepancies = []
        if abs(reported_temp - truth_temp) > 0.1:
            discrepancies.append(f"Temperature: reported {reported_temp}°C vs truth {truth_temp}°C")
        if abs(reported_precip - truth_precip) > 0.05:
            discrepancies.append(f"Precipitation: reported {reported_precip}mm vs truth {truth_precip}mm")
        if reported_prob != truth_prob:
            discrepancies.append(f"Precipitation Probability: reported {reported_prob}% vs truth {truth_prob}%")
        if reported_rain != truth_rain:
            discrepancies.append(f"Rain Decision: reported {reported_rain} vs truth {truth_rain}")

        if discrepancies:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Hallucination detected in final report! Discrepancies: " + "; ".join(discrepancies),
                diagnostics={"discrepancies": discrepancies},
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message="Zero hallucination verified: reported weather matches real tool data exactly.",
            diagnostics={"verified": True},
        )
