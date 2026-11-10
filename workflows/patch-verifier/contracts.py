"""Executable Pydantic contract for patch-verifier workflow."""

from pydantic import BaseModel, Field


class VerifiedPatchReport(BaseModel):
    """Canonical structured output for verified Python patches."""

    summary: str = Field(description="Concise summary of the implemented Python solution.")
    python_code: str = Field(description="Syntactically valid, AST-safe Python source code.")
    safety_notes: str = Field(
        default="Verified AST-safe with zero dynamic eval/exec primitives.",
        description="Notes explaining safety and invariants upheld.",
    )
    passed: bool = Field(default=True, description="Whether the patch passed self-checks.")
