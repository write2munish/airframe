"""Sandboxed ToolRunner integrating ToolCallValidatorSensor, HumanApprovalGate, and StallDetector."""

from __future__ import annotations

import inspect
import json
import time
from typing import Any, Callable

from pydantic import BaseModel

from airframe.governance.circuit_breakers import (
    ApprovalRejectedError,
    HumanApprovalGate,
    StallDetector,
)
from airframe.sensors.tier2_semantic import ToolCallValidatorSensor
from airframe.tools.registry import ToolNotFoundError, ToolRegistry


class ToolExecutionResult(BaseModel):
    """Normalized execution result from ToolRunner."""

    tool_name: str
    success: bool
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0


class ToolRunner:
    """Executes registered tools safely with whitelist, HITL approval, and stall detection."""

    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: list[str] | None = None,
        approval_gate: HumanApprovalGate | None = None,
        stall_detector: StallDetector | None = None,
        approval_callback: Callable[[str], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.allowed_tools = allowed_tools
        self.approval_gate = approval_gate or HumanApprovalGate()
        self.stall_detector = stall_detector
        self.approval_callback = approval_callback

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> ToolExecutionResult:
        """Validate, govern, and execute a registered tool."""
        args = dict(arguments or {})
        effective_allowed = allowed_tools if allowed_tools is not None else self.allowed_tools

        canonical_sig = f"{tool_name}({json.dumps(args, sort_keys=True, default=str)})"
        start_t = time.perf_counter()

        # 1. Lookup tool
        try:
            reg_tool = self.registry.get(tool_name)
        except ToolNotFoundError as err:
            err_msg = str(err)
            if self.stall_detector is not None:
                self.stall_detector.record_attempt(canonical_sig, success=False, error_message=err_msg)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=err_msg,
                latency_ms=(time.perf_counter() - start_t) * 1000.0,
            )

        # 2. Tier 2 ToolCallValidatorSensor check (whitelist & required params)
        validator = ToolCallValidatorSensor(
            allowed_tools=effective_allowed or [],
            required_params={tool_name: reg_tool.spec.required_params},
        )
        sensor_res = await validator.evaluate({"tool_name": tool_name, "arguments": args})
        if not sensor_res.passed:
            if self.stall_detector is not None:
                self.stall_detector.record_attempt(
                    canonical_sig,
                    success=False,
                    error_message=sensor_res.message,
                )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=sensor_res.message,
                latency_ms=(time.perf_counter() - start_t) * 1000.0,
            )

        # 3. Human-in-the-Loop approval gate check for destructive operations
        action_text = f"{tool_name} {json.dumps(args, default=str)}"
        if self.approval_gate.requires_approval(action_text):
            if self.approval_callback is None:
                raise ApprovalRejectedError(
                    f"Operation '{action_text}' requires interactive human approval, "
                    "but no approval_callback was configured."
                )
            self.approval_gate.request_approval(action_text, self.approval_callback)

        # 4. Validate/coerce input arguments with Pydantic model and invoke
        try:
            validated_args = reg_tool.args_model(**args)
            call_kwargs = validated_args.model_dump()

            if inspect.iscoroutinefunction(reg_tool.fn):
                output = await reg_tool.fn(**call_kwargs)
            else:
                output = reg_tool.fn(**call_kwargs)

            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            if self.stall_detector is not None:
                self.stall_detector.record_attempt(canonical_sig, success=True)

            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                output=output,
                latency_ms=elapsed_ms,
            )
        except Exception as exec_err:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            err_str = f"{type(exec_err).__name__}: {exec_err}"
            if self.stall_detector is not None:
                self.stall_detector.record_attempt(
                    canonical_sig,
                    success=False,
                    error_message=err_str,
                )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=err_str,
                latency_ms=elapsed_ms,
            )
