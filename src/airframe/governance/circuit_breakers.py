"""Governance, circuit breakers, and unified budget tracking for autonomous agent runs."""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from typing import Callable

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


@dataclass(frozen=True)
class BudgetReservation:
    """In-flight budget hold debited atomically before spawning a task or invoking a model."""

    reservation_id: str
    tokens: int = 0
    cost_usd: float = 0.0
    label: str = ""


class BudgetTracker:
    """Monitors token usage and financial spend, enforcing hard budget ceilings via atomic reservations."""

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

        self._lock = threading.Lock()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_tokens = 0
        self.total_cost_usd = 0.0
        self.reserved_tokens = 0
        self.reserved_cost_usd = 0.0
        self._active_reservations: dict[str, BudgetReservation] = {}

    @property
    def effective_tokens(self) -> int:
        """Return total committed plus currently reserved in-flight tokens."""
        with self._lock:
            return self.total_tokens + self.reserved_tokens

    @property
    def effective_cost_usd(self) -> float:
        """Return total committed plus currently reserved in-flight spend in USD."""
        with self._lock:
            return self.total_cost_usd + self.reserved_cost_usd

    @property
    def remaining_tokens(self) -> int | None:
        """Return remaining token headroom after committed and reserved holds, or None if uncapped."""
        if self.max_tokens is None:
            return None
        with self._lock:
            return max(0, self.max_tokens - (self.total_tokens + self.reserved_tokens))

    @property
    def remaining_cost_usd(self) -> float | None:
        """Return remaining USD headroom after committed and reserved holds, or None if uncapped."""
        if self.max_cost_usd is None:
            return None
        with self._lock:
            return max(0.0, self.max_cost_usd - (self.total_cost_usd + self.reserved_cost_usd))

    @property
    def active_reservations_count(self) -> int:
        """Return number of un-reconciled in-flight reservations."""
        with self._lock:
            return len(self._active_reservations)

    def reserve(
        self,
        estimated_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        *,
        label: str = "",
    ) -> BudgetReservation:
        """Atomically verify remaining headroom and debit a pre-flight reservation."""
        est_tokens = max(0, int(estimated_tokens))
        est_cost = max(0.0, float(estimated_cost_usd))

        with self._lock:
            projected_tokens = self.total_tokens + self.reserved_tokens + est_tokens
            if self.max_tokens is not None and projected_tokens > self.max_tokens:
                raise BudgetExceededError(
                    f"Token budget exceeded on pre-flight reserve: {projected_tokens} "
                    f"(committed={self.total_tokens}, reserved={self.reserved_tokens}, "
                    f"requested={est_tokens}) > {self.max_tokens} ceiling."
                )

            projected_cost = self.total_cost_usd + self.reserved_cost_usd + est_cost
            if self.max_cost_usd is not None and projected_cost > self.max_cost_usd + 1e-12:
                raise BudgetExceededError(
                    f"Cost budget exceeded on pre-flight reserve: ${projected_cost:.4f} "
                    f"(committed=${self.total_cost_usd:.4f}, reserved=${self.reserved_cost_usd:.4f}, "
                    f"requested=${est_cost:.4f}) > ${self.max_cost_usd:.4f} ceiling."
                )

            reservation = BudgetReservation(
                reservation_id=uuid.uuid4().hex,
                tokens=est_tokens,
                cost_usd=est_cost,
                label=label,
            )
            self._active_reservations[reservation.reservation_id] = reservation
            self.reserved_tokens += est_tokens
            self.reserved_cost_usd += est_cost
            return reservation

    def _pop_reservation_locked(self, reservation: BudgetReservation) -> bool:
        """Remove an active reservation while holding self._lock (idempotent)."""
        active = self._active_reservations.pop(reservation.reservation_id, None)
        if active is None:
            return False
        self.reserved_tokens = max(0, self.reserved_tokens - active.tokens)
        self.reserved_cost_usd = max(0.0, self.reserved_cost_usd - active.cost_usd)
        if not self._active_reservations:
            self.reserved_tokens = 0
            self.reserved_cost_usd = 0.0
        return True

    def _commit_and_check_locked(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Commit actual usage while holding self._lock and validate budget ceilings."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.total_cost_usd += cost_usd

        if self.max_tokens is not None and self.total_tokens > self.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self.total_tokens} > {self.max_tokens} ceiling."
            )

        if self.max_cost_usd is not None and self.total_cost_usd > self.max_cost_usd + 1e-12:
            raise BudgetExceededError(
                f"Cost budget exceeded: ${self.total_cost_usd:.4f} > ${self.max_cost_usd:.4f} ceiling."
            )

    def release(self, reservation: BudgetReservation | None) -> bool:
        """Release an unspent pre-flight reservation back to available budget (idempotent)."""
        if reservation is None:
            return False
        with self._lock:
            return self._pop_reservation_locked(reservation)

    def refund(self, reservation: BudgetReservation | None) -> bool:
        """Refund an unspent reservation after a failed or cancelled call (alias for release)."""
        return self.release(reservation)

    def reconcile(
        self,
        reservation: BudgetReservation | None,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Release reservation, commit actual consumption, and verify hard ceilings."""
        with self._lock:
            if reservation is not None:
                self._pop_reservation_locked(reservation)
            self._commit_and_check_locked(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Record token consumption and check against budget ceilings."""
        with self._lock:
            self._commit_and_check_locked(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
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
