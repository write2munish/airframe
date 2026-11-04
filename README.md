# `model-harness`

> **A contract-first evaluation and execution harness for swappable LLMs and agentic workflows.**

$$\text{Agent} = \text{Model} + \text{Harness}$$

---

## Overview

`model-harness` is an engineering chassis designed to decouple foundation models from agentic and application logic. Based on modern **Harness Engineering** principles, it treats models as interchangeable engines while providing the necessary steering, brakes, suspension, and diagnostic sensors to ensure production reliability.

---

## Core Architectural Pillars

* **Guides Before, Sensors After**:
  * **Guides (Feedforward)**: Task contracts, canonical prompt profiles, and lean context diet.
  * **Sensors (Feedback)**: Automated AST linters, type checks, schema validators, and deterministic test gates.
* **Executable Guards Over Prose Rules**:
  * Hard programmatic barriers (Pydantic v2 schemas, automated repair loops, permission sandboxes) rather than soft prompt suggestions.
* **Dynamic Tool Scoping & Context Diet**:
  * Prevents rule fatigue and parameter hallucinations by exposing only the 3–5 tools relevant to the active execution phase.
* **Durable State & Reversible Checkpoints**:
  * Step-level state tracking and rollback capability so failures don't trigger cascading amnesia or costly restarts.
* **Differential Regression Testing**:
  * Compare candidate model runs directly against golden baselines to detect regressions in output contracts, latency, and token cost.

---

## Project Structure

```
model-harness/
├── README.md
├── REQUIREMENTS.md
├── pyproject.toml
├── src/
│   └── harness/
│       ├── adapters/         # Swappable model engines (Gemini, Claude, OpenAI, Ollama)
│       ├── guards/           # Executable guards, Pydantic schemas, repair loops
│       ├── sensors/          # Tier 1 deterministic, Tier 2 semantic, Tier 3 judges
│       ├── tools/            # Scoped tool registries and dynamic injectors
│       ├── state/            # Checkpoints, session logs, rollback handlers
│       ├── runner/           # Async execution engine, concurrency, telemetry
│       ├── storage/          # Baselines, caches, and report generators
│       └── cli.py            # Command-line interface
├── tests/                    # Unit and integration test suites
└── datasets/                 # Declarative YAML/JSON test suites and golden sets
```

---

## Detailed Specifications

See **[REQUIREMENTS.md](REQUIREMENTS.md)** for full functional and non-functional requirements.
