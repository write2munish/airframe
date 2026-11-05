"""Evaluator-Optimizer loop: eliminates self-evaluation bias via independent verification."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from airframe.sensors.base import SensorPipeline, SensorResult


class IterationRecord(BaseModel):
    """Log of a single generator-evaluator iteration."""

    iteration: int
    candidate: str
    passed: bool
    sensor_results: list[SensorResult]
    critique: str


class OptimizationResult(BaseModel):
    """Outcome of the Evaluator-Optimizer feedback loop."""

    passed: bool
    final_candidate: str
    iterations: int
    history: list[IterationRecord]
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluatorOptimizerLoop:
    """Iterative feedback loop pairing an independent generator with an objective sensor pipeline."""

    def __init__(
        self,
        sensor_pipeline: SensorPipeline,
        max_iterations: int = 3,
    ) -> None:
        self.sensor_pipeline = sensor_pipeline
        self.max_iterations = max_iterations

    async def run(
        self,
        initial_prompt: str,
        generator_fn: Callable[[str, str | None], Coroutine[Any, Any, str]],
        context: dict[str, Any] | None = None,
    ) -> OptimizationResult:
        """Run the optimization loop until all sensors pass or max_iterations is reached.

        Args:
            initial_prompt: The base task instruction for the generator.
            generator_fn: Async callable taking (prompt, previous_critique) -> candidate_string.
            context: Additional contextual data passed into sensors (e.g. golden references).

        Returns:
            OptimizationResult detailing whether the candidate passed and history of critiques.
        """
        history: list[IterationRecord] = []
        last_critique: str | None = None
        current_candidate: str = ""
        passed = False

        for i in range(1, self.max_iterations + 1):
            # 1. Generator generates candidate output, informed by prior critique if any
            current_candidate = await generator_fn(initial_prompt, last_critique)

            # 2. Objective Sensor Pipeline evaluates candidate
            sensor_results = await self.sensor_pipeline.run(current_candidate, context=context)
            failed_sensors = [r for r in sensor_results if not r.passed]

            if not failed_sensors:
                passed = True
                critique = "All sensors passed successfully."
                history.append(
                    IterationRecord(
                        iteration=i,
                        candidate=current_candidate,
                        passed=True,
                        sensor_results=sensor_results,
                        critique=critique,
                    )
                )
                break

            # 3. Formulate structured critique from sensor failures
            critique_lines = [f"- [{r.sensor_name} (Tier {r.tier})]: {r.message}" for r in failed_sensors]
            last_critique = "The previous output failed verification with the following issues:\n" + "\n".join(critique_lines)

            history.append(
                IterationRecord(
                    iteration=i,
                    candidate=current_candidate,
                    passed=False,
                    sensor_results=sensor_results,
                    critique=last_critique,
                )
            )

        return OptimizationResult(
            passed=passed,
            final_candidate=current_candidate,
            iterations=len(history),
            history=history,
        )


# Canonical alias
FeedbackLoop = EvaluatorOptimizerLoop


async def run_with_retry(
    initial_prompt: str,
    generator_fn: Callable[[str, str | None], Coroutine[Any, Any, str]],
    sensors: SensorPipeline | list[Any] | Any,
    max_retries: int = 3,
    context: dict[str, Any] | None = None,
) -> OptimizationResult:
    """Canonical retry-with-critique primitive: generate -> sensor check -> critique -> retry.

    Unifies executable schema contract enforcement and multi-tier evaluator-optimizer loops into
    a single reusable feedback control loop.
    """
    if isinstance(sensors, SensorPipeline):
        pipeline = sensors
    elif isinstance(sensors, list):
        pipeline = SensorPipeline(sensors=sensors)
    else:
        if hasattr(sensors, "to_sensor"):
            pipeline = SensorPipeline(sensors=[sensors.to_sensor()])
        else:
            pipeline = SensorPipeline(sensors=[sensors])

    loop = EvaluatorOptimizerLoop(sensor_pipeline=pipeline, max_iterations=max_retries + 1)
    return await loop.run(initial_prompt=initial_prompt, generator_fn=generator_fn, context=context)
