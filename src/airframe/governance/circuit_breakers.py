"""Governance, circuit breakers, and unified budget tracking for autonomous agent runs."""

from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


class GovernanceError(RuntimeError):
    """Base exception for agent governance and circuit breaker violations."""
    pass


class BudgetExceededError(GovernanceError):
    """Raised when token or dollar budget ceilings are breached."""
    pass


class StallDetectedError(GovernanceError):
    """Raised when an agent is caught in an infinite loop or repetitive failure pattern."""
    pass


class RecursionDepthExceededError(GovernanceError):
    """Raised when sub-agent delegation exceeds the maximum allowed nesting depth."""
    pass


class ApprovalRejectedError(GovernanceError):
    """Raised when a human rejects a flagged high-risk operation."""
    pass


class Budget(BaseModel):
    """Unified resource quotas and circuit breakers for workflows and agents.

    Eliminates duplicated budget schemas between workflow manifests and agent blueprints.
    Workflow defines default limits; individual agent blueprints can override.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    max_tokens: int = Field(default=150_000, description="Maximum total tokens allowed before halting.")
    max_turns: int = Field(default=20, description="Maximum turn iterations allowed.")
    timeout_seconds: int = Field(default=600, description="Max wall-clock seconds before timing out.")
    max_cost_usd: float | None = Field(
        default=None,
        alias="max_budget_usd",
        description="Maximum spend ceiling in USD.",
    )
    stall_threshold: int = Field(default=3, description="Consecutive failure count to trip stall detector.")

    def merge_with_override(self, override: Budget | None) -> Budget:
        """Create a merged budget where non-default override values take precedence."""
        if override is None:
            return self.model_copy()
        data = self.model_dump()
        override_data = override.model_dump(exclude_unset=True)
        data.update(override_data)
        return Budget.model_validate(data)


# Alias for backward compatibility
AgentBudget = Budget
GovernanceSpec = Budget


class BudgetTracker:
    """Monitors token usage and financial spend, enforcing hard budget ceilings."""

    def __init__(
        self,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        budget: Budget | None = None,
    ) -> None:
        if budget is not None:
            self.max_tokens = budget.max_tokens
            self.max_cost_usd = budget.max_cost_usd
        else:
            self.max_tokens = max_tokens
            self.max_cost_usd = max_cost_usd

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0
        self.total_cost_usd = 0.0

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Record token consumption and check against budget ceilings."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += (input_tokens + output_tokens)
        self.total_cost_usd += cost_usd

        if self.max_tokens is not None and self.total_tokens > self.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self.total_tokens} > {self.max_tokens} ceiling."
            )

        if self.max_cost_usd is not None and self.total_cost_usd > self.max_cost_usd:
            raise BudgetExceededError(
                f"Cost budget exceeded: ${self.total_cost_usd:.4f} > ${self.max_cost_usd:.4f} ceiling."
            )


class StallDetector:
    """Detects repetitive tool-call loops and consecutive recurring failure traps."""

    def __init__(self, stall_threshold: int = 3) -> None:
        self.stall_threshold = stall_threshold
        self._last_signature: str | None = None
        self._consecutive_failures = 0

    def record_attempt(
        self,
        action_signature: str,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        """Record an attempted action and trip circuit breaker if repeating failures occur."""
        normalized_sig = action_signature.strip()

        if success:
            self._consecutive_failures = 0
            self._last_signature = None
            return

        # Failure occurred
        if self._last_signature == normalized_sig:
            self._consecutive_failures += 1
        else:
            self._last_signature = normalized_sig
            self._consecutive_failures = 1

        if self._consecutive_failures >= self.stall_threshold:
            raise StallDetectedError(
                f"Agent stalled: Action '{normalized_sig}' failed {self._consecutive_failures} "
                f"consecutive times without progress.\nLast error: {error_message or 'Unknown'}"
            )


class RecursionLimiter:
    """Enforces maximum sub-agent nesting depth to prevent runaway agent spawning trees."""

    def __init__(self, max_depth: int = 2) -> None:
        self.max_depth = max_depth

    def check_depth(self, current_depth: int) -> None:
        """Verify that spawning another sub-agent does not exceed the depth limit."""
        if current_depth > self.max_depth:
            raise RecursionDepthExceededError(
                f"Sub-agent delegation depth {current_depth} exceeds maximum ceiling of {self.max_depth}."
            )


class HumanApprovalGate:
    """Flags destructive actions and requests interactive human confirmation before execution."""

    DEFAULT_SENSITIVE_PATTERNS = [
        r"\brm\s+-[rf]{1,2}\b",
        r"\bdelete\b",
        r"\bdrop\s+table\b",
        r"\bpush\s+--force\b",
        r"\bformat\b",
        r"\bshutdown\b",
    ]

    def __init__(self, sensitive_patterns: list[str] | None = None) -> None:
        self.sensitive_patterns = sensitive_patterns or self.DEFAULT_SENSITIVE_PATTERNS

    def requires_approval(self, command_or_action: str) -> bool:
        """Check if an action matches sensitive patterns."""
        for pattern in self.sensitive_patterns:
            if re.search(pattern, command_or_action, flags=re.IGNORECASE):
                return True
        return False

    def request_approval(
        self,
        action_description: str,
        approval_callback: Callable[[str], bool],
    ) -> bool:
        """Request confirmation for an action via approval_callback."""
        approved = approval_callback(action_description)
        if not approved:
            raise ApprovalRejectedError(
                f"Operation rejected by human operator: '{action_description}'"
            )
        return True
