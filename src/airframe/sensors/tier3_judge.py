"""Tier 3: Calibrated Model-as-a-Judge rubric evaluator."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from airframe.adapters.base import ModelAdapter
from airframe.sensors.base import Sensor, SensorResult


class RubricCriterion(BaseModel):
    """Individual grading dimension for an LLM judge."""

    name: str = Field(description="Criterion name (e.g. 'factual_accuracy', 'conciseness').")
    description: str = Field(description="Rubric description explaining what defines quality.")
    weight: float = Field(default=1.0, ge=0.0, description="Weighting factor.")


class JudgeRubric(BaseModel):
    """Evaluation rubric defining grading dimensions and passing thresholds."""

    rubric_id: str
    description: str
    criteria: list[RubricCriterion]
    passing_score: float = Field(default=0.7, ge=0.0, le=1.0, description="Minimum score to pass.")


class JudgeVerdict(BaseModel):
    """Structured verdict emitted by the Model Judge."""

    score: float = Field(ge=0.0, le=1.0, description="Aggregate score from 0.0 to 1.0.")
    passed: bool = Field(description="True if score >= passing_score.")
    reasoning: str = Field(description="Detailed explanation justifying the score.")
    criterion_scores: dict[str, float] = Field(
        default_factory=dict, description="Individual scores for each rubric criterion."
    )


class ModelJudgeSensor(Sensor):
    """Evaluates candidate output using a calibrated LLM-as-a-judge bound to a rubric."""

    def __init__(
        self,
        name: str,
        adapter: ModelAdapter,
        rubric: JudgeRubric,
    ) -> None:
        self._name = name
        self.adapter = adapter
        self.rubric = rubric

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return 3

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        context_str = json.dumps(context, indent=2) if context else "None provided"
        rubric_spec = json.dumps(self.rubric.model_dump(), indent=2)

        prompt = (
            f"You are an impartial, highly rigorous evaluation judge.\n\n"
            f"[EVALUATION RUBRIC]\n{rubric_spec}\n\n"
            f"[CONTEXT]\n{context_str}\n\n"
            f"[CANDIDATE OUTPUT UNDER REVIEW]\n{candidate}\n\n"
            f"Grade the candidate strictly against the rubric criteria.\n"
            f"Passing threshold: {self.rubric.passing_score:.2f}."
        )

        try:
            verdict, resp = await self.adapter.invoke_contract(
                prompt=prompt,
                schema=JudgeVerdict,
                temperature=0.0,
            )

            is_pass = verdict.score >= self.rubric.passing_score
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=is_pass,
                message=f"Judge score {verdict.score:.2f}/{1.00} - {'PASS' if is_pass else 'FAIL'}: {verdict.reasoning}",
                diagnostics={
                    "score": verdict.score,
                    "passing_threshold": self.rubric.passing_score,
                    "criterion_scores": verdict.criterion_scores,
                    "judge_tokens": resp.usage.total_tokens,
                },
            )
        except Exception as err:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Judge sensor execution failed: {err}",
                diagnostics={"error": str(err)},
            )
