# ✈️ Airframe

> **The engineering chassis and operating system for reliable, long-horizon AI agents and swappable model engines.**

$$\text{Agent} = \text{Engine (Model)} + \text{Airframe (Harness)}$$

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/write2munish/airframe/actions/workflows/ci.yml/badge.svg)](https://github.com/write2munish/airframe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Overview

In aerospace engineering, an **airframe** is the complete structural aircraft—including aerodynamics, flight controls, avionics, sensors, and structural safety barriers—built to operate independently of any specific propulsion unit. An airframe can mount a Pratt & Whitney, Rolls-Royce, or GE engine without changing the cockpit controls or safety tolerances.

**Airframe** brings this exact paradigm to AI engineering. Beyond single-model evaluation, Airframe acts as an **Agent Operating System** designed for:
* **Swappable Model Engines**: Decoupled adapters for Google Gemini, Anthropic Claude, OpenAI, and local OSS models (Ollama, vLLM).
* **Executable Guards Over Prose Rules**: Programmatic Pydantic v2 schema enforcement and automated self-repair loops.
* **Guides Before, Sensors After**: Multi-tier sensor pipeline (Tier 1 deterministic, Tier 2 semantic, Tier 3 LLM judge) with fast-fail short-circuiting to save token costs.
* **Unified Feedback Loop & Contract Repair**: Canonical `run_with_retry` / `FeedbackLoop` primitive where executable schema contracts operate as standard Tier 1 `SchemaSensor` components.
* **Unified Workspace Isolation Dispatcher**: Single `get_workspace(mode, agent_id, repo_path)` factory supporting `"inherit"` (shared), `"branch"` (Git worktree), and `"sandbox"` (ephemeral tempdir).
* **De-duped Governance & Lightweight Packs**: Single shared `Budget` model with `merge_with_override`, supporting both inline single-file workflows and full multi-agent directories.
* **Long-Horizon Autonomy**: Multi-shift execution and disk-externalized state (`plan.md`, `task_queue.json`).
* **Multi-Agent Topologies**: Supervisor/Worker fleets with independent Evaluator-Optimizer loops.
* **Dual-Layer Isolation**: Context window partitioning (discarding noisy exploration tokens) + Git Worktree filesystem sandboxing.
* **Self-Contained Workflow Packs**: Colocated domain packs under `workflows/<use-case>/`.

---

## ⚡ Installation & Quickstart

### 1. Install (Python 3.10+ & Git)

```bash
git clone https://github.com/write2munish/airframe.git
cd airframe
pip install -e ".[dev]"   # or: uv pip install -e ".[dev]"
```

Set credentials only for the cloud provider(s) you plan to run live (local Ollama works out of the box at `http://localhost:11434/v1` with zero API keys):

```bash
export GEMINI_API_KEY="..."      # Google Gemini
export ANTHROPIC_API_KEY="..."   # Anthropic Claude
export OPENAI_API_KEY="..."      # OpenAI
```

### 2. Verify CLI

```bash
airframe --version
airframe info
```

### 3. 5-Minute Developer Showcase (0 API Keys Required)

Run every topology directly from the CLI and inspect live W3C execution span trees with `--trace` / `airframe trace` (see [docs/SHOWCASE.md](docs/SHOWCASE.md) for the full tour):

```bash
# Evaluator-Optimizer loop + AST safety sensor + immediate trace tree:
airframe run workflows/patch-verifier -i "Write solve(x: int) -> int" --mock --trace

# Orchestrator-Workers swarm + parallel tool delegation + trace tree:
airframe run workflows/code-auditor-swarm -i "Audit login.py for SQL injection" --mock --trace

# Replay the latest hierarchical span tree anytime (or export JSON via --json):
airframe trace

# Verify semantic assertions & custom workflow sensors offline:
airframe test workflows/patch-verifier --mock
airframe test workflows/code-auditor-swarm --mock

# Measure regression-gated playbook self-improvement on 6 real auth0/java-jwt bugs:
python3 workflows/java-jwt-triage/evolve.py
```

### 4. Run Automated Tests & Lint

```bash
# Run deterministic offline unit and integration suite (zero external API keys required):
PYTHONPATH=src pytest tests/unit tests/integration -v

# Run required Ruff checks (matches .github/workflows/ci.yml):
ruff check --select E9 src tests
ruff check --select E4,E7,E9,F,I src/airframe/cli.py tests/unit/test_budget_concurrency.py tests/unit/test_workflows_and_diff.py

# Run opt-in live vendor adapter smoke tests (requires AIRFRAME_LIVE_TESTS=1 + keys / Ollama):
AIRFRAME_LIVE_TESTS=1 PYTHONPATH=src pytest tests/live -v
```

---

## 🚀 30-Second Code Example

### Swappable Engine with Executable Contract & Self-Repair

```python
import asyncio
from pydantic import BaseModel, Field
from airframe.adapters import get_adapter
from airframe.sensors import PythonASTSensor, RegexSensor, SensorPipeline

# 1. Define your canonical contract schema
class GeneratedFunction(BaseModel):
    function_name: str
    docstring: str
    code: str = Field(description="Executable Python code snippet.")

async def main():
    # 2. Pick any engine: 'gemini', 'anthropic', 'openai', or 'ollama'
    # Swapping the engine requires changing just this one line!
    engine = get_adapter(provider="gemini", model_name="gemini-2.0-flash")

    # 3. Invoke with automatic schema validation and self-repair loop
    output, resp = await engine.invoke_contract(
        prompt="Write a Python function to calculate the Fibonacci sequence.",
        schema=GeneratedFunction,
    )
    print(f"Generated function '{output.function_name}' ({resp.latency_ms}ms)")

    # 4. Run through multi-tier sensors (Tier 1 deterministic AST check)
    pipeline = SensorPipeline([
        PythonASTSensor(),  # Validates generated code compiles to valid Python AST
        RegexSensor(name="no_eval", pattern=r"\beval\(", must_match=False), # Security guard
    ])
    results = await pipeline.run(output.code)
    for res in results:
        print(f"[{'PASS' if res.passed else 'FAIL'}] Tier {res.tier} {res.sensor_name}: {res.message}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🗺️ Roadmap & Implementation Status

| Milestone | Subsystem | Key Capabilities | Verification & Status |
| :---: | :--- | :--- | :--- |
| **Phase 1** | **Core Engine & Adapters** | Universal `ModelAdapter`, telemetry, Gemini/Claude/OpenAI/Ollama adapters, Jinja2 prompt profiler, CLI | ✅ **Completed** ([`test_adapters.py`](tests/unit/test_adapters.py), opt-in [`test_live_adapters.py`](tests/live/test_live_adapters.py)) |
| **Phase 2** | **Guards & Sensor Pipeline** | `ExecutableGuard` (Pydantic v2), byte/line ceilings, Tier 1–3 sensors, fast-fail short-circuiting, `ToolRegistry` / phase scoping / `ToolRunner` | ✅ **Completed** ([`test_guards_and_sensors.py`](tests/unit/test_guards_and_sensors.py), [`test_tools.py`](tests/unit/test_tools.py)) |
| **Phase 3** | **Dual-Layer Isolation** | `AgentBlueprint` manifests, locked `WorktreeManager` with `SIGKILL`/`SIGINT`/`SIGTERM` compensation & `WorktreeConflictError`, `ContextPartitioner` (`fsync` + `.corrupt_tail` recovery) | ✅ **Completed** ([`test_blueprints.py`](tests/unit/test_blueprints.py), [`test_isolation.py`](tests/unit/test_isolation.py)) · Container mode (`workspace_mode="container"`) **Deferred (per [ADR 0001](docs/adr/0001-workflow-vs-container-isolation.md))** |
| **Phase 4** | **Multi-Agent, IPC & Runner** | Async typed `EventBus`, `SupervisorAgent` with `RecursionLimiter`, `EvaluatorOptimizerLoop`, `WorkflowRunner` (`single-agent`, `orchestrator-workers`, `evaluator-optimizer`), `AirframeTracer` W3C JSONL trace trees | ✅ **Completed** ([`test_orchestration.py`](tests/unit/test_orchestration.py), [`test_telemetry_tracer.py`](tests/unit/test_telemetry_tracer.py), [`test_runtime_runner.py`](tests/integration/test_runtime_runner.py)) · `router` topology **Not Implemented** |
| **Phase 5** | **Long-Horizon Shifts & Governance** | Crash-safe `DiskStateManager` (`atomic_write_text`, re-entrant `flock`, `.bak` recovery), `ShiftTurnoverEngine` with git checkpointing & history wipe, `BudgetTracker`, `StallDetector`, `HumanApprovalGate`, `max_turns` & `timeout_seconds` | ✅ **Completed** ([`test_long_horizon_and_governance.py`](tests/unit/test_long_horizon_and_governance.py), [`test_system_integration.py`](tests/integration/test_system_integration.py), [`test_runtime_runner.py`](tests/integration/test_runtime_runner.py)) |
| **Phase 6** | **Workflow Packs & CLI Diff Engine**| `WorkflowPack` (`load_tools`, `load_sensors`, `entrypoint_agent`), `airframe run`/`test`/`compare`/`trace`/`init`, semantic `expected` assertions & custom sensor gates, differential scorecard comparator | ✅ **Completed** ([`test_cli.py`](tests/unit/test_cli.py), [`test_workflows_and_diff.py`](tests/unit/test_workflows_and_diff.py), [`test_weather_forecaster.py`](tests/unit/test_weather_forecaster.py), [`test_showcase_workflows.py`](tests/unit/test_showcase_workflows.py)) |

---

## Adding a New Workflow (Workflow Packs)

Airframe follows a **Workflow Pack** pattern. To add a new use case, create a self-contained directory under `workflows/<use-case-name>/`:

```
workflows/<use-case-name>/
├── workflow.yaml                # Master manifest: topology, agents, budget
├── blueprints/                  # Agent blueprints (supervisor, workers, evaluators)
│   ├── supervisor.yaml
│   ├── worker.yaml
│   └── evaluator.yaml
├── guides/                      # Task instructions & prompt templates
│   ├── task_spec.md
│   └── prompt.jinja2
├── guards/                      # Pydantic v2 output schemas & contracts
│   └── contracts.py
├── sensors/                     # Tier 1, 2, and 3 verification checks
│   ├── deterministic.py
│   └── rubric.yaml
├── tools/                       # Scoped tools / MCP connectors
│   └── custom_tools.py
└── testbed/                     # Smoke tests and golden regression cases
    ├── smoke.yaml
    └── benchmark.yaml
```

### CLI Workflow Commands

```bash
# Execute a workflow end-to-end in deterministic offline mode with contract & sensor validation
airframe run workflows/calculator -i "Calculate compound interest on ₹10,000 at 5% for 3 years" --mock

# Execute in deterministic dry-run verification mode and render hierarchical trace tree
airframe run workflows/weather-forecaster -i "Will it rain in Bengaluru tomorrow at 3 PM?" --mock --trace

# Inspect latest trace tree (or pass trace_id / file path / --json)
airframe trace

# Run local regression testbed offline (pass --live to enforce provider API keys)
airframe test workflows/pr-reviewer --mock

# Benchmark model swap (pass --mock offline, or set keys for both engines to compare live)
airframe compare workflows/pr-reviewer \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet \
  --mock

# Scaffold a minimal, single-file workflow (lightweight mode, no empty subdirectories)
airframe init workflow my-task --light

# Scaffold a full multi-agent workflow pack
airframe init workflow customer-support
```

---

## Complete Repository Structure

```
airframe/
├── .github/workflows/ci.yml          # Required CI workflow (pytest + ruff)
├── CONTRIBUTING.md                   # Local checks, commit policy, correctness gates
├── README.md                         # Project overview and quickstart
├── pyproject.toml                    # Package metadata and dependencies
├── docs/
│   ├── SHOWCASE.md                   # 5-minute developer quickstart & CLI showcase guide
│   ├── WRITING_WORKFLOWS.md          # Step-by-step Workflow Pack authoring handbook
│   ├── EXTENDING.md                  # Plug-in guide: adapters, sensors, tools, workspaces, self-improvement
│   ├── ARCHITECTURE.md               # Architectural principles, FR-1..FR-15 verification matrix, NFRs
│   └── adr/                          # Architecture Decision Records (ADR 0001)
│
├── src/
│   └── airframe/
│       ├── adapters/                 # Swappable model engines (Gemini, Claude, OpenAI, Ollama)
│       ├── guards/                   # Executable guards, Pydantic schemas, repair loops
│       ├── sensors/                  # Tier 1 deterministic, Tier 2 semantic, Tier 3 judges
│       ├── blueprints/               # Agent manifests & schema validation (Phase 3)
│       ├── isolation/                # Locked Git worktree manager, transcript partitioner (Phase 3)
│       ├── orchestration/            # Supervisor, Evaluator-Optimizer, shift turnover (Phase 4, 5)
│       ├── ipc/                      # Async mailbox, event bus, cancellation cascades (Phase 4)
│       ├── tools/                    # Tool registry, phase scoper (max 5/phase), ToolRunner (FR-4)
│       ├── governance/               # Budget trackers, stall/recursion detectors, HITL gates (Phase 5)
│       ├── state/                    # Crash-safe state externalizer (`atomic_write_text`, `.bak`) (Phase 5)
│       ├── runner/                   # Canonical `WorkflowRunner` & ReAct loop runtime (FR-10..FR-13)
│       ├── telemetry/                # Hierarchical JSONL `AirframeTracer`, scorecards, diff reports (FR-14)
│       ├── workflows/                # WorkflowPack manifest, tool & sensor loader (FR-15)
│       └── cli.py                    # CLI (`airframe run`, `test`, `compare`, `trace`, `init`)
│
├── tests/
│   ├── unit/                         # Deterministic unit suites (`test_adapters.py`, `test_budget_concurrency.py`, `test_cli.py`, `test_isolation.py`, `test_java_jwt_triage.py`, etc.)
│   ├── integration/                  # End-to-end runtime, worktree crash recovery & governance suites (`test_system_integration.py`, `test_runtime_runner.py`)
│   └── live/                         # Opt-in live vendor HTTP smoke tests (`AIRFRAME_LIVE_TESTS=1`)
└── workflows/                        # Self-contained packs (`pr-reviewer`, `basic-calculator`, `calculator`, `weather-forecaster`, `java-jwt-triage`, `patch-verifier`, `code-auditor-swarm`)
```

---

## Documentation

* **[docs/SHOWCASE.md](docs/SHOWCASE.md)**: 5-minute developer quickstart and CLI workflow showcase guide.
* **[docs/WRITING_WORKFLOWS.md](docs/WRITING_WORKFLOWS.md)**: Step-by-step guide to creating `--light` and multi-agent Workflow Packs.
* **[docs/EXTENDING.md](docs/EXTENDING.md)**: Guide to extending adapters, tools, sensors, workspaces, topologies, traces, and self-improving loops.
* **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Core architectural principles, FR-1..FR-15 verification matrix, and NFRs.
* **[docs/adr/0001-workflow-vs-container-isolation.md](docs/adr/0001-workflow-vs-container-isolation.md)**: ADR 0001: Workflow-Level Isolation vs. Container-Based Isolation.
