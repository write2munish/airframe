# ✈️ Airframe

> **The engineering chassis for AI agents and swappable foundation models.**

$$\text{Agent} = \text{Engine (Model)} + \text{Airframe (Harness)}$$

---

## Overview

In aerospace engineering, an **airframe** is the complete structural aircraft—including aerodynamics, flight controls, avionics, sensors, and structural safety barriers—built to operate independently of any specific propulsion unit. An airframe can mount a Pratt & Whitney, Rolls-Royce, or GE engine without changing the cockpit controls or safety tolerances.

**Airframe** brings this exact paradigm to AI engineering. It decouples the underlying models from application logic, providing the guidance, executable guards, sensors, and telemetry necessary to hot-swap models (Gemini, Claude, GPT, Ollama/local models) with zero downstream turbulence or contract breakage.

---

## Core Architectural Pillars

* **Guides Before, Sensors After**:
  * **Guides (Feedforward)**: Task contracts, canonical prompt profiles, and lean context diet to direct the model before execution without causing rule fatigue.
  * **Sensors (Feedback)**: Automated AST linters, type checks, schema validators, and deterministic test gates to detect and catch errors after execution.
* **Executable Guards Over Prose Rules**:
  * Hard programmatic boundaries (Pydantic v2 schemas, automated repair loops, permission sandboxes, token/byte ceilings) rather than soft prompt suggestions.
* **Dynamic Tool Scoping & Context Diet**:
  * Prevents parameter hallucinations and decision paralysis by exposing only the 3–5 tools relevant to the active execution phase (e.g., Discovery vs. Execution vs. Verification).
* **Durable State & Reversible Checkpoints**:
  * Step-level session tracking and rollback handling so unexpected failures do not trigger cascading amnesia or costly restarts.
* **Differential Regression Testing**:
  * Run candidate model engines directly against golden baselines with automated diff scorecards to track contract conformity, latency, and token cost drift.

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
│       ├── guards/           # Executable guards, Pydantic schemas, repair loops
│       ├── sensors/          # Tier 1 deterministic, Tier 2 semantic, Tier 3 judges
│       ├── tools/            # Scoped tool registries and dynamic injectors
│       ├── state/            # Checkpoints, session logs, rollback handlers
│       ├── runner/           # Async execution engine, concurrency, telemetry
│       ├── storage/          # Baselines, caches, and report generators
│       └── cli.py            # Command-line interface (`airframe run`, `airframe compare`)
├── tests/                    # Unit and integration test suites
└── datasets/                 # Declarative YAML/JSON test suites and golden sets
```

---

## Detailed Specifications

See **[REQUIREMENTS.md](REQUIREMENTS.md)** for full functional and non-functional requirements.
