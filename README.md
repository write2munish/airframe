# ✈️ Airframe

> **The engineering chassis and operating system for reliable, long-horizon AI agents and swappable model engines.**

$$\text{Agent} = \text{Engine (Model)} + \text{Airframe (Harness)}$$

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests Passing](https://img.shields.io/badge/tests-79%20offline%20passed-brightgreen.svg)](tests/)
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

See the full [**Installation Guide (INSTALLATION.md)**](INSTALLATION.md) for detailed environment and provider setup.

### 1. Install via Pip

```bash
# Clone and install in editable mode with development dependencies:
git clone https://github.com/write2munish/airframe.git
cd airframe
pip install -e ".[dev]"
```

### 2. Verify CLI

```bash
airframe --version
airframe info
```

### 3. 5-Minute Developer Showcase (0 API Keys Required)

```bash
# Run the interactive 5-act showcase (contracts, scoped tools, SIGKILL worktree reap, shift turnover, W3C trace tree):
PYTHONPATH=src python3 examples/showcase_walkthrough.py

# Or run the multi-agent showcase packs directly via CLI:
airframe test workflows/patch-verifier --mock        # evaluator-optimizer + AST safety sensor
airframe test workflows/code-auditor-swarm --mock    # orchestrator-workers + parallel tool delegation
```

### 4. Run Automated Tests (79/79 Offline Passing)

```bash
# Run deterministic offline unit and integration suite (zero external API keys required):
PYTHONPATH=src pytest tests/unit tests/integration -v

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
from airframe.guards import ExecutableGuard, PayloadBounds
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
| **Phase 6** | **Workflow Packs & CLI Diff Engine**| `WorkflowPack` (`load_tools`, `load_sensors`, `entrypoint_agent`), `airframe run`/`test`/`compare`/`init`, semantic `expected` assertions & custom sensor gates, differential scorecard comparator | ✅ **Completed** ([`test_cli.py`](tests/unit/test_cli.py), [`test_workflows_and_diff.py`](tests/unit/test_workflows_and_diff.py), [`test_weather_forecaster.py`](tests/unit/test_weather_forecaster.py)) |

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
# Execute a workflow end-to-end with real model adapter and contract validation
airframe run workflows/calculator -i "Calculate compound interest on ₹10,000 at 5% for 3 years"

# Execute in deterministic dry-run verification mode (no API key required)
airframe run workflows/weather-forecaster -i "Will it rain in Bengaluru tomorrow at 3 PM?" --mock

# Run local regression testbed
airframe test workflows/pr-reviewer

# Benchmark model swap (verify zero regressions when upgrading models)
airframe compare workflows/pr-reviewer \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet

# Scaffold a minimal, single-file workflow (lightweight mode, no empty subdirectories)
airframe init workflow my-task --light

# Scaffold a full multi-agent workflow pack
airframe init workflow customer-support
```

---

## Complete Repository Structure

```
airframe/
├── INSTALLATION.md                   # Installation & provider configuration guide
├── IMPLEMENTATION_PLAN.md            # 6-phase engineering roadmap & verification matrix
├── README.md                         # Project overview and quickstart
├── REQUIREMENTS.md                   # Complete architectural requirements (FR-1 to FR-15)
├── pyproject.toml                    # Package metadata and dependencies
├── docs/
│   ├── SHOWCASE.md                   # 5-minute developer quickstart & 5-act showcase guide
│   ├── EXTENDING.md                  # Plug-in guide: adapters, sensors, tools, workspaces, topologies
│   ├── WRITING_WORKFLOWS.md          # Step-by-step Workflow Pack authoring handbook
│   ├── adr/                          # Architecture Decision Records (ADR 0001)
│   └── specs/                        # Remediation & architecture specifications
│
├── examples/
│   └── showcase_walkthrough.py       # One-command deterministic 5-act showcase script
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
│       └── cli.py                    # CLI (`airframe run`, `test`, `compare`, `init`)
│
├── tests/
│   ├── unit/                         # Deterministic unit suites (`test_adapters.py`, `test_cli.py`, `test_isolation.py`, `test_tools.py`, `test_telemetry_tracer.py`, etc.)
│   ├── integration/                  # End-to-end runtime, worktree crash recovery & governance suites (`test_system_integration.py`, `test_runtime_runner.py`)
│   └── live/                         # Opt-in live vendor HTTP smoke tests (`AIRFRAME_LIVE_TESTS=1`)
└── workflows/                        # Self-contained packs (`pr-reviewer`, `basic-calculator`, `calculator`, `weather-forecaster`, `patch-verifier`, `code-auditor-swarm`)
```

---

## Detailed Specifications & Planning

* **[docs/SHOWCASE.md](docs/SHOWCASE.md)**: 5-minute developer quickstart and interactive 5-act showcase guide.
* **[docs/EXTENDING.md](docs/EXTENDING.md)**: Complete guide to extending adapters, tools, sensors, workspaces, topologies, and traces.
* **[docs/WRITING_WORKFLOWS.md](docs/WRITING_WORKFLOWS.md)**: Step-by-step guide to creating `--light` and multi-agent Workflow Packs.
* **[INSTALLATION.md](INSTALLATION.md)**: Full setup instructions and model provider credentials guide.
* **[REQUIREMENTS.md](REQUIREMENTS.md)**: Full functional requirements (FR-1 through FR-15) and non-functional requirements (NFR-1 through NFR-5).
* **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**: 6-phase engineering roadmap, milestone deliverables, and critical path.
* **[SELF_IMPROVING_AGENTS.md](SELF_IMPROVING_AGENTS.md)**: Architectural guide and code patterns for building safe, verified self-improving agents.
* **[docs/adr/0001-workflow-vs-container-isolation.md](docs/adr/0001-workflow-vs-container-isolation.md)**: ADR 0001: Workflow-Level Isolation vs. Container-Based Isolation.
