"""Schema sensor for deterministic Pydantic contract validation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from airframe.adapters.base import extract_json_payload
from airframe.sensors.base import Sensor, SensorResult

if TYPE_CHECKING:
    from airframe.guards.executable import PayloadBounds

T = TypeVar("T", bound=BaseModel)


class SchemaSensor(Sensor, Generic[T]):
    """Tier 1 Deterministic Sensor validating candidate output against a Pydantic schema contract."""

    def __init__(self, schema: type[T], bounds: Any | None = None) -> None:
        self.schema = schema
        self.bounds = bounds

    @property
    def name(self) -> str:
        return f"SchemaSensor[{self.schema.__name__}]"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        if isinstance(candidate, self.schema):
            return SensorResult(
                sensor_name=self.name,
                tier=1,
                passed=True,
                message=f"Valid instance of {self.schema.__name__}",
                diagnostics={"data": candidate.model_dump()},
            )

        raw_text = str(candidate)

        # 1. Bounds check if configured
        if self.bounds is not None:
            from airframe.guards.executable import ExecutableGuard, GuardViolationError
            guard = ExecutableGuard(schema=self.schema, bounds=self.bounds)
            try:
                guard.check_bounds(raw_text)
            except GuardViolationError as err:
                return SensorResult(
                    sensor_name=self.name,
                    tier=1,
                    passed=False,
                    message=f"Payload bounds violation: {err.message}",
                    diagnostics=err.details,
                )

        # 2. Extract and validate JSON
        clean_json = extract_json_payload(raw_text)
        try:
            validated = self.schema.model_validate_json(clean_json)
            return SensorResult(
                sensor_name=self.name,
                tier=1,
                passed=True,
                message=f"Output conforms to {self.schema.__name__}",
                diagnostics={"data": validated.model_dump()},
            )
        except (ValidationError, json.JSONDecodeError) as err:
            return SensorResult(
                sensor_name=self.name,
                tier=1,
                passed=False,
                message=f"Schema validation failed for '{self.schema.__name__}': {err}",
                diagnostics={"error": str(err), "clean_json": clean_json},
            )
