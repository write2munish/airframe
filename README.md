# ✈️ Airframe

> **The engineering chassis and operating system for reliable, long-horizon AI agents and swappable model engines.**

$$\text{Agent} = \text{Engine (Model)} + \text{Airframe (Harness)}$$

---

## Overview

In aerospace engineering, an **airframe** is the complete structural aircraft—including aerodynamics, flight controls, avionics, sensors, and structural safety barriers—built to operate independently of any specific propulsion unit. An airframe can mount a Pratt & Whitney, Rolls-Royce, or GE engine without changing the cockpit controls or safety tolerances.

**Airframe** brings this exact paradigm to AI engineering. Beyond single-model evaluation, Airframe acts as an **Agent Operating System** designed for multi-hour autonomous tasks, dynamic sub-agent fleets, strict context and filesystem isolation, and deterministic execution guards.

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

## Project Structure

```
airframe/
├── README.md
├── REQUIREMENTS.md
├── pyproject.toml
├── src/
│   └── airframe/
│       ├── adapters/         # Swappable model engines (Gemini, Claude, OpenAI, Ollama)
│       ├── blueprints/       # Agent manifests, schemas, and dynamic definitions
│       ├── orchestration/    # Supervisor, worker pool, topologies, and shift turnover
│       ├── isolation/        # Git worktree managers, context partitioners, sandboxes
│       ├── ipc/              # Asynchronous event bus, mailbox, reactive wakeups
│       ├── guards/           # Executable guards, Pydantic schemas, repair loops
│       ├── sensors/          # Tier 1 deterministic, Tier 2 semantic, Tier 3 judges
│       ├── tools/            # Scoped tool registries and dynamic injectors
│       ├── governance/       # Budget trackers, loop detectors, HITL approval gates
│       ├── state/            # Checkpoints, disk state (plan.md, task_queue.json)
│       ├── telemetry/        # Distributed tracing, transcript loggers, replay
│       ├── runner/           # Async execution engine, concurrency
│       └── cli.py            # Command-line interface (`airframe run`, `airframe compare`)
├── tests/                    # Unit and integration test suites
└── datasets/                 # Declarative YAML/JSON test suites and golden sets
```

---

## Detailed Specifications

See **[REQUIREMENTS.md](REQUIREMENTS.md)** for the complete requirements breakdown (FR-1 through FR-14, NFR-1 through NFR-5).
