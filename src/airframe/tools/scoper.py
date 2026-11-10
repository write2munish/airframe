"""Phase-based tool scoping and context diet budgeting (FR-4)."""

from __future__ import annotations

import warnings

from airframe.tools.registry import ToolPhase, ToolRegistry, ToolSpec


class ToolScopeExceededError(ValueError):
    """Raised when a workflow phase binds more tools than the allowed context-diet cap."""
    pass


class ContextDietWarning(UserWarning):
    """Warning emitted when guide/prompt token footprint exceeds recommended ceilings."""
    pass


class ContextBudgetExceededError(RuntimeError):
    """Raised in strict mode when guide/prompt token footprint breaches ceiling."""
    pass


class ScopedToolInjector:
    """Enforces phase-scoped tool whitelists (max 3-5 active tools per phase)."""

    def __init__(
        self,
        registry: ToolRegistry,
        max_tools_per_phase: int = 5,
    ) -> None:
        self.registry = registry
        self.max_tools_per_phase = max_tools_per_phase
        self._phase_bindings: dict[ToolPhase, list[str]] = {
            "discovery": [],
            "execution": [],
            "verification": [],
        }

    def bind_phase(self, phase: ToolPhase, tool_names: list[str]) -> list[ToolSpec]:
        """Bind a bounded list of tools to a specific workflow phase."""
        unique_names = list(dict.fromkeys(tool_names))
        if len(unique_names) > self.max_tools_per_phase:
            raise ToolScopeExceededError(
                f"Phase '{phase}' attempts to bind {len(unique_names)} tools, "
                f"exceeding the maximum ceiling of {self.max_tools_per_phase}."
            )

        specs: list[ToolSpec] = []
        for name in unique_names:
            specs.append(self.registry.get(name).spec)

        self._phase_bindings[phase] = unique_names
        return specs

    def get_tools_for_phase(self, phase: ToolPhase) -> list[ToolSpec]:
        """Return ToolSpec objects scoped to the active phase."""
        return self.registry.list_specs(self._phase_bindings[phase])

    def get_tool_names(self, phase: ToolPhase) -> list[str]:
        """Return bound tool names for the active phase."""
        return list(self._phase_bindings[phase])

    def render_phase_guidance(self, phase: ToolPhase) -> str:
        """Render scoped tool schemas for prompt injection in the active phase."""
        names = self._phase_bindings[phase]
        if not names:
            return "[]"
        return self.registry.to_prompt_schema(names)


class ContextBudgeter:
    """Measures prompt/guide token footprints to enforce lean context diets."""

    def __init__(
        self,
        max_guide_tokens: int = 4000,
        strict: bool = False,
    ) -> None:
        self.max_guide_tokens = max_guide_tokens
        self.strict = strict

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token footprint (~4 characters per token)."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def check_guide(self, guide_text: str, name: str = "guide") -> int:
        """Validate guide token footprint, warning or raising if oversized."""
        tokens = self.estimate_tokens(guide_text)
        if tokens > self.max_guide_tokens:
            msg = (
                f"Context diet warning: '{name}' consumes ~{tokens} tokens, "
                f"exceeding recommended ceiling of {self.max_guide_tokens} tokens."
            )
            if self.strict:
                raise ContextBudgetExceededError(msg)
            warnings.warn(msg, ContextDietWarning, stacklevel=2)
        return tokens
