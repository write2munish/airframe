# 🧩 Extending Airframe

Airframe separates **execution harnesses** from **model engines, tools, sensors, workspaces, and topologies**. Every subsystem exposes a narrow abstract base class or auto-discovery hook so you can extend Airframe without modifying runner internals.

---

## Quick Extension Index

| Extension Point | Base Class / Mechanism | Source Module |
| :--- | :--- | :--- |
| **Model Provider Adapter** | `ModelAdapter` + `get_adapter` / `resolve_adapter` | [`src/airframe/adapters/`](../src/airframe/adapters/__init__.py) |
| **Prompt Dialect** | `PromptProfiler.templates` | [`src/airframe/adapters/profiler.py`](../src/airframe/adapters/profiler.py) |
| **Workflow Tools (Zero-Decorator)** | Plain sync/async functions -> `ToolRegistry` | [`src/airframe/tools/registry.py`](../src/airframe/tools/registry.py) |
| **Deterministic / Semantic Sensors** | `Sensor` + `SensorPipeline` | [`src/airframe/sensors/base.py`](../src/airframe/sensors/base.py) |
| **Executable Contracts** | Pydantic v2 `BaseModel` | [`src/airframe/runner/engine.py`](../src/airframe/runner/engine.py) |
| **Workspace Isolation Strategy** | `WorkspaceStrategy` + `get_workspace` | [`src/airframe/isolation/workspace.py`](../src/airframe/isolation/workspace.py) |
| **Orchestration Topology** | `WorkflowManifest.topology` + `WorkflowRunner` | [`src/airframe/runner/engine.py`](../src/airframe/runner/engine.py) |
| **Trace Consumers & Governance** | `load_trace`, `build_trace_tree`, `Budget`, `HumanApprovalGate` | [`src/airframe/telemetry/tracer.py`](../src/airframe/telemetry/tracer.py), [`src/airframe/governance/circuit_breakers.py`](../src/airframe/governance/circuit_breakers.py) |

---

## 1. Adding a Model Provider (`ModelAdapter`)

### Option A: Zero-Code Endpoint Override (OpenAI-Compatible)
Any endpoint speaking the OpenAI chat completions schema (vLLM, LM Studio, LiteLLM proxy, Azure gateway) works immediately via [`OpenAICompatibleAdapter`](../src/airframe/adapters/openai_compatible.py):

```python
from airframe.adapters import get_adapter

adapter = get_adapter(
    provider="openai_compatible",
    model_name="qwen2.5-coder:32b",
    base_url="http://localhost:11434/v1",
    api_key="local-key",
    timeout=120.0,
)
```

