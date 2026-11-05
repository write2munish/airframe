"""Telemetry, scorecards, and differential regression analysis."""

from airframe.telemetry.diff import (
    CaseResult,
    DifferentialReport,
    RunScorecard,
    compare_scorecards,
)

__all__ = [
    "CaseResult",
    "DifferentialReport",
    "RunScorecard",
    "compare_scorecards",
]
