"""Tier 2: Semantic, functional, and tool-invocation validation sensors."""

from __future__ import annotations

import re
from typing import Any

from airframe.sensors.base import Sensor, SensorResult


class ToolCallValidatorSensor(Sensor):
    """Validates that a tool call was chosen from allowed tools with required arguments."""

    def __init__(
        self,
        name: str = "tool_call_validator",
        allowed_tools: list[str] | None = None,
        required_params: dict[str, list[str]] | None = None,
    ) -> None:
        self._name = name
        self.allowed_tools = allowed_tools or []
        self.required_params = required_params or {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 2

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        # Candidate should be a dict representing a tool call: {"tool_name": str, "arguments": dict}
        if not isinstance(candidate, dict):
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Candidate tool call must be a dict, got {type(candidate).__name__}.",
            )

        tool_name = candidate.get("tool_name") or candidate.get("name")
        arguments = candidate.get("arguments") or candidate.get("args") or {}

        if not tool_name:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Tool call missing 'tool_name' or 'name' field.",
            )

        if self.allowed_tools and tool_name not in self.allowed_tools:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Disallowed tool '{tool_name}' invoked. Allowed: {self.allowed_tools}",
                diagnostics={"tool_name": tool_name, "allowed_tools": self.allowed_tools},
            )

        # Check required params
        if tool_name in self.required_params:
            missing_args = [
                arg for arg in self.required_params[tool_name]
                if arg not in arguments
            ]
            if missing_args:
                return SensorResult(
                    sensor_name=self.name,
                    tier=self.tier,
                    passed=False,
                    message=f"Tool '{tool_name}' missing required arguments: {missing_args}",
                    diagnostics={"tool_name": tool_name, "missing_args": missing_args},
                )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message=f"Tool call '{tool_name}' successfully validated.",
            diagnostics={"tool_name": tool_name, "arguments": arguments},
        )


class TokenOverlapSensor(Sensor):
    """Computes token Jaccard similarity against a golden reference string."""

    def __init__(
        self,
        name: str = "token_overlap_sensor",
        min_similarity: float = 0.5,
    ) -> None:
        self._name = name
        self.min_similarity = min_similarity

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 2

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        context = context or {}
        reference = context.get("reference") or context.get("golden")
        if not reference:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Missing 'reference' or 'golden' in evaluation context for token overlap.",
            )

        candidate_tokens = self._tokenize(str(candidate))
        reference_tokens = self._tokenize(str(reference))

        if not reference_tokens and not candidate_tokens:
            similarity = 1.0
        elif not reference_tokens or not candidate_tokens:
            similarity = 0.0
        else:
            intersection = candidate_tokens.intersection(reference_tokens)
            union = candidate_tokens.union(reference_tokens)
            similarity = len(intersection) / len(union)

        passed = similarity >= self.min_similarity
        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=passed,
            message=(
                f"Similarity score {similarity:.2f} "
                f"({'passed' if passed else 'failed'} threshold {self.min_similarity:.2f})."
            ),
            diagnostics={"similarity": round(similarity, 3), "threshold": self.min_similarity},
        )
