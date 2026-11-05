"""Executable contracts and output schemas for PR Code Reviewer."""

from typing import Literal
from pydantic import BaseModel, Field

SeverityLevel = Literal["BLOCKER", "WARNING", "NIT"]


class ReviewIssue(BaseModel):
    """An individual defect or suggestion flagged during code review."""

    file_path: str = Field(description="Relative path to the affected file.")
    line_number: int = Field(description="Line number in the file or patch.")
    severity: SeverityLevel = Field(description="Severity classification.")
    description: str = Field(description="Detailed explanation of the defect.")
    suggested_fix: str | None = Field(default=None, description="Proposed replacement code snippet.")


class CodeReviewReport(BaseModel):
    """Canonical output report returned by the Code Reviewer."""

    summary: str = Field(description="High-level assessment of the pull request.")
    passed: bool = Field(description="True if zero BLOCKER issues exist.")
    issues: list[ReviewIssue] = Field(default_factory=list, description="List of issues identified.")
