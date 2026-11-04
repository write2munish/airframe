# ✈️ Airframe

> **The engineering chassis and operating system for reliable, long-horizon AI agents and swappable model engines.**

$$\text{Agent} = \text{Engine (Model)} + \text{Airframe (Harness)}$$

---

## Overview

In aerospace engineering, an **airframe** is the complete structural aircraft—including aerodynamics, flight controls, avionics, sensors, and structural safety barriers—built to operate independently of any specific propulsion unit. An airframe can mount a Pratt & Whitney, Rolls-Royce, or GE engine without changing the cockpit controls or safety tolerances.

**Airframe** brings this exact paradigm to AI engineering. Beyond single-model evaluation, Airframe acts as an **Agent Operating System** designed for:
* **Swappable Model Engines**: Decoupled adapters for Gemini, Claude, OpenAI, and local OSS models.
* **Long-Horizon Autonomy**: Multi-shift execution and disk-externalized state (`plan.md`, `task_queue.json`).
* **Multi-Agent Topologies**: Supervisor/Worker fleets with independent Evaluator-Optimizer loops.
* **Dual-Layer Isolation**: Context window partitioning (discarding noisy scratch tokens) + Git Worktree filesystem sandboxing.
* **Self-Contained Workflow Packs**: Colocated domain packs under `workflows/<use-case>/`.

---

## Core Architectural Pillars

* **Guides Before, Sensors After**:
  * **Guides (Feedforward)**: Task contracts, canonical prompt profiles, and lean context diet to direct the model before execution without causing rule fatigue.
  * **Sensors (Feedback)**: Automated AST linters, type checks, schema validators, and deterministic test gates to detect and catch errors after execution.
* **Dual-Layer Isolation**:
  * **Context Window Isolation**: Sub-agents run with private transcripts; 50k+ tokens of intermediate exploration are discarded from the supervisor's active context.
  * **Filesystem Isolation (Git Worktrees)**: Parallel workers run in dedicated `git worktree` scratch directories, preventing file-clobbering and commit collisions.
* **Supervisor / Worker Topologies**:
  * Decouples high-level strategic planning from low-level tool execution. Includes independent **Evaluator-Optimizer** loops to eliminate self-evaluation bias.
* **Long-Horizon Persistence & Multi-Shift Durability**:
  * State is externalized to persistent disk files (`plan.md`, `task_queue.json`, `progress.log`).
  * Automated **shift turnover** wipes bloated context windows and reboots fresh sessions seamlessly from disk.
* **Executable Guards Over Prose Rules**:
  * Hard programmatic boundaries (Pydantic v2 schemas, automated repair loops, permission sandboxes, token/byte ceilings) rather than soft prompt suggestions.
* **Governance & Circuit Breakers**:
  * Recursion depth ceilings, dollar/token budget limits, loop/stall detectors, and human-in-the-loop approval gates.

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
├── README.md
├── REQUIREMENTS.md
├── pyproject.toml
│
├── workflows/                        # Self-contained use-case packs
│   ├── pr-reviewer/                  # Example workflow
│   └── feature-builder/              # Example workflow
│
├── src/
│   └── airframe/
│       ├── adapters/                 # Swappable model engines (Gemini, Claude, OpenAI, Ollama)
│       ├── blueprints/               # Agent manifests & schema validation (FR-8)
│       ├── orchestration/            # Supervisor, topologies, shift turnover (FR-10, FR-11)
│       ├── isolation/                # Git worktree managers, transcript partitioners (FR-9)
│       ├── ipc/                      # Async mailbox, event bus, reactive wakeups (FR-12)
│       ├── guards/                   # Executable guards, Pydantic schemas, repair loops (FR-2)
│       ├── sensors/                  # Tier 1, 2, and 3 evaluators (FR-3)
│       ├── tools/                    # Tool registry, dynamic injector (FR-4)
│       ├── governance/               # Budget trackers, loop detectors, HITL gates (FR-13)
│       ├── state/                    # State externalization (plan.md, task_queue.json) (FR-11)
│       ├── telemetry/                # OpenTelemetry traces, session replay (FR-14)
│       ├── runner/                   # Core async execution engine
│       └── cli.py                    # CLI (`airframe run`, `airframe compare`, `airframe init`)
│
├── tests/
│   ├── unit/                         # Unit tests for airframe modules
│   └── integration/                  # End-to-end multi-agent & worktree tests
└── datasets/
    ├── smoke.yaml                    # Global engine sanity checks
    └── benchmark_core.yaml           # Global provider benchmark suite
```

---

## Detailed Specifications & Planning

* **[REQUIREMENTS.md](REQUIREMENTS.md)**: Full functional requirements (FR-1 through FR-15) and non-functional requirements (NFR-1 through NFR-5).
* **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)**: 6-phase engineering roadmap, milestone deliverables, and critical path.
