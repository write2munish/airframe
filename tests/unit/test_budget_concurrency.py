"""Concurrency and atomic reservation tests for BudgetTracker, WorkflowRunner, and SupervisorAgent."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
from airframe.ipc.lifecycle import AgentState
from airframe.isolation.context import CompressedHandoff
from airframe.orchestration.supervisor import SubtaskSpec, SupervisorAgent
from airframe.runner import (
    AgentStepDecision,
    OrchestratorPlan,
    PlannedSubtask,
    WorkerStepReport,
    WorkflowRunner,
    WorkflowRunResult,
)
from airframe.telemetry import AirframeTracer
from airframe.workflows.loader import load_workflow


class _ConcurrentBarrierAdapter(ModelAdapter):
    """Instrumented async adapter opening a concurrency window before returning usage."""

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
            # without blocking once BudgetTracker rejects excess workers pre-dispatch.
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
                context_partitioner=ContextPartitionerFactory.build(tmp_path, worker_idx),
                allow_cwd_switch=False,
            )

    async def _gather_all() -> list[Any]:
        return list(await asyncio.gather(*(_worker(i) for i in range(num_workers)), return_exceptions=True))

    outcomes = asyncio.run(_gather_all())
    return adapter, shared_tracker, outcomes


class ContextPartitionerFactory:
    """Small helper keeping isolated per-worker transcript storage out of test body noise."""

    @staticmethod
    def build(tmp_path: Path, worker_idx: int) -> Any:
        from airframe.isolation.context import ContextPartitioner

        return ContextPartitioner(
            agent_id=f"worker-{worker_idx}",
            storage_dir=tmp_path / "transcripts" / f"w{worker_idx}",
        )


def test_toctou_hazard_reproduced_under_unreserved_posthoc_checks() -> None:
    """Prove why unreserved post-hoc BudgetTracker.record_usage overshoots N-fold under concurrency."""
    num_workers = 6
    max_tokens = 100
    tokens_per_call = 80

    tracker = BudgetTracker(max_tokens=max_tokens, max_cost_usd=0.01)
    adapter = _ConcurrentBarrierAdapter(
        expected_concurrent=num_workers,
        tokens_per_call=tokens_per_call,
        cost_per_call=0.008,
    )

    async def _unreserved_worker(idx: int) -> ModelResponse:
        # Deliberate vulnerable pattern (check without atomic reserve before await).
        if tracker.total_tokens >= max_tokens:
            raise BudgetExceededError("Pre-check saw exhausted token budget.")
        resp = await adapter.invoke(prompt=f"Subtask {idx}", system_prompt="Execute bounded subtask.")
        tracker.record_usage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cost_usd=resp.usage.estimated_cost_usd,
        )
        return resp

    async def _run() -> list[Any]:
        return list(await asyncio.gather(*(_unreserved_worker(i) for i in range(num_workers)), return_exceptions=True))

    outcomes = asyncio.run(_run())

    assert adapter.call_count == num_workers
    assert adapter.max_concurrent == num_workers
    assert tracker.total_tokens == num_workers * tokens_per_call
    assert tracker.total_tokens > max_tokens
    assert sum(1 for o in outcomes if isinstance(o, BudgetExceededError)) == num_workers - 1


def test_concurrent_workers_strictly_bounded_by_tight_budget(tmp_path: Path) -> None:
    """Correctness gate: N concurrent workers on a 100-token ceiling must dispatch <= 1 call (80 tok/call)."""
    num_workers = 6
    max_tokens = 100
    max_cost_usd = 0.01

    adapter, shared_tracker, outcomes = _run_concurrent_workers(
        tmp_path,
        num_workers=num_workers,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
        tokens_per_call=80,
        cost_per_call=0.008,
    )

    assert adapter.call_count == 1
    assert adapter.max_concurrent == 1
    assert shared_tracker.total_tokens == 80
    assert shared_tracker.total_tokens <= max_tokens
    assert shared_tracker.total_cost_usd == pytest.approx(0.008)
    assert shared_tracker.total_cost_usd <= max_cost_usd
    assert shared_tracker.reserved_tokens == 0
    assert shared_tracker.reserved_cost_usd == 0.0
    assert sum(1 for o in outcomes if isinstance(o, WorkflowRunResult)) == 1
    assert sum(1 for o in outcomes if isinstance(o, BudgetExceededError)) == num_workers - 1


def test_budget_tracker_reserve_reconcile_refund_and_threads() -> None:
    """Verify atomic reserve/reconcile/refund semantics, idempotency, and thread safety."""
    tracker = BudgetTracker(max_tokens=100, max_cost_usd=0.02)

    res1 = tracker.reserve(estimated_tokens=60, estimated_cost_usd=0.01, label="w1")
    assert tracker.reserved_tokens == 60
    assert tracker.effective_tokens == 60
    assert tracker.remaining_tokens == 40
    assert tracker.remaining_cost_usd == pytest.approx(0.01)

    # Exceeding token or USD ceilings rejects pre-flight without corrupting state
    with pytest.raises(BudgetExceededError, match="Token budget exceeded on pre-flight reserve"):
        tracker.reserve(estimated_tokens=41, estimated_cost_usd=0.001, label="too-big-tok")
    assert tracker.reserved_tokens == 60
    assert tracker.active_reservations_count == 1

    with pytest.raises(BudgetExceededError, match="Cost budget exceeded on pre-flight reserve"):
        tracker.reserve(estimated_tokens=10, estimated_cost_usd=0.011, label="too-big-cost")
    assert tracker.reserved_tokens == 60

    # Refund releases capacity cleanly and is idempotent
    assert tracker.refund(res1) is True
    assert tracker.refund(res1) is False
    assert tracker.reserved_tokens == 0
    assert tracker.reserved_cost_usd == 0.0
    assert tracker.remaining_tokens == 100

    # Reconcile converts a hold to actual spend
    res2 = tracker.reserve(estimated_tokens=70, estimated_cost_usd=0.015, label="w2")
    tracker.reconcile(res2, input_tokens=30, output_tokens=20, cost_usd=0.005)
    assert tracker.reserved_tokens == 0
    assert tracker.reserved_cost_usd == 0.0
    assert tracker.total_tokens == 50
    assert tracker.total_cost_usd == pytest.approx(0.005)
    assert tracker.remaining_tokens == 50

    # Parallel OS threads racing on reserve() never admit beyond ceiling
    thread_tracker = BudgetTracker(max_tokens=100)

    def _try_reserve(i: int) -> bool:
        try:
            thread_tracker.reserve(estimated_tokens=40, label=f"t{i}")
            return True
        except BudgetExceededError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_try_reserve, range(16)))

    assert sum(1 for ok in results if ok) == 2
    assert thread_tracker.reserved_tokens == 80
    assert thread_tracker.remaining_tokens == 20


def test_workflow_runner_refunds_reservation_on_invoke_failure(tmp_path: Path) -> None:
    """Model invoke errors must refund pre-flight reservations so subsequent calls can use that budget."""

    class _FlakyAdapter(ModelAdapter):
        def __init__(self) -> None:
            super().__init__(model_name="flaky-model")
            self.should_fail = True

        @property
        def provider_name(self) -> str:
            return "mock-flaky"

        async def invoke(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> ModelResponse:
            if self.should_fail:
                raise RuntimeError("transient provider outage")
            return ModelResponse(
                content='{"summary": "recovered", "status": "SUCCESS"}',
                model_name=self.model_name,
                provider=self.provider_name,
                usage=UsageMetadata(input_tokens=30, output_tokens=20, total_tokens=50, estimated_cost_usd=0.002),
            )

    wf_dir = tmp_path / "refund_wf"
    wf_dir.mkdir()
    (wf_dir / "workflow.yaml").write_text(
        yaml.dump({"id": "refund-wf", "topology": "single-agent", "governance": {"max_tokens": 90}}),
        encoding="utf-8",
    )
    pack = load_workflow(wf_dir)
    bp = pack.load_blueprints()[0]
    tracer = AirframeTracer(trace_dir=tmp_path / "traces")
    runner = WorkflowRunner(tracer=tracer)
    tracker = BudgetTracker(max_tokens=90, max_cost_usd=0.01)
    adapter = _FlakyAdapter()

    async def _run_once(q: str) -> WorkflowRunResult:
        return await runner._run_single_agent(
            pack=pack,
            blueprint=bp,
            sys_prompt="Concise prompt.",
            input_query=q,
            adapter=adapter,
            contract_schema=WorkerStepReport,
            tool_registry=pack.load_tools(),
            sensors=[],
            budget_tracker=tracker,
            stall_detector=StallDetector(),
            effective_max_turns=2,
            tracer=tracer,
            work_dir=tmp_path,
            allow_cwd_switch=False,
        )

    with pytest.raises(RuntimeError, match="transient provider outage"):
        asyncio.run(_run_once("Attempt 1"))

    # Full reservation must be refunded — zero ghost debt left
    assert tracker.reserved_tokens == 0
    assert tracker.reserved_cost_usd == 0.0
    assert tracker.total_tokens == 0
    assert tracker.active_reservations_count == 0

    # A follow-up call requesting ~70 tokens on the 90-token tracker now succeeds
    adapter.should_fail = False
    res = asyncio.run(_run_once("Attempt 2"))
    assert res.output["summary"] == "recovered"
    assert tracker.reserved_tokens == 0
    assert tracker.total_tokens == 50


def test_workflow_runner_all_four_call_sites_reserve_before_await_and_reconcile(tmp_path: Path) -> None:
    """Verify all 4 engine.py invoke_contract call sites reserve pre-await and reconcile afterwards."""

    class _InspectingAdapter(ModelAdapter):
        def __init__(self, tracker: BudgetTracker, responses: list[ModelResponse]) -> None:
            super().__init__(model_name="inspect-model")
            self._tracker = tracker
            self._responses = list(responses)
            self.observed_reserved_in_flight: list[int] = []
            self.observed_active_holds_in_flight: list[int] = []

        @property
        def provider_name(self) -> str:
            return "mock-inspect"

        async def invoke(self, prompt: str, system_prompt: str | None = None, **kwargs: Any) -> ModelResponse:
            self.observed_reserved_in_flight.append(self._tracker.reserved_tokens)
            self.observed_active_holds_in_flight.append(self._tracker.active_reservations_count)
            return self._responses.pop(0)

    def _resp(payload: str, tokens: int = 20, cost: float = 0.001) -> ModelResponse:
        return ModelResponse(
            content=payload,
            model_name="inspect-model",
            provider="mock-inspect",
            usage=UsageMetadata(
                input_tokens=tokens // 2,
                output_tokens=tokens - (tokens // 2),
                total_tokens=tokens,
                estimated_cost_usd=cost,
            ),
        )

    tracer = AirframeTracer(trace_dir=tmp_path / "traces")
    runner = WorkflowRunner(tracer=tracer)

    # 1. Site 1 (direct single-agent without tools) + Site 2 (multi-turn ReAct tool loop)
    wf_tools = tmp_path / "react_wf"
    wf_tools.mkdir()
    (wf_tools / "tools.py").write_text("def double(n: int) -> int:\n    return n * 2\n", encoding="utf-8")
    (wf_tools / "workflow.yaml").write_text(
        yaml.dump({"id": "react-wf", "topology": "single-agent", "tools": ["double"]}),
        encoding="utf-8",
    )
    pack_react = load_workflow(wf_tools)
    bp_react = pack_react.load_blueprints()[0]

    tracker_1 = BudgetTracker(max_tokens=10_000, max_cost_usd=1.0)
    adapter_1 = _InspectingAdapter(
        tracker_1,
        [_resp(WorkerStepReport(summary="direct", status="SUCCESS").model_dump_json(), tokens=30)],
    )
    asyncio.run(
        runner._run_single_agent(
            pack=pack_react,
            blueprint=bp_react.model_copy(update={"allowed_tools": []}),
            sys_prompt="Direct",
            input_query="Hello",
            adapter=adapter_1,
            contract_schema=WorkerStepReport,
            tool_registry=pack_react.load_tools(),
            sensors=[],
            budget_tracker=tracker_1,
            stall_detector=StallDetector(),
            effective_max_turns=2,
            tracer=tracer,
            work_dir=tmp_path,
            allow_cwd_switch=False,
        )
    )
    assert len(adapter_1.observed_reserved_in_flight) == 1
    assert adapter_1.observed_reserved_in_flight[0] > 0
    assert adapter_1.observed_active_holds_in_flight == [1]
    assert tracker_1.reserved_tokens == 0
    assert tracker_1.total_tokens == 30

    tracker_2 = BudgetTracker(max_tokens=10_000, max_cost_usd=1.0)
    adapter_2 = _InspectingAdapter(
        tracker_2,
        [
            _resp(
                AgentStepDecision(
                    thought="call double",
                    action="call_tool",
                    tool_name="double",
                    arguments={"n": 4},
                ).model_dump_json(),
                tokens=40,
            ),
            _resp(
                AgentStepDecision(
                    thought="emit final",
                    action="final_answer",
                    final_output={"summary": "8", "status": "SUCCESS"},
                ).model_dump_json(),
                tokens=50,
            ),
        ],
    )
    res_2 = asyncio.run(
        runner._run_single_agent(
            pack=pack_react,
            blueprint=bp_react,
            sys_prompt="ReAct",
            input_query="Double 4",
            adapter=adapter_2,
            contract_schema=WorkerStepReport,
            tool_registry=pack_react.load_tools(),
            sensors=[],
            budget_tracker=tracker_2,
            stall_detector=StallDetector(),
            effective_max_turns=4,
            tracer=tracer,
            work_dir=tmp_path,
            allow_cwd_switch=False,
        )
    )
    assert res_2.tools_called == ["double"]
    assert len(adapter_2.observed_reserved_in_flight) == 2
    assert all(tok > 0 for tok in adapter_2.observed_reserved_in_flight)
    assert adapter_2.observed_active_holds_in_flight == [1, 1]
    assert tracker_2.reserved_tokens == 0
    assert tracker_2.total_tokens == 90

    # 2. Sites 3 & 4: _run_orchestrator_workers (orchestrator.plan + worker + orchestrator.synthesize)
    wf_ow = tmp_path / "ow_wf"
    (wf_ow / "blueprints").mkdir(parents=True)
    (wf_ow / "blueprints" / "lead.yaml").write_text(
        yaml.dump({"name": "lead", "role": "Lead", "system_prompt": "Lead", "engine_binding": "mock"}),
        encoding="utf-8",
    )
    (wf_ow / "blueprints" / "worker.yaml").write_text(
        yaml.dump({"name": "worker", "role": "Worker", "system_prompt": "Work", "engine_binding": "mock"}),
        encoding="utf-8",
    )
    (wf_ow / "workflow.yaml").write_text(
        yaml.dump(
            {
                "id": "ow-wf",
                "topology": "orchestrator-workers",
                "entrypoint_agent": "lead",
                "agents": ["blueprints/lead.yaml", "blueprints/worker.yaml"],
            }
        ),
        encoding="utf-8",
    )
    pack_ow = load_workflow(wf_ow)
    bps_ow = {bp.name: bp for bp in pack_ow.load_blueprints()}

    tracker_ow = BudgetTracker(max_tokens=10_000, max_cost_usd=1.0)
    adapter_ow = _InspectingAdapter(
        tracker_ow,
        [
            _resp(
                OrchestratorPlan(
                    goal="G",
                    subtasks=[PlannedSubtask(task_id="t1", description="Do work", assigned_blueprint="worker")],
                ).model_dump_json(),
                tokens=30,
            ),
            _resp(WorkerStepReport(summary="Sub done").model_dump_json(), tokens=25),
            _resp('{"summary": "All done", "status": "SUCCESS"}', tokens=35),
        ],
    )
    res_ow = asyncio.run(
        runner._run_orchestrator_workers(
            pack=pack_ow,
            entrypoint_bp=bps_ow["lead"],
            blueprints_by_name=bps_ow,
            sys_prompt="Lead",
            input_query="Run orchestrated task",
            adapter=adapter_ow,
            contract_schema=WorkerStepReport,
            tool_registry=pack_ow.load_tools(),
            sensors=[],
            budget_tracker=tracker_ow,
            stall_detector=StallDetector(),
            effective_max_turns=4,
            tracer=tracer,
            work_dir=tmp_path,
        )
    )
    assert res_ow.output["summary"] == "All done"
    # Plan (1 hold), Worker (supervisor hold + model hold = 2 holds), Synthesize (1 hold)
    assert len(adapter_ow.observed_reserved_in_flight) == 3
    assert all(tok > 0 for tok in adapter_ow.observed_reserved_in_flight)
    assert adapter_ow.observed_active_holds_in_flight == [1, 2, 1]
    assert tracker_ow.reserved_tokens == 0
    assert tracker_ow.total_tokens == 90


def test_supervisor_reserves_before_create_task_and_releases_on_completion_failure_and_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SupervisorAgent must gate asyncio.create_task via reserve() and always release() on exit/failure/cancel."""
    # 1. Concurrency gate around asyncio.create_task
    tracker = BudgetTracker(max_tokens=100)
    supervisor = SupervisorAgent(supervisor_id="gated_sup", budget_tracker=tracker, default_worker_reserve_tokens=64)

    spawned_tasks = 0
    orig_create_task = asyncio.create_task

    def _counting_create_task(coro: Any, *args: Any, **kwargs: Any) -> asyncio.Task[Any]:
        nonlocal spawned_tasks
        spawned_tasks += 1
        return orig_create_task(coro, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", _counting_create_task)

    observed_reserved_during_worker: list[int] = []

    async def _slow_worker(subtask: SubtaskSpec) -> CompressedHandoff:
        observed_reserved_during_worker.append(tracker.reserved_tokens)
        await asyncio.sleep(0.05)
        return CompressedHandoff(
            agent_id=f"worker_{subtask.task_id}",
            status="SUCCESS",
            final_output="done",
            tokens_discarded=0,
            total_steps=1,
        )

    subtasks = [SubtaskSpec(task_id=f"t{i}", description=f"Task {i}") for i in range(6)]

    async def _race_subtasks() -> list[Any]:
        return list(
            await asyncio.gather(
                *(supervisor.delegate_subtask(st, worker_runner=_slow_worker) for st in subtasks),
                return_exceptions=True,
            )
        )

    outcomes = asyncio.run(_race_subtasks())

    assert spawned_tasks == 1
    assert observed_reserved_during_worker == [64]
    assert sum(1 for o in outcomes if isinstance(o, CompressedHandoff) and o.status == "SUCCESS") == 1
    assert sum(1 for o in outcomes if isinstance(o, BudgetExceededError)) == 5
    assert tracker.reserved_tokens == 0
    assert tracker.active_reservations_count == 0
    for i in range(1, 6):
        assert supervisor.lifecycle.get_state(f"worker_t{i}") == AgentState.FAILED

    # 2. Release on generic worker exception
    async def _exploding_worker(subtask: SubtaskSpec) -> CompressedHandoff:
        raise ValueError("boom")

    err_handoff = asyncio.run(
        supervisor.delegate_subtask(
            SubtaskSpec(task_id="err_task", description="Fails"),
            worker_runner=_exploding_worker,
        )
    )
    assert err_handoff.status == "FAILED"
    assert tracker.reserved_tokens == 0
    assert tracker.active_reservations_count == 0

    # 3. Release on CancelledError and propagate cancellation
    async def _cancellable_worker(subtask: SubtaskSpec) -> CompressedHandoff:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            supervisor.delegate_subtask(
                SubtaskSpec(task_id="cancel_task", description="Cancels"),
                worker_runner=_cancellable_worker,
            )
        )
    assert tracker.reserved_tokens == 0
    assert supervisor.lifecycle.get_state("worker_cancel_task") == AgentState.CANCELLED

    # 4. BudgetExceededError inside worker propagates (never swallowed into FAILED handoff) and releases hold
    async def _budget_breach_worker(subtask: SubtaskSpec) -> CompressedHandoff:
        raise BudgetExceededError("inner limit tripped")

    with pytest.raises(BudgetExceededError, match="inner limit tripped"):
        asyncio.run(
            supervisor.delegate_subtask(
                SubtaskSpec(task_id="breach_task", description="Breaches"),
                worker_runner=_budget_breach_worker,
            )
        )
    assert tracker.reserved_tokens == 0
    assert supervisor.lifecycle.get_state("worker_breach_task") == AgentState.FAILED
