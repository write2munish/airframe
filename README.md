# ✈️ Airframe

> **The engineering chassis and operating system for reliable, long-horizon AI agents and swappable model engines.**

$$\text{Agent} = \text{Engine (Model)} + \text{Airframe (Harness)}$$

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests Passing](https://img.shields.io/badge/tests-40%20passed-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Overview

In aerospace engineering, an **airframe** is the complete structural aircraft—including aerodynamics, flight controls, avionics, sensors, and structural safety barriers—built to operate independently of any specific propulsion unit. An airframe can mount a Pratt & Whitney, Rolls-Royce, or GE engine without changing the cockpit controls or safety tolerances.

**Airframe** brings this exact paradigm to AI engineering. Beyond single-model evaluation, Airframe acts as an **Agent Operating System** designed for:
* **Swappable Model Engines**: Decoupled adapters for Google Gemini, Anthropic Claude, OpenAI, and local OSS models (Ollama, vLLM).
* **Executable Guards Over Prose Rules**: Programmatic Pydantic v2 schema enforcement and automated self-repair loops.
* **Guides Before, Sensors After**: Multi-tier sensor pipeline (Tier 1 deterministic, Tier 2 semantic, Tier 3 LLM judge) with fast-fail short-circuiting to save token costs.
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

### 3. Run Automated Tests (20/20 Passing)

```bash
PYTHONPATH=src pytest tests/ -v
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

| Milestone | Subsystem | Key Capabilities | Status |
| :---: | :--- | :--- | :---: |
| **Phase 1** | **Core Engine & Adapters** | Universal `ModelAdapter`, telemetry, Gemini/Claude/OpenAI/Ollama adapters, Jinja2 prompt profiler, CLI | ✅ **Completed** |
| **Phase 2** | **Guards & Sensor Pipeline** | `ExecutableGuard` (Pydantic v2), byte/line ceilings, Tier 1-3 sensors, fast-fail short-circuiting | ✅ **Completed** |
| **Phase 3** | **Dual-Layer Isolation** | `AgentBlueprint` manifests, `WorktreeManager` (Git worktrees), Context window partitioner | ✅ **Completed** |
| **Phase 4** | **Multi-Agent & IPC** | Async typed mailbox, Supervisor-Worker coordination, Evaluator-Optimizer loop, cancellation | ✅ **Completed** |
| **Phase 5** | **Long-Horizon Shifts** | State externalizer (`plan.md`), shift turnover engine, budget caps, loop/stall detectors | ✅ **Completed** |
| **Phase 6** | **Workflows & Diff Engine**| `workflows/` pack loader, differential regression comparator, `workflows/pr-reviewer` pack, CLI | ✅ **Completed** |

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
# Execute a workflow with input parameters
airframe run workflows/pr-reviewer --input pr_number=42

# Run local regression testbed
airframe test workflows/pr-reviewer

# Benchmark model swap (verify zero regressions when upgrading models)
airframe compare workflows/pr-reviewer \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet

# Scaffold a new workflow boilerplate
airframe init workflow customer-support
```

---

## Complete Repository Structure

```
airframe/
├── INSTALLATION.md                   # Installation & provider configuration guide
├── IMPLEMENTATION_PLAN.md            # 6-phase engineering roadmap
├── README.md                         # Project overview and quickstart
├── REQUIREMENTS.md                   # Complete architectural requirements (FR-1 to FR-15)
├── pyproject.toml                    # Package metadata and dependencies
│
├── src/
│   └── airframe/
│       ├── adapters/                 # Swappable model engines (Gemini, Claude, OpenAI, Ollama)
│       ├── guards/                   # Executable guards, Pydantic schemas, repair loops
│       ├── sensors/                  # Tier 1 deterministic, Tier 2 semantic, Tier 3 judges
│       ├── blueprints/               # Agent manifests & schema validation (Phase 3)
│       ├── isolation/                # Git worktree managers, transcript partitioners (Phase 3)
│       ├── orchestration/            # Supervisor, topologies, shift turnover (Phase 4, 5)
│       ├── ipc/                      # Async mailbox, event bus, reactive wakeups (Phase 4)
│       ├── tools/                    # Tool registry, dynamic injector
│       ├── governance/               # Budget trackers, loop detectors, HITL gates (Phase 5)
│       ├── state/                    # State externalization (plan.md, task_queue.json) (Phase 5)
│       ├── telemetry/                # OpenTelemetry traces, session replay
│       └── cli.py                    # CLI (`airframe info`, `airframe run`, `airframe compare`)
│
├── tests/
│   └── unit/
│       ├── test_adapters.py          # 8 adapter & contract tests (Passing)
│       ├── test_guards_and_sensors.py# 10 guard & sensor tests (Passing)
│       └── test_cli.py               # 2 CLI tests (Passing)
└── workflows/                        # Self-contained use-case packs
```

---

## Detailed Specifications & Planning

* **[INSTALLATION.md](INSTALLATION.md)**: Full setup instructions and model provider credentials guide.
* **[REQUIREMENTS.md](REQUIREMENTS.md)**: Full functional requirements (FR-1 through FR-15) and non-functional requirements (NFR-1 through NFR-5).
* **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**: 6-phase engineering roadmap, milestone deliverables, and critical path.
* **[SELF_IMPROVING_AGENTS.md](SELF_IMPROVING_AGENTS.md)**: Architectural guide and code patterns for building safe, verified self-improving agents.
