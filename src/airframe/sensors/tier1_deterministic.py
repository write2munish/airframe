"""Tier 1: Fast, deterministic, zero-cost sensors (syntax, regex, exit codes, keywords)."""

from __future__ import annotations

import ast
import re
from typing import Any

from airframe.sensors.base import Sensor, SensorResult


class PythonASTSensor(Sensor):
    """Verifies that generated code compiles into a valid Python Abstract Syntax Tree."""

    def __init__(self, name: str = "python_ast_validator") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        code_str = str(candidate)
        try:
            ast.parse(code_str)
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message="Code successfully parsed into valid Python AST.",
            )
        except SyntaxError as err:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"SyntaxError on line {err.lineno}: {err.msg}",
                diagnostics={"line": err.lineno, "offset": err.offset, "text": err.text},
            )


class RegexSensor(Sensor):
    """Enforces that candidate text matches required patterns or does not match forbidden patterns."""

    def __init__(
        self,
        name: str,
        pattern: str,
        must_match: bool = True,
        flags: int = 0,
    ) -> None:
        self._name = name
        self.pattern = pattern
        self.must_match = must_match
        self.flags = flags

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        text = str(candidate)
        matched = bool(re.search(self.pattern, text, flags=self.flags))

        if self.must_match and matched:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message=f"Pattern '{self.pattern}' matched.",
            )
        elif self.must_match and not matched:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Required pattern '{self.pattern}' was not found in candidate output.",
                diagnostics={"pattern": self.pattern},
            )
        elif not self.must_match and matched:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Forbidden pattern '{self.pattern}' was found in candidate output.",
                diagnostics={"pattern": self.pattern},
            )
        else:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message=f"Forbidden pattern '{self.pattern}' was correctly absent.",
            )


class KeywordSensor(Sensor):
    """Ensures mandatory keywords are present in output text."""

    def __init__(self, name: str, required_keywords: list[str], case_sensitive: bool = False) -> None:
        self._name = name
        self.required_keywords = required_keywords
        self.case_sensitive = case_sensitive

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        text = str(candidate) if self.case_sensitive else str(candidate).lower()
        missing = [
            kw for kw in self.required_keywords
            if (kw if self.case_sensitive else kw.lower()) not in text
        ]

        if not missing:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message="All required keywords are present.",
            )
        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=False,
            message=f"Missing required keywords: {missing}",
            diagnostics={"missing_keywords": missing},
        )


class ExitCodeSensor(Sensor):
    """Asserts that a command or process execution returned exit code 0."""

    def __init__(self, name: str = "exit_code_zero") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        # Candidate can be an int or a dict with 'exit_code'
        if isinstance(candidate, dict):
            exit_code = candidate.get("exit_code", -1)
        elif isinstance(candidate, int):
            exit_code = candidate
        else:
            try:
                exit_code = int(str(candidate).strip())
            except ValueError:
                return SensorResult(
                    sensor_name=self.name,
                    tier=self.tier,
                    passed=False,
                    message=f"Candidate value '{candidate}' cannot be parsed as an exit code.",
                )

        if exit_code == 0:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message="Process exited successfully with code 0.",
            )
        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=False,
            message=f"Process exited with non-zero failure code: {exit_code}.",
            diagnostics={"exit_code": exit_code},
        )
