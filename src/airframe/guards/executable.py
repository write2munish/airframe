"""Executable guards for programmatic boundary and contract enforcement."""

from __future__ import annotations

import re
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, ValidationError

from airframe.adapters.base import extract_json_payload

T = TypeVar("T", bound=BaseModel)


class GuardViolationError(ValueError):
    """Raised when an executable guard boundary is breached."""

    def __init__(self, guard_name: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{guard_name}] {message}")
        self.guard_name = guard_name
        self.message = message
        self.details = details or {}


class PayloadBounds(BaseModel):
    """Hard physical limits on model output payload."""

    max_bytes: int | None = Field(default=None, description="Max byte size of output.")
    max_lines: int | None = Field(default=None, description="Max line count of output.")
    forbidden_substrings: list[str] = Field(
        default_factory=list, description="Substrings strictly banned from the output."
    )
    forbidden_patterns: list[str] = Field(
        default_factory=list, description="Regex patterns strictly banned from the output."
    )


class ExecutableGuard(Generic[T]):
    """Enforces programmatic boundaries and Pydantic schema validation on raw model output."""

    def __init__(self, schema: type[T], bounds: PayloadBounds | None = None) -> None:
        self.schema = schema
        self.bounds = bounds or PayloadBounds()

    def check_bounds(self, raw_text: str) -> None:
        """Validate raw text against physical payload bounds."""
        # 1. Byte size check
        byte_len = len(raw_text.encode("utf-8"))
        if self.bounds.max_bytes is not None and byte_len > self.bounds.max_bytes:
            raise GuardViolationError(
                guard_name="PayloadBounds.MaxBytes",
                message=f"Payload size {byte_len}B exceeds ceiling of {self.bounds.max_bytes}B.",
                details={"actual_bytes": byte_len, "max_bytes": self.bounds.max_bytes},
            )

        # 2. Line count check
        line_count = len(raw_text.splitlines())
        if self.bounds.max_lines is not None and line_count > self.bounds.max_lines:
            raise GuardViolationError(
                guard_name="PayloadBounds.MaxLines",
                message=f"Payload lines ({line_count}) exceeds ceiling of {self.bounds.max_lines}.",
                details={"actual_lines": line_count, "max_lines": self.bounds.max_lines},
            )

        # 3. Forbidden substrings
        for sub in self.bounds.forbidden_substrings:
            if sub in raw_text:
                raise GuardViolationError(
                    guard_name="PayloadBounds.ForbiddenSubstring",
                    message=f"Banned substring detected in output: '{sub}'",
                    details={"forbidden_substring": sub},
                )

        # 4. Forbidden regex patterns
        for pattern in self.bounds.forbidden_patterns:
            if re.search(pattern, raw_text):
                raise GuardViolationError(
                    guard_name="PayloadBounds.ForbiddenPattern",
                    message=f"Banned regex pattern matched in output: '{pattern}'",
                    details={"forbidden_pattern": pattern},
                )

    def validate(self, raw_text: str) -> T:
        """Enforce physical bounds and parse into the validated Pydantic contract."""
        # Step 1: Check physical limits
        self.check_bounds(raw_text)

        # Step 2: Extract clean JSON string
        clean_json = extract_json_payload(raw_text)

        # Step 3: Validate against target schema
        try:
            return self.schema.model_validate_json(clean_json)
        except ValidationError as err:
            raise GuardViolationError(
                guard_name="SchemaContractGuard",
                message=f"Output fails schema '{self.schema.__name__}': {err}",
                details={"errors": err.errors(), "raw_json": clean_json},
            ) from err

    def to_sensor(self) -> Any:
        """Convert this guard into a Tier 1 SchemaSensor for pipeline evaluation."""
        from airframe.sensors.schema import SchemaSensor
        return SchemaSensor(schema=self.schema, bounds=self.bounds)
