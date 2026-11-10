"""Tier 1 deterministic sensors for patch-verifier Evaluator-Optimizer loop."""

from __future__ import annotations

import ast
import json
from typing import Any

from airframe.sensors.base import Sensor, SensorResult


class SafePythonPatchSensor(Sensor):
    """Tier 1 Sensor: validates Python AST compilation and forbids unsafe eval/exec calls."""

    _FORBIDDEN_PATTERNS = ("eval(", "exec(", "os.system(")

    @property
    def name(self) -> str:
        return "safe_python_patch_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        if hasattr(candidate, "model_dump"):
            data = candidate.model_dump()
        elif isinstance(candidate, dict):
            data = candidate
        elif isinstance(candidate, str):
            try:
                parsed = json.loads(candidate)
                data = parsed if isinstance(parsed, dict) else {"python_code": candidate}
            except Exception:
                data = {"python_code": candidate}
        else:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Unsupported candidate payload type for SafePythonPatchSensor.",
            )

        code = str(data.get("python_code", "")).strip()
        if not code:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Field 'python_code' is empty; must supply executable Python code.",
            )

        try:
            ast.parse(code)
        except SyntaxError as err:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Python SyntaxError at line {err.lineno}: {err.msg}",
                diagnostics={"line": err.lineno, "msg": err.msg},
            )

        found_forbidden = [pat for pat in self._FORBIDDEN_PATTERNS if pat in code]
        if found_forbidden:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Unsafe primitive(s) detected in python_code: {found_forbidden}",
                diagnostics={"forbidden": found_forbidden},
            )

        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message="Python patch compiles cleanly and contains zero forbidden execution primitives.",
        )
