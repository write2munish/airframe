"""Unified WorkflowRunner runtime engine wiring isolation, tools, governance, shift turnover, and tracing."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from airframe.adapters.base import ModelAdapter, ModelResponse
from airframe.blueprints.schema import AgentBlueprint
from airframe.governance.circuit_breakers import (
    BudgetReservation,
    BudgetTracker,
    GovernanceError,
    StallDetector,
)
from airframe.guards.executable import ExecutableGuard, GuardViolationError
from airframe.isolation.context import CompressedHandoff, ContextPartitioner
from airframe.isolation.workspace import get_workspace
from airframe.orchestration.evaluator_optimizer import EvaluatorOptimizerLoop
from airframe.orchestration.shift import ShiftTurnoverEngine
from airframe.orchestration.supervisor import SubtaskSpec, SupervisorAgent
from airframe.sensors.base import Sensor, SensorPipeline, SensorResult
from airframe.state.disk import DiskStateManager, TaskItem
from airframe.telemetry.tracer import AirframeTracer, get_active_tracer
from airframe.tools.registry import ToolRegistry
from airframe.tools.runner import ToolRunner
from airframe.tools.scoper import ContextBudgeter, ScopedToolInjector
from airframe.workflows.loader import WorkflowPack

_ACTIVE_WORKSPACE_DIR: ContextVar[Path | None] = ContextVar("airframe_active_workspace_dir", default=None)
_MIN_CONTRACT_OUTPUT_TOKENS = 64
_FALLBACK_COST_PER_TOKEN_USD = 5e-6


def _estimate_contract_call_budget(
    prompt: str,
    system_prompt: str | None,
    budget_tracker: BudgetTracker,
) -> tuple[int, float]:
    """Conservative synchronous estimate of (tokens, cost_usd) for an invoke_contract call."""
    combined_prompt = f"{system_prompt or ''}\n{prompt}".strip()
    prompt_tokens = ContextBudgeter.estimate_tokens(combined_prompt)
    est_tokens = max(_MIN_CONTRACT_OUTPUT_TOKENS, prompt_tokens + _MIN_CONTRACT_OUTPUT_TOKENS)

    if budget_tracker.max_cost_usd is None:
        return est_tokens, 0.0

    if budget_tracker.total_tokens > 0 and budget_tracker.total_cost_usd > 0.0:
        observed_rate = budget_tracker.total_cost_usd / budget_tracker.total_tokens
        cost_rate = max(_FALLBACK_COST_PER_TOKEN_USD, observed_rate)
    else:
        cost_rate = _FALLBACK_COST_PER_TOKEN_USD

    return est_tokens, est_tokens * cost_rate


def get_current_workspace_dir() -> Path | None:
    """Return the workspace directory active for the current async execution task, if any."""
    return _ACTIVE_WORKSPACE_DIR.get()


class MaxTurnsExceededError(GovernanceError):
    """Raised when an agent exceeds its configured max_turns ceiling without emitting a final answer."""
    pass


class AgentStepDecision(BaseModel):
    """Model-agnostic structured control decision for ReAct tool-calling loops."""

    thought: str
    action: Literal["call_tool", "final_answer"]
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    final_output: dict[str, Any] | None = None


class PlannedSubtask(BaseModel):
    """Subtask plan entry synthesized by orchestrator-workers topology."""

    task_id: str
    description: str
    assigned_blueprint: str = "default_worker"


class OrchestratorPlan(BaseModel):
    """Execution decomposition plan for orchestrator-workers workflows."""

    goal: str = ""
    subtasks: list[PlannedSubtask] = Field(default_factory=list)


class WorkerStepReport(BaseModel):
    """Structured summary emitted by a delegated worker agent."""

    summary: str
    status: str = "SUCCESS"
    findings: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResult(BaseModel):
    """Unified output and governance telemetry returned by WorkflowRunner."""

    workflow_id: str
    topology: str
    entrypoint_agent: str
    workspace_mode: str
    provider: str
    model_name: str
    output: dict[str, Any]
    validated_output: Any = Field(default=None, exclude=True)
    contract_name: str
    turns_executed: int = 1
    shifts_executed: int = 1
    tools_called: list[str] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    sensor_results: list[SensorResult] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: float = 0.0
    trace_id: str | None = None
    trace_file: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def all_sensors_passed(self) -> bool:
        """Return True if all executed sensors reported passed=True."""
        return all(r.passed for r in self.sensor_results)


def load_contract_schema(pack: WorkflowPack) -> type[BaseModel]:
    """Discover and dynamically import the primary output Pydantic contract for a workflow pack."""
    candidates = [
        pack.root_dir / "guards" / "contracts.py",
        pack.root_dir / "contracts.py",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        mod_name = f"airframe_contracts_{pack.id.replace('-', '_')}_{abs(hash(str(p))) % 100000}"
        spec = importlib.util.spec_from_file_location(mod_name, p)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            local_models: list[type[BaseModel]] = []
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseModel)
                    and attr is not BaseModel
                    and getattr(attr, "__module__", None) == module.__name__
                ):
                    local_models.append(attr)

            if local_models:
                if pack.manifest.schema_contract:
                    target_name = pack.manifest.schema_contract.split(":")[-1].split(".")[-1]
                    for m in local_models:
                        if m.__name__ == target_name:
                            return m
                for suffix in ("Report", "Contract", "Result", "Output"):
                    for m in local_models:
                        if m.__name__.endswith(suffix):
                            return m
                return local_models[0]

    class DefaultContract(BaseModel):
        summary: str
        status: str = "SUCCESS"

    return DefaultContract


def discover_tools(pack: WorkflowPack) -> ToolRegistry:
    """Auto-discover public tool functions in workflows/<pack>/tools.py or tools/*.py."""
    registry = ToolRegistry()
    root_tools = pack.root_dir / "tools.py"
    if root_tools.is_file():
        registry.load_from_file(root_tools, default_phase="execution")

    tools_dir = pack.root_dir / "tools"
    if tools_dir.is_dir():
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            registry.load_from_file(py_file, default_phase="execution")

    return registry


def resolve_blueprint_prompt(pack: WorkflowPack, blueprint: AgentBlueprint) -> str:
    """Resolve inline prompt or relative prompt file path from a blueprint."""
    sys_prompt = blueprint.system_prompt
    if "\n" not in sys_prompt and len(sys_prompt) < 255:
        try:
            candidate = pack.root_dir / sys_prompt
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            pass
    return sys_prompt


class WorkflowRunner:
    """Single canonical runtime orchestrating workflows, tools, circuit breakers, shifts, and traces."""

    def __init__(
        self,
        tracer: AirframeTracer | None = None,
        trace_dir: str | Path = ".airframe/traces",
        max_recursion_depth: int = 2,
        max_tools_per_phase: int = 5,
        max_guide_tokens: int = 4000,
    ) -> None:
        self.tracer = tracer
        self.trace_dir = Path(trace_dir)
        self.max_recursion_depth = max_recursion_depth
        self.max_tools_per_phase = max_tools_per_phase
        self.max_guide_tokens = max_guide_tokens

    def _select_entrypoint_blueprint(
        self,
        pack: WorkflowPack,
        blueprints: list[AgentBlueprint],
    ) -> AgentBlueprint:
        """Select entrypoint_agent strictly instead of blindly choosing blueprints[0]."""
        if not blueprints:
            raise ValueError(f"No agent blueprints found in workflow pack '{pack.id}'.")

        target_name = pack.manifest.entrypoint_agent
        if not target_name:
            return blueprints[0]

        for bp in blueprints:
            if bp.name == target_name:
                return bp

        available = [bp.name for bp in blueprints]
        raise ValueError(
            f"Entrypoint agent '{target_name}' specified in workflow '{pack.id}' "
            f"not found among loaded blueprints: {available}."
        )

    async def run(
        self,
        pack: WorkflowPack,
        input_query: str,
        adapter: ModelAdapter,
        *,
        contract_schema: type[BaseModel] | None = None,
        tool_registry: ToolRegistry | None = None,
        sensors: list[Sensor] | None = None,
        shift_engine: ShiftTurnoverEngine | None = None,
        state_manager: DiskStateManager | None = None,
        approval_callback: Callable[[str], bool] | None = None,
        eval_context: dict[str, Any] | None = None,
        workspace_repo_path: str | Path | None = None,
        context_partitioner: ContextPartitioner | None = None,
        extra_subtasks: list[SubtaskSpec] | None = None,
        timeout_seconds: float | None = None,
        delete_workspace_on_exit: bool = True,
        fail_on_sensor_error: bool = False,
    ) -> WorkflowRunResult:
        """Execute a workflow pack end-to-end under full circuit breakers, isolation, and tracing."""
        blueprints = pack.load_blueprints()
        entrypoint_bp = self._select_entrypoint_blueprint(pack, blueprints)
        blueprints_by_name = {bp.name: bp for bp in blueprints}

        effective_budget = pack.manifest.governance.merge_with_override(entrypoint_bp.budget)
        effective_timeout = float(
            timeout_seconds if timeout_seconds is not None else effective_budget.timeout_seconds
        )

        budget_tracker = BudgetTracker(budget=effective_budget)
        stall_detector = StallDetector(stall_threshold=effective_budget.stall_threshold)

        active_contract = contract_schema or load_contract_schema(pack)
        active_registry = tool_registry if tool_registry is not None else discover_tools(pack)
        active_sensors = list(sensors or [])
        active_tracer = self.tracer or get_active_tracer() or AirframeTracer(trace_dir=self.trace_dir)

        sys_prompt = resolve_blueprint_prompt(pack, entrypoint_bp)
        guide_tokens = ContextBudgeter(
            max_guide_tokens=self.max_guide_tokens, strict=False
        ).check_guide(sys_prompt, name=entrypoint_bp.name)

        workspace_repo = Path(workspace_repo_path or pack.root_dir).resolve()
        workspace = get_workspace(
            mode=entrypoint_bp.workspace_mode,
            agent_id=entrypoint_bp.name,
            repo_path=workspace_repo,
            delete_on_exit=delete_workspace_on_exit,
        )

        run_started_t = time.perf_counter()

        async def _execute_inside_span() -> WorkflowRunResult:
            async with active_tracer.start_span(
                f"workflow.{pack.id}",
                kind="root",
                attributes={
                    "workflow_id": pack.id,
                    "topology": pack.manifest.topology,
                    "entrypoint": entrypoint_bp.name,
                    "workspace_mode": entrypoint_bp.workspace_mode,
                },
            ):
                async with workspace as work_dir:
                    ws_token = _ACTIVE_WORKSPACE_DIR.set(work_dir)
                    try:
                        topology = pack.manifest.topology
                        if topology == "single-agent":
                            res = await self._run_single_agent(
                                pack=pack,
                                blueprint=entrypoint_bp,
                                sys_prompt=sys_prompt,
                                input_query=input_query,
                                adapter=adapter,
                                contract_schema=active_contract,
                                tool_registry=active_registry,
                                sensors=active_sensors,
                                budget_tracker=budget_tracker,
                                stall_detector=stall_detector,
                                effective_max_turns=effective_budget.max_turns,
                                tracer=active_tracer,
                                work_dir=work_dir,
                                shift_engine=shift_engine,
                                state_manager=state_manager,
                                approval_callback=approval_callback,
                                eval_context=eval_context,
                                context_partitioner=context_partitioner,
                            )
                        elif topology == "evaluator-optimizer":
                            res = await self._run_evaluator_optimizer(
                                pack=pack,
                                blueprint=entrypoint_bp,
                                sys_prompt=sys_prompt,
                                input_query=input_query,
                                adapter=adapter,
                                contract_schema=active_contract,
                                tool_registry=active_registry,
                                sensors=active_sensors,
                                budget_tracker=budget_tracker,
                                stall_detector=stall_detector,
                                effective_max_turns=effective_budget.max_turns,
                                tracer=active_tracer,
                                work_dir=work_dir,
                                shift_engine=shift_engine,
                                state_manager=state_manager,
                                approval_callback=approval_callback,
                                eval_context=eval_context,
                            )
                        elif topology == "orchestrator-workers":
                            res = await self._run_orchestrator_workers(
                                pack=pack,
                                entrypoint_bp=entrypoint_bp,
                                blueprints_by_name=blueprints_by_name,
                                sys_prompt=sys_prompt,
                                input_query=input_query,
                                adapter=adapter,
                                contract_schema=active_contract,
                                tool_registry=active_registry,
                                sensors=active_sensors,
                                budget_tracker=budget_tracker,
                                stall_detector=stall_detector,
                                effective_max_turns=effective_budget.max_turns,
                                tracer=active_tracer,
                                work_dir=work_dir,
                                state_manager=state_manager,
                                approval_callback=approval_callback,
                                eval_context=eval_context,
                                extra_subtasks=extra_subtasks,
                            )
                        else:
                            raise ValueError(f"Unsupported topology '{topology}' in workflow '{pack.id}'.")

                        res.trace_id = active_tracer.trace_id
                        res.trace_file = str(active_tracer.trace_file)
                        res.latency_ms = round((time.perf_counter() - run_started_t) * 1000.0, 2)
                        res.metadata.setdefault("guide_tokens", guide_tokens)
                        res.metadata.setdefault("workspace_path", str(work_dir))

                        if fail_on_sensor_error and not res.all_sensors_passed:
                            failures = [f"{r.sensor_name}: {r.message}" for r in res.sensor_results if not r.passed]
                            raise GuardViolationError(
                                f"Workflow '{pack.id}' failed sensor verification: {'; '.join(failures)}"
                            )
                        return res
                    finally:
                        _ACTIVE_WORKSPACE_DIR.reset(ws_token)

        return await asyncio.wait_for(_execute_inside_span(), timeout=effective_timeout)

    async def _run_single_agent(
        self,
        *,
        pack: WorkflowPack,
        blueprint: AgentBlueprint,
        sys_prompt: str,
        input_query: str,
        adapter: ModelAdapter,
        contract_schema: type[BaseModel],
        tool_registry: ToolRegistry,
        sensors: list[Sensor],
        budget_tracker: BudgetTracker,
        stall_detector: StallDetector,
        effective_max_turns: int,
        tracer: AirframeTracer,
        work_dir: Path,
        shift_engine: ShiftTurnoverEngine | None = None,
        state_manager: DiskStateManager | None = None,
        approval_callback: Callable[[str], bool] | None = None,
        eval_context: dict[str, Any] | None = None,
        context_partitioner: ContextPartitioner | None = None,
        allow_cwd_switch: bool = True,
    ) -> WorkflowRunResult:
        """Execute a single agent via direct contract call or multi-turn ReAct tool loop."""
        partitioner = context_partitioner or ContextPartitioner(agent_id=blueprint.name)
        partitioner.log_turn(role="user", content=input_query)

        # Bind local tools if workflow provides tool implementations and blueprint declares tools
        has_registry_tools = len(tool_registry.list_specs()) > 0
        allowed_names = list(blueprint.allowed_tools)
        active_tool_names: list[str] = []

        if allowed_names and has_registry_tools:
            scoper = ScopedToolInjector(
                registry=tool_registry,
                max_tools_per_phase=self.max_tools_per_phase,
            )
            scoper.bind_phase("execution", allowed_names)
            active_tool_names = scoper.get_tool_names("execution")

        # Short-circuit directly to invoke_contract when no executable local tools are bound
        if not active_tool_names:
            est_tokens, est_cost = _estimate_contract_call_budget(
                prompt=input_query,
                system_prompt=sys_prompt,
                budget_tracker=budget_tracker,
            )
            reservation: BudgetReservation = budget_tracker.reserve(
                estimated_tokens=est_tokens,
                estimated_cost_usd=est_cost,
                label=f"model.invoke_contract.{blueprint.name}",
            )
            try:
                async with tracer.start_span(
                    f"model.invoke_contract.{blueprint.name}",
                    kind="model",
                    attributes={"model": adapter.model_name, "contract": contract_schema.__name__},
                ):
                    validated_obj, resp = await adapter.invoke_contract(
                        prompt=input_query,
                        schema=contract_schema,
                        system_prompt=sys_prompt,
                        max_retries=2,
                    )
            except BaseException:
                budget_tracker.refund(reservation)
                raise

            budget_tracker.reconcile(
                reservation,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=resp.usage.estimated_cost_usd,
            )
            partitioner.log_turn(
                role="assistant",
                content=resp.content,
                tokens=resp.usage.output_tokens,
            )

            sensor_results: list[SensorResult] = []
            if sensors:
                async with tracer.start_span("sensor.pipeline", kind="sensor"):
                    sensor_results = await SensorPipeline(sensors).run(
                        validated_obj,
                        context={
                            "tool_sequence": [],
                            "tool_results": [],
                            **(eval_context or {}),
                        },
                    )

            return WorkflowRunResult(
                workflow_id=pack.id,
                topology=pack.manifest.topology,
                entrypoint_agent=blueprint.name,
                workspace_mode=blueprint.workspace_mode,
                provider=resp.provider,
                model_name=resp.model_name,
                output=validated_obj.model_dump(),
                validated_output=validated_obj,
                contract_name=contract_schema.__name__,
                turns_executed=1,
                shifts_executed=1,
                tools_called=[],
                tool_results=[],
                sensor_results=sensor_results,
                input_tokens=budget_tracker.total_input_tokens,
                output_tokens=budget_tracker.total_output_tokens,
                total_tokens=budget_tracker.total_tokens,
                estimated_cost_usd=budget_tracker.total_cost_usd,
                metadata={"tokens_shielded": partitioner._total_tokens_consumed},
            )

        # Multi-turn ReAct tool loop using AgentStepDecision
        tool_runner = ToolRunner(
            registry=tool_registry,
            allowed_tools=active_tool_names,
            stall_detector=stall_detector,
            approval_callback=approval_callback,
        )
        tools_schema_json = scoper.render_phase_guidance("execution")
        contract_schema_json = json.dumps(contract_schema.model_json_schema(), indent=2)

        current_shift = 1
        shifts_executed = 1
        turns_executed = 0
        cumulative_shift_tokens = 0
        conversation_history: list[str] = []
        active_task_prompt = input_query
        tools_called: list[str] = []
        tool_results: list[dict[str, Any]] = []
        last_resp: ModelResponse | None = None
        turn_prompts_log: list[str] = []

        active_state_mgr = state_manager
        while True:
            # Check shift turnover before starting next turn after at least 1 turn executed
            if (
                shift_engine is not None
                and turns_executed > 0
                and shift_engine.should_turnover(cumulative_shift_tokens)
            ):
                if active_state_mgr is None:
                    active_state_mgr = DiskStateManager(work_dir / ".airframe" / "state")
                    if not active_state_mgr.queue_file.is_file():
                        active_state_mgr.init_state(
                            plan_markdown=f"# Workflow: {pack.id}\nGoal: {input_query}",
                            tasks=[TaskItem(id="main", description=input_query, status="IN_PROGRESS")],
                        )

                wt_dir = work_dir if blueprint.workspace_mode == "branch" else None
                handoff_memo = (
                    f"Completed {turns_executed} turn(s) in shift {current_shift}; "
                    f"tools executed: {tools_called or ['none']}."
                )

                async with tracer.start_span(
                    f"shift.turnover_{current_shift}",
                    kind="supervisor",
                    attributes={"ending_shift": current_shift, "tokens_in_shift": cumulative_shift_tokens},
                ):
                    current_shift = shift_engine.execute_turnover(
                        state_manager=active_state_mgr,
                        shift_id=current_shift,
                        accomplishments_summary=handoff_memo,
                        worktree_dir=wt_dir,
                    )

                conversation_history.clear()
                cumulative_shift_tokens = 0
                shifts_executed += 1
                active_task_prompt = shift_engine.bootload_shift_prompt(
                    state_manager=active_state_mgr,
                    shift_id=current_shift,
                    extra_instructions=f"Original goal: {input_query}",
                )

            if turns_executed >= effective_max_turns:
                raise MaxTurnsExceededError(
                    f"Workflow '{pack.id}' exceeded max_turns ceiling ({effective_max_turns})."
                )

            turns_executed += 1

            history_block = "\n".join(conversation_history) if conversation_history else "(none yet)"
            turn_prompt = (
                f"[TASK]\n{active_task_prompt}\n\n"
                f"[AVAILABLE TOOLS]\n```json\n{tools_schema_json}\n```\n\n"
                f"[FINAL OUTPUT CONTRACT: {contract_schema.__name__}]\n"
                f"When returning action='final_answer', populate final_output conforming strictly to:\n"
                f"```json\n{contract_schema_json}\n```\n\n"
                f"[SHIFT {current_shift} EXECUTION HISTORY]\n{history_block}"
            )
            turn_prompts_log.append(turn_prompt)

            turn_est_tokens, turn_est_cost = _estimate_contract_call_budget(
                prompt=turn_prompt,
                system_prompt=sys_prompt,
                budget_tracker=budget_tracker,
            )
            turn_reservation = budget_tracker.reserve(
                estimated_tokens=turn_est_tokens,
                estimated_cost_usd=turn_est_cost,
                label=f"model.turn_{turns_executed}.{blueprint.name}",
            )
            try:
                async with tracer.start_span(
                    f"model.turn_{turns_executed}",
                    kind="model",
                    attributes={"turn": turns_executed, "shift": current_shift, "model": adapter.model_name},
                ):
                    decision, last_resp = await adapter.invoke_contract(
                        prompt=turn_prompt,
                        schema=AgentStepDecision,
                        system_prompt=sys_prompt,
                        max_retries=2,
                    )
            except BaseException:
                budget_tracker.refund(turn_reservation)
                raise

            budget_tracker.reconcile(
                turn_reservation,
                input_tokens=last_resp.usage.input_tokens,
                output_tokens=last_resp.usage.output_tokens,
                cost_usd=last_resp.usage.estimated_cost_usd,
            )
            step_tokens = (
                last_resp.usage.total_tokens
                or ContextBudgeter.estimate_tokens(turn_prompt + last_resp.content)
            )
            cumulative_shift_tokens += step_tokens
            partitioner.log_turn(
                role="assistant",
                content=last_resp.content,
                tokens=last_resp.usage.output_tokens,
            )

            if decision.action == "call_tool":
                t_name = (decision.tool_name or "").strip()
                if not t_name:
                    err_msg = "AgentStepDecision chose action='call_tool' without tool_name."
                    stall_detector.record_attempt("call_tool:<missing>", success=False, error_message=err_msg)
                    conversation_history.append(f"Turn {turns_executed}: Error: {err_msg}")
                    continue

                async with tracer.start_span(
                    f"tool.{t_name}",
                    kind="tool",
                    attributes={"tool_name": t_name, "turn": turns_executed},
                ) as tool_span:
                    prev_cwd: Path | None = None
                    if allow_cwd_switch and blueprint.workspace_mode in ("branch", "sandbox"):
                        prev_cwd = Path.cwd()
                        os.chdir(work_dir)
                    try:
                        exec_res = await tool_runner.execute(
                            tool_name=t_name,
                            arguments=decision.arguments,
                            allowed_tools=active_tool_names,
                        )
                    finally:
                        if prev_cwd is not None:
                            os.chdir(prev_cwd)

                    tool_span.set_attribute("success", exec_res.success)
                    if exec_res.error:
                        tool_span.set_attribute("error", exec_res.error)

                tools_called.append(t_name)
                tool_results.append(exec_res.model_dump())

                if exec_res.success:
                    obs = f"Tool '{t_name}' returned: {json.dumps(exec_res.output, default=str)}"
                else:
                    obs = f"Tool '{t_name}' failed: {exec_res.error}"

                conversation_history.append(
                    f"Turn {turns_executed} [Thought]: {decision.thought}\n"
                    f"Turn {turns_executed} [Observation]: {obs}"
                )
                partitioner.log_turn(role="tool", content=obs)
                continue

            # decision.action == "final_answer"
            candidate_payload = decision.final_output if decision.final_output is not None else {}
            try:
                validated_obj = contract_schema.model_validate(candidate_payload)
            except ValidationError as val_err:
                sig = f"schema:{contract_schema.__name__}:{json.dumps(candidate_payload, sort_keys=True, default=str)}"
                stall_detector.record_attempt(sig, success=False, error_message=str(val_err))
                if turns_executed < effective_max_turns:
                    conversation_history.append(
                        f"Turn {turns_executed} [Contract Validation Error]: {val_err}"
                    )
                    continue
                raise GuardViolationError(
                    f"Final output failed '{contract_schema.__name__}' validation: {val_err}"
                ) from val_err

            sensor_results: list[SensorResult] = []
            if sensors:
                async with tracer.start_span("sensor.pipeline", kind="sensor"):
                    sensor_results = await SensorPipeline(sensors).run(
                        validated_obj,
                        context={
                            "tool_sequence": tools_called,
                            "tool_results": tool_results,
                            **(eval_context or {}),
                        },
                    )

            return WorkflowRunResult(
                workflow_id=pack.id,
                topology=pack.manifest.topology,
                entrypoint_agent=blueprint.name,
                workspace_mode=blueprint.workspace_mode,
                provider=last_resp.provider,
                model_name=last_resp.model_name,
                output=validated_obj.model_dump(),
                validated_output=validated_obj,
                contract_name=contract_schema.__name__,
                turns_executed=turns_executed,
                shifts_executed=shifts_executed,
                tools_called=tools_called,
                tool_results=tool_results,
                sensor_results=sensor_results,
                input_tokens=budget_tracker.total_input_tokens,
                output_tokens=budget_tracker.total_output_tokens,
                total_tokens=budget_tracker.total_tokens,
                estimated_cost_usd=budget_tracker.total_cost_usd,
                metadata={
                    "tokens_shielded": partitioner._total_tokens_consumed,
                    "turn_prompts": turn_prompts_log,
                },
            )

    async def _run_evaluator_optimizer(
        self,
        *,
        pack: WorkflowPack,
        blueprint: AgentBlueprint,
        sys_prompt: str,
        input_query: str,
        adapter: ModelAdapter,
        contract_schema: type[BaseModel],
        tool_registry: ToolRegistry,
        sensors: list[Sensor],
        budget_tracker: BudgetTracker,
        stall_detector: StallDetector,
        effective_max_turns: int,
        tracer: AirframeTracer,
        work_dir: Path,
        shift_engine: ShiftTurnoverEngine | None = None,
        state_manager: DiskStateManager | None = None,
        approval_callback: Callable[[str], bool] | None = None,
        eval_context: dict[str, Any] | None = None,
    ) -> WorkflowRunResult:
        """Execute workflow using EvaluatorOptimizerLoop with SchemaSensor + workflow sensors."""
        pipeline_sensors: list[Sensor] = [ExecutableGuard(schema=contract_schema).to_sensor(), *sensors]
        pipeline = SensorPipeline(pipeline_sensors)
        max_iters = max(1, min(effective_max_turns, 5))
        loop = EvaluatorOptimizerLoop(sensor_pipeline=pipeline, max_iterations=max_iters)

        last_run: WorkflowRunResult | None = None
        total_turns = 0
        all_tools: list[str] = []
        all_tool_results: list[dict[str, Any]] = []

        async def generator_fn(prompt: str, critique: str | None) -> str:
            nonlocal last_run, total_turns
            iter_prompt = (
                prompt
                if critique is None
                else f"{prompt}\n\n[EVALUATOR CRITIQUE - FIX THESE ISSUES]\n{critique}"
            )
            run_res = await self._run_single_agent(
                pack=pack,
                blueprint=blueprint,
                sys_prompt=sys_prompt,
                input_query=iter_prompt,
                adapter=adapter,
                contract_schema=contract_schema,
                tool_registry=tool_registry,
                sensors=[],
                budget_tracker=budget_tracker,
                stall_detector=stall_detector,
                effective_max_turns=effective_max_turns,
                tracer=tracer,
                work_dir=work_dir,
                shift_engine=shift_engine,
                state_manager=state_manager,
                approval_callback=approval_callback,
                eval_context=eval_context,
            )
            last_run = run_res
            total_turns += run_res.turns_executed
            all_tools.extend(run_res.tools_called)
            all_tool_results.extend(run_res.tool_results)
            return json.dumps(run_res.output)

        async with tracer.start_span("orchestration.evaluator_optimizer", kind="supervisor"):
            opt_result = await loop.run(
                initial_prompt=input_query,
                generator_fn=generator_fn,
                context=eval_context,
            )

        if last_run is None or not opt_result.passed:
            critique = opt_result.history[-1].critique if opt_result.history else "Verification failed"
            raise GuardViolationError(
                f"Evaluator-Optimizer failed verification for '{pack.id}' after "
                f"{opt_result.iterations} iteration(s):\n{critique}"
            )

        validated_obj = contract_schema.model_validate_json(opt_result.final_candidate)
        final_sensors = opt_result.history[-1].sensor_results if opt_result.history else []

        return WorkflowRunResult(
            workflow_id=pack.id,
            topology=pack.manifest.topology,
            entrypoint_agent=blueprint.name,
            workspace_mode=blueprint.workspace_mode,
            provider=last_run.provider,
            model_name=last_run.model_name,
            output=validated_obj.model_dump(),
            validated_output=validated_obj,
            contract_name=contract_schema.__name__,
            turns_executed=total_turns,
            shifts_executed=last_run.shifts_executed,
            tools_called=all_tools,
            tool_results=all_tool_results,
            sensor_results=final_sensors,
            input_tokens=budget_tracker.total_input_tokens,
            output_tokens=budget_tracker.total_output_tokens,
            total_tokens=budget_tracker.total_tokens,
            estimated_cost_usd=budget_tracker.total_cost_usd,
            metadata={"iterations": opt_result.iterations},
        )

    async def _run_orchestrator_workers(
        self,
        *,
        pack: WorkflowPack,
        entrypoint_bp: AgentBlueprint,
        blueprints_by_name: dict[str, AgentBlueprint],
        sys_prompt: str,
        input_query: str,
        adapter: ModelAdapter,
        contract_schema: type[BaseModel],
        tool_registry: ToolRegistry,
        sensors: list[Sensor],
        budget_tracker: BudgetTracker,
        stall_detector: StallDetector,
        effective_max_turns: int,
        tracer: AirframeTracer,
        work_dir: Path,
        state_manager: DiskStateManager | None = None,
        approval_callback: Callable[[str], bool] | None = None,
        eval_context: dict[str, Any] | None = None,
        extra_subtasks: list[SubtaskSpec] | None = None,
    ) -> WorkflowRunResult:
        """Execute multi-agent orchestrator-workers topology via SupervisorAgent and handoff synthesis."""
        turns_executed = 0
        worker_blueprints = [name for name in blueprints_by_name if name != entrypoint_bp.name]
        default_worker_name = worker_blueprints[0] if worker_blueprints else entrypoint_bp.name

        if extra_subtasks is not None:
            subtasks = list(extra_subtasks)
        else:
            plan_prompt = (
                f"Decompose this goal into atomic subtasks for worker agents "
                f"(available worker blueprints: {worker_blueprints or [entrypoint_bp.name]}):\n\n"
                f"{input_query}"
            )
            plan_est_tokens, plan_est_cost = _estimate_contract_call_budget(
                prompt=plan_prompt,
                system_prompt=sys_prompt,
                budget_tracker=budget_tracker,
            )
            plan_reservation = budget_tracker.reserve(
                estimated_tokens=plan_est_tokens,
                estimated_cost_usd=plan_est_cost,
                label=f"orchestrator.plan.{entrypoint_bp.name}",
            )
            try:
                async with tracer.start_span("orchestrator.plan", kind="model"):
                    plan_obj, plan_resp = await adapter.invoke_contract(
                        prompt=plan_prompt,
                        schema=OrchestratorPlan,
                        system_prompt=sys_prompt,
                        max_retries=2,
                    )
            except BaseException:
                budget_tracker.refund(plan_reservation)
                raise

            turns_executed += 1
            budget_tracker.reconcile(
                plan_reservation,
                input_tokens=plan_resp.usage.input_tokens,
                output_tokens=plan_resp.usage.output_tokens,
                cost_usd=plan_resp.usage.estimated_cost_usd,
            )
            subtasks = [
                SubtaskSpec(
                    task_id=s.task_id,
                    description=s.description,
                    assigned_blueprint=s.assigned_blueprint,
                )
                for s in plan_obj.subtasks
            ]
            if not subtasks:
                subtasks = [
                    SubtaskSpec(
                        task_id="subtask-1",
                        description=input_query,
                        assigned_blueprint=default_worker_name,
                    )
                ]

        supervisor = SupervisorAgent(
            supervisor_id=entrypoint_bp.name,
            state_manager=state_manager,
            max_depth=self.max_recursion_depth,
            tracer=tracer,
            budget_tracker=budget_tracker,
        )

        worker_tools: list[str] = []
        worker_tool_results: list[dict[str, Any]] = []

        async def worker_runner(subtask: SubtaskSpec) -> CompressedHandoff:
            nonlocal turns_executed
            w_bp = blueprints_by_name.get(subtask.assigned_blueprint, entrypoint_bp)
            w_prompt = resolve_blueprint_prompt(pack, w_bp)
            w_partitioner = ContextPartitioner(agent_id=f"worker_{subtask.task_id}")
            depth = int(subtask.input_payload.get("depth", 1)) if isinstance(subtask.input_payload, dict) else 1
            supervisor.recursion_limiter.check_depth(depth)

            w_res = await self._run_single_agent(
                pack=pack,
                blueprint=w_bp,
                sys_prompt=w_prompt,
                input_query=subtask.description,
                adapter=adapter,
                contract_schema=WorkerStepReport,
                tool_registry=tool_registry,
                sensors=[],
                budget_tracker=budget_tracker,
                stall_detector=stall_detector,
                effective_max_turns=effective_max_turns,
                tracer=tracer,
                work_dir=work_dir,
                approval_callback=approval_callback,
                context_partitioner=w_partitioner,
                allow_cwd_switch=False,
            )
            turns_executed += w_res.turns_executed
            worker_tools.extend(w_res.tools_called)
            worker_tool_results.extend(w_res.tool_results)

            return w_partitioner.compress_handoff(
                final_output=json.dumps(w_res.output),
                status="SUCCESS",
                final_output_tokens=ContextBudgeter.estimate_tokens(json.dumps(w_res.output)),
            )

        sup_res = await supervisor.run_plan(
            goal=input_query,
            subtasks=subtasks,
            worker_runner=worker_runner,
            depth=1,
        )

        handoff_text = "\n".join(
            f"- {h.agent_id} [{h.status}]: {h.final_output}"
            for h in sup_res.completed_subtasks
        )
        synth_prompt = (
            f"Original goal: {input_query}\n\n"
            f"Synthesize the following worker handoffs into final contract '{contract_schema.__name__}':\n"
            f"{handoff_text}"
        )
        synth_est_tokens, synth_est_cost = _estimate_contract_call_budget(
            prompt=synth_prompt,
            system_prompt=sys_prompt,
            budget_tracker=budget_tracker,
        )
        synth_reservation = budget_tracker.reserve(
            estimated_tokens=synth_est_tokens,
            estimated_cost_usd=synth_est_cost,
            label=f"orchestrator.synthesize.{entrypoint_bp.name}",
        )
        try:
            async with tracer.start_span("orchestrator.synthesize", kind="model"):
                final_obj, synth_resp = await adapter.invoke_contract(
                    prompt=synth_prompt,
                    schema=contract_schema,
                    system_prompt=sys_prompt,
                    max_retries=2,
                )
        except BaseException:
            budget_tracker.refund(synth_reservation)
            raise

        turns_executed += 1
        budget_tracker.reconcile(
            synth_reservation,
            input_tokens=synth_resp.usage.input_tokens,
            output_tokens=synth_resp.usage.output_tokens,
            cost_usd=synth_resp.usage.estimated_cost_usd,
        )

        sensor_results: list[SensorResult] = []
        if sensors:
            async with tracer.start_span("sensor.pipeline", kind="sensor"):
                sensor_results = await SensorPipeline(sensors).run(
                    final_obj,
                    context={
                        "tool_sequence": worker_tools,
                        "tool_results": worker_tool_results,
                        **(eval_context or {}),
                    },
                )

        return WorkflowRunResult(
            workflow_id=pack.id,
            topology=pack.manifest.topology,
            entrypoint_agent=entrypoint_bp.name,
            workspace_mode=entrypoint_bp.workspace_mode,
            provider=synth_resp.provider,
            model_name=synth_resp.model_name,
            output=final_obj.model_dump(),
            validated_output=final_obj,
            contract_name=contract_schema.__name__,
            turns_executed=turns_executed,
            shifts_executed=1,
            tools_called=worker_tools,
            tool_results=worker_tool_results,
            sensor_results=sensor_results,
            input_tokens=budget_tracker.total_input_tokens,
            output_tokens=budget_tracker.total_output_tokens,
            total_tokens=budget_tracker.total_tokens,
            estimated_cost_usd=budget_tracker.total_cost_usd,
            metadata={
                "subtasks_completed": len(sup_res.completed_subtasks),
                "failed_subtasks": sup_res.failed_subtasks,
                "tokens_shielded": sup_res.total_tokens_shielded,
            },
        )
