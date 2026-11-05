"""Executable Pydantic contract for basic-calculator."""

from pydantic import BaseModel, Field


class CalculationResult(BaseModel):
    """Canonical structured output schema for calculator results."""

    query: str = Field(description="The user's original mathematical query or expression.")
    result: float = Field(description="The computed numeric result.")
    formatted_result: str = Field(description="Formatted human-readable string representation of the result.")
    steps: list[str] = Field(default_factory=list, description="Step-by-step breakdown or formula applied.")
    status: str = Field(default="SUCCESS", description="Execution status.")
