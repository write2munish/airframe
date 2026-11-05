"""Orchestration plane: supervisor, worker delegation, and evaluator-optimizer loops."""

from airframe.orchestration.evaluator_optimizer import (
    EvaluatorOptimizerLoop,
    IterationRecord,
    OptimizationResult,
)
from airframe.orchestration.supervisor import (
    SubtaskSpec,
    SupervisorAgent,
    SupervisorResult,
)

__all__ = [
    "EvaluatorOptimizerLoop",
    "IterationRecord",
    "OptimizationResult",
    "SubtaskSpec",
    "SupervisorAgent",
    "SupervisorResult",
]
