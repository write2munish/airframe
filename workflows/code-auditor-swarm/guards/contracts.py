"""Executable Pydantic contract for code-auditor-swarm."""

from typing import Literal

from pydantic import BaseModel, Field


class SwarmAuditReport(BaseModel):
    """Synthesized report combining isolated worker handoffs."""

    summary: str = Field(description="High-level audit verdict synthesized across workers.")
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = Field(description="Overall risk classification.")
    findings_count: int = Field(ge=0, description="Total number of security or quality findings.")
    passed: bool = Field(default=True, description="True if zero critical security blockers exist.")
