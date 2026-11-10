"""Telemetry, scorecards, hierarchical tracing, and differential regression analysis."""

from airframe.telemetry.diff import (
    CaseResult,
    DifferentialReport,
    RunScorecard,
    compare_scorecards,
)
from airframe.telemetry.tracer import (
    AirframeTracer,
    SpanKind,
    SpanRecord,
    SpanStatus,
    build_trace_tree,
    get_active_tracer,
    load_trace,
)

__all__ = [
    "AirframeTracer",
    "CaseResult",
    "DifferentialReport",
    "RunScorecard",
    "SpanKind",
    "SpanRecord",
    "SpanStatus",
    "build_trace_tree",
    "compare_scorecards",
    "get_active_tracer",
    "load_trace",
]
