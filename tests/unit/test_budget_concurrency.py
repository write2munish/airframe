"""Concurrency tests proving the BudgetTracker/WorkflowRunner TOCTOU budget overshoot hazard."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata
from airframe.governance.circuit_breakers import (
    Budget,
    BudgetExceededError,
    BudgetTracker,
    StallDetector,
)
from airframe.isolation.context import ContextPartitioner
from airframe.runner import WorkerStepReport, WorkflowRunner
from airframe.telemetry import AirframeTracer
from airframe.workflows.loader import load_workflow


class _ConcurrentBarrierAdapter(ModelAdapter):
    """Instrumented async adapter opening a narrow concurrency window before returning usage."""

    def __init__(
        self,
        expected_concurrent: int,
        *,
        tokens_per_call: int = 80,
        cost_per_call: float = 0.008,
    ) -> None:
        super().__init__(model_name="concurrent-budget-probe")
        self._expected_concurrent = expected_concurrent
        self._tokens_per_call = tokens_per_call
        self._cost_per_call = cost_per_call
        self._in_flight = 0
        self.call_count = 0
        self.max_concurrent = 0
        self._all_in_flight = asyncio.Event()

    @property
    def provider_name(self) -> str:
        return "mock-concurrent"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        self.call_count += 1
        self._in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self._in_flight)
        if self._in_flight >= self._expected_concurrent:
            self._all_in_flight.set()

        try:
            # Brief bounded wait lets sibling tasks reach invoke() simultaneously if unguarded,
            # without deadlocking once Phase 2 rejects or serializes excess workers pre-dispatch.
            await asyncio.wait_for(self._all_in_flight.wait(), timeout=0.05)
        except TimeoutError:
            pass
        finally:
            self._in_flight -= 1

        in_tok = self._tokens_per_call // 2
        out_tok = self._tokens_per_call - in_tok
        return ModelResponse(
            content='{"summary": "worker done", "status": "SUCCESS"}',
            model_name=self.model_name,
            provider=self.provider_name,
            usage=UsageMetadata(
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=self._tokens_per_call,
                estimated_cost_usd=self._cost_per_call,
            ),
        )


def _run_concurrent_workers(
    tmp_path: Path,
    *,
    num_workers: int = 6,
    max_tokens: int = 100,
    max_cost_usd: float = 0.01,
    tokens_per_call: int = 80,
    cost_per_call: float = 0.008,
) -> tuple[_ConcurrentBarrierAdapter, BudgetTracker, list[Any]]:
    """Run N concurrent workers sharing one tight BudgetTracker via WorkflowRunner._run_single_agent."""
    wf_dir = tmp_path / "tight_budget_wf"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "workflow.yaml").write_text(
        yaml.dump(
            {
                "id": "tight-budget-wf",
                "topology": "single-agent",
                "entrypoint_agent": "worker",
                "system_prompt": "Execute bounded subtask.",
                "governance": {
                    "max_tokens": max_tokens,
                    "max_cost_usd": max_cost_usd,
                    "max_turns": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    pack = load_workflow(wf_dir)
    blueprint = pack.load_blueprints()[0]
    tracer = AirframeTracer(trace_dir=tmp_path / "traces")
    runner = WorkflowRunner(tracer=tracer)
    shared_tracker = BudgetTracker(budget=Budget(max_tokens=max_tokens, max_cost_usd=max_cost_usd))
    adapter = _ConcurrentBarrierAdapter(
        expected_concurrent=num_workers,
        tokens_per_call=tokens_per_call,
        cost_per_call=cost_per_call,
    )

    async def _worker(worker_idx: int) -> Any:
        # Time-of-check: naive pre-flight read passes for every concurrent task before usage commits.
        if shared_tracker.total_tokens >= max_tokens:
            raise BudgetExceededError("Pre-check saw exhausted token budget.")

        async with tracer.start_span(f"worker.{worker_idx}", kind="worker"):
            return await runner._run_single_agent(
                pack=pack,
                blueprint=blueprint,
                sys_prompt="Execute bounded subtask.",
                input_query=f"Subtask {worker_idx}",
                adapter=adapter,
                contract_schema=WorkerStepReport,
                tool_registry=pack.load_tools(),
                sensors=[],
                budget_tracker=shared_tracker,
                stall_detector=StallDetector(),
                effective_max_turns=2,
                tracer=tracer,
                work_dir=tmp_path,
                context_partitioner=ContextPartitioner(
                    agent_id=f"worker-{worker_idx}",
                    storage_dir=tmp_path / "transcripts" / f"w{worker_idx}",
                ),
                allow_cwd_switch=False,
            )

    async def _gather_all() -> list[Any]:
        return list(await asyncio.gather(*(_worker(i) for i in range(num_workers)), return_exceptions=True))

    outcomes = asyncio.run(_gather_all())
    return adapter, shared_tracker, outcomes


def test_toctou_hazard_reproduced_under_n_concurrent_workers(tmp_path: Path) -> None:
    """Prove N concurrent workers bypass post-hoc BudgetTracker accounting and overshoot N-fold."""
    num_workers = 6
    max_tokens = 100
    tokens_per_call = 80

    adapter, shared_tracker, outcomes = _run_concurrent_workers(
        tmp_path,
        num_workers=num_workers,
        max_tokens=max_tokens,
        tokens_per_call=tokens_per_call,
    )

    # All N workers pass the pre-call check, invoke the model concurrently, and overshoot 4.8x before failing post-hoc.
    assert adapter.call_count == num_workers
    assert adapter.max_concurrent == num_workers
    assert shared_tracker.total_tokens == num_workers * tokens_per_call
    assert shared_tracker.total_tokens > max_tokens
    assert sum(1 for o in outcomes if isinstance(o, BudgetExceededError)) == num_workers - 1


@pytest.mark.xfail(
    strict=True,
    reason="Known TOCTOU hazard: BudgetTracker lacks atomic pre-flight reservation and WorkflowRunner records usage post-hoc (fixed in Phase 2).",
)
def test_concurrent_workers_strictly_bounded_by_tight_budget(tmp_path: Path) -> None:
    """Correctness gate invariant: N concurrent workers on a 100-token ceiling must dispatch <= 1 call (80 tok/call)."""
    max_tokens = 100
    max_cost_usd = 0.01

    adapter, shared_tracker, _ = _run_concurrent_workers(
        tmp_path,
        num_workers=6,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        tokens_per_call=80,
        cost_per_call=0.008,
    )

    assert adapter.call_count <= 1
    assert shared_tracker.total_tokens <= max_tokens
    assert shared_tracker.total_cost_usd <= max_cost_usd