### Option B: Custom Vendor Class
Subclass [`ModelAdapter`](../src/airframe/adapters/base.py#L51-L100) and implement `provider_name` and `async invoke(...)`. You inherit Pydantic `invoke_contract` self-repair automatically:

```python
from typing import Any
from airframe.adapters.base import ModelAdapter, ModelResponse, UsageMetadata


class BedrockClaudeAdapter(ModelAdapter):
    @property
    def provider_name(self) -> str:
        return "bedrock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        # Call your SDK/HTTP client here and return normalized telemetry:
        raw_text = await my_bedrock_call(self.model_name, prompt, system_prompt)
        return ModelResponse(
            content=raw_text,
            model_name=self.model_name,
            provider=self.provider_name,
            usage=UsageMetadata(input_tokens=100, output_tokens=50, total_tokens=150),
            latency_ms=42.0,
        )
```

To expose it on the CLI (`airframe run --engine bedrock/claude-3-5-sonnet`), add a branch in [`get_adapter`](../src/airframe/adapters/__init__.py#L26-L86) and [`resolve_adapter`](../src/airframe/adapters/__init__.py#L89-L118).

### Extending Prompt Dialects (`PromptProfiler`)
Register custom Jinja2 syntax templates per `provider_name` in [`PromptProfiler.templates`](../src/airframe/adapters/profiler.py#L43-L47).

---

## 2. Adding Zero-Decorator Tools & Governance

Place plain typed Python functions (sync or `async def`) inside `workflows/<pack>/tools.py` or `workflows/<pack>/tools/*.py`, or register them programmatically on [`ToolRegistry`](../src/airframe/tools/registry.py#L95-L188):

```python
import httpx


async def lookup_cve(cve_id: str, client: httpx.AsyncClient | None = None) -> dict:
    """Fetch essential CVE severity and summary fields (pruned for LLM context)."""
    # Note: 'client' has a non-JSON default (= None), so Airframe automatically hides it
    # from the LLM JSON schema while allowing clean dependency injection in tests!
    ...
    return {"cve_id": cve_id, "severity": "HIGH", "cvss": 8.6}
```

### Hard Guardrails Enforced by `ToolRunner` & `ScopedToolInjector`
1. **5-Tool Phase Ceiling:** [`ScopedToolInjector(max_tools_per_phase=5)`](../src/airframe/tools/scoper.py#L25-L71) raises `ToolScopeExceededError` if a phase tries to bind more than 5 tools.
2. **Tier 2 Argument Validation:** [`ToolRunner`](../src/airframe/tools/runner.py#L31-L139) checks tool whitelist membership and required params before execution.
3. **Human-in-the-Loop (HITL):** Destructive operations trigger `HumanApprovalGate`; pass `approval_callback=lambda action: ...` to `WorkflowRunner.run(...)` to integrate interactive terminal or Slack/UI approvals.
4. **Loop Protection:** Consecutive identical failures are tracked by `StallDetector`, raising `StallDetectedError` at `stall_threshold`.

---

## 3. Adding Custom Verification Sensors (`Sensor`)

Subclass [`Sensor`](../src/airframe/sensors/base.py#L21-L40) and choose the right tier:
* **Tier `1` (Deterministic):** Zero-cost AST, regex, math, exit-code, schema checks. **Any Tier 1 failure short-circuits Tier 2 and Tier 3** in [`SensorPipeline`](../src/airframe/sensors/base.py#L52-L96) to prevent wasted LLM judge tokens.
* **Tier `2` (Semantic / Sequence):** Tool sequence validators (`ToolSequenceSensor`), token Jaccard overlap (`TokenOverlapSensor`).
* **Tier `3` (LLM Judge):** `ModelJudgeSensor` with calibrated `JudgeRubric` at `temperature=0.0`.

```python
from typing import Any
from airframe.sensors.base import Sensor, SensorResult


class NoTodosSensor(Sensor):
    def __init__(self, allow_fixme: bool = False) -> None:
        self.allow_fixme = allow_fixme

    @property
    def name(self) -> str:
        return "no_todo_placeholders"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        text = str(candidate)
        if "TODO" in text or (not self.allow_fixme and "FIXME" in text):
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Unresolved TODO/FIXME marker detected in output.",
            )
        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message="Zero placeholder markers found.",
        )
```

> **Workflow Auto-Discovery Tip:** Any concrete `Sensor` subclass defined in `workflows/<pack>/sensors.py` or `sensors/*.py` is instantiated automatically by [`WorkflowPack.load_sensors(case_input=...)`](../src/airframe/workflows/loader.py#L138-L189). Required `__init__` parameters are matched by name against each test case's `input:` dictionary!

---

## 4. Adding Workspace Isolation Strategies (Including Future Container Mode)

To support a new workspace backend (for example, implementing `workspace_mode="container"` when your workload crosses the threshold documented in [ADR 0001](adr/0001-workflow-vs-container-isolation.md)):

1. Subclass [`WorkspaceStrategy`](../src/airframe/isolation/workspace.py#L21-L64) (`async def enter() -> Path` and `async def exit(...) -> None`).
2. Update `WorkspaceMode` in [`src/airframe/blueprints/schema.py`](../src/airframe/blueprints/schema.py#L13) and [`WorkflowManifest.workspace_mode`](../src/airframe/workflows/loader.py#L36).
3. Register the mode inside [`get_workspace`](../src/airframe/isolation/workspace.py#L201-L234):

```python
class DockerWorkspace(WorkspaceStrategy):
    def __init__(self, image: str, repo_path: str | Path) -> None:
        super().__init__(mode="container")
        self.image = image
        self.repo_path = Path(repo_path).resolve()

    async def enter(self) -> Path:
        # Start container with bind-mounted temp clone and return workspace path
        ...

    async def exit(self, exc_type=None, exc_val=None, exc_tb=None) -> None:
        # Stop/remove container cleanly on exit or signal
        ...
```

---

## 5. Adding a New Orchestration Topology (e.g., `router`)

[`WorkflowRunner`](../src/airframe/runner/engine.py#L189-L967) currently implements `"single-agent"`, `"orchestrator-workers"`, and `"evaluator-optimizer"`. To add `"router"` (intent classification -> specialist blueprint dispatch):

1. Extend `WorkflowManifest.topology` in [`src/airframe/workflows/loader.py`](../src/airframe/workflows/loader.py#L33-L35).
2. Add an `elif topology == "router":` branch in [`WorkflowRunner.run`](../src/airframe/runner/engine.py#L296-L360).
3. Implement `async def _run_router(...) -> WorkflowRunResult` reusing `budget_tracker`, `stall_detector`, `tracer.start_span(...)`, and `SensorPipeline`. Because timeout, trace root span, and workspace cleanup wrap `_execute_inside_span()`, your topology inherits all governance and cleanup guarantees automatically.

---

## 6. Consuming Trace Trees & Exporting OpenTelemetry

Every `WorkflowRunner` execution writes W3C-compatible spans line-by-line (with `fsync`) to `.airframe/traces/<trace_id>.jsonl`.

```python
from airframe.telemetry import load_trace, build_trace_tree

spans = load_trace(".airframe/traces/<trace_id>.jsonl")
tree = build_trace_tree(spans)  # {"trace_id": ..., "span_count": ..., "roots": [...]}
```

* **OpenTelemetry Mirroring:** Installing `opentelemetry-api` (with your configured OTLP SDK exporter) causes [`AirframeTracer`](../src/airframe/telemetry/tracer.py#L46-L52) to mirror every span start/end event automatically without changing any workflow code.
* **Custom Scorecards:** Feed `CaseResult` lists into `RunScorecard.from_results()` and `compare_scorecards()` in [`src/airframe/telemetry/diff.py`](../src/airframe/telemetry/diff.py) to gate CI deployments programmatically.
