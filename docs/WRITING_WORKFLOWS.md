# 🛠️ Writing New Airframe Workflows

Airframe packages every AI use case as an isolated, self-contained **Workflow Pack** under `workflows/<pack-name>/`. You never modify core engine code to ship a new agent workflow.

---

## 1. Choose Your Layout: `--light` vs. Multi-Agent

| Format | Best For | Scaffold Command |
| :--- | :--- | :--- |
| **Lightweight (`--light`)** | Focused `single-agent` or `evaluator-optimizer` packs with `<= 5` tools | `airframe init workflow <name> --light` |
| **Full Multi-Agent** | `orchestrator-workers` fleets, distinct specialist blueprints, longer specs | `airframe init workflow <name>` |

### Directory Layouts

```text
# Lightweight Pack (--light)                # Multi-Agent Pack
workflows/my-light-wf/                      workflows/my-swarm-wf/
├── workflow.yaml                           ├── workflow.yaml
├── contracts.py      (Pydantic output)     ├── blueprints/
├── tools.py          (plain functions)     │   ├── lead.yaml
├── sensors.py        (optional checks)     │   └── worker.yaml
└── testbed.yaml      (golden cases)        ├── guides/
                                            │   ├── lead_spec.md
                                            │   └── worker_spec.md
                                            ├── guards/contracts.py
                                            ├── sensors/*.py
                                            ├── tools/*.py
                                            └── testbed/smoke.yaml
```

Reference examples in this repo:
* Lightweight `single-agent`: [`workflows/basic-calculator`](../workflows/basic-calculator/workflow.yaml), [`workflows/weather-forecaster`](../workflows/weather-forecaster/workflow.yaml)
* Lightweight `evaluator-optimizer`: [`workflows/patch-verifier`](../workflows/patch-verifier/workflow.yaml)
* Full `orchestrator-workers`: [`workflows/code-auditor-swarm`](../workflows/code-auditor-swarm/workflow.yaml), [`workflows/pr-reviewer`](../workflows/pr-reviewer/workflow.yaml)

---

## 2. Step-by-Step Workflow Authoring Guide

### Step 1 — Scaffold the Directory

```bash
airframe init workflow sql-migrator --light
```

### Step 2 — Define the Executable Contract (`contracts.py`)

Write your Pydantic v2 schema first. This enforces exact field names, numeric bounds, and `Literal` enums via automated self-repair (`ExecutableGuard`):

```python
from typing import Literal
from pydantic import BaseModel, Field


class MigrationAuditReport(BaseModel):
    summary: str = Field(description="Brief safety verdict.")
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    table_locks_detected: int = Field(ge=0)
    passed: bool
```

> **Schema Discovery Rule:** Name your main output class ending in `Report`, `Contract`, `Result`, or `Output` (or set `schema_contract: "contracts:MigrationAuditReport"` in `workflow.yaml`) so [`load_contract_schema`](../src/airframe/runner/engine.py#L115-L156) binds it automatically.

### Step 3 — Write Context-Pruned Python Tools (`tools.py`)

Airframe discovers every public function in `tools.py` or `tools/*.py` **without decorators** (`ToolRegistry`). Keep tool return values concise (< 200 tokens) and whitelist at most `5` tools per phase:

```python
import re
from typing import Any


def inspect_ddl_locks(sql_text: str) -> dict[str, Any]:
    """Detect blocking PostgreSQL DDL operations such as unguarded ADD INDEX."""
    risky = bool(re.search(r"CREATE\s+INDEX(?!\s+CONCURRENTLY)", sql_text, re.I))
    return {
        "has_blocking_index": risky,
        "statement_count": len([s for s in sql_text.split(";") if s.strip()]),
    }
```

> **Dependency Injection Tip:** If a tool accepts an optional non-JSON client for offline testing (e.g., `client: httpx.AsyncClient | None = None`), Airframe automatically omits that parameter from the LLM tool schema.

### Step 4 — Author `workflow.yaml` (and `blueprints/*.yaml`)

```yaml
id: "sql-migrator"
version: "1.0.0"
description: "Checks DDL migrations for blocking table locks."
topology: "evaluator-optimizer"     # single-agent | evaluator-optimizer | orchestrator-workers
workspace_mode: "inherit"           # inherit | branch | sandbox
entrypoint_agent: "ddl-auditor"     # Must match blueprint name in multi-agent packs
engine_binding: "gemini-2.0-flash"
tools:
  - "inspect_ddl_locks"

system_prompt: |
  You are a PostgreSQL reliability specialist.
  Always inspect SQL via inspect_ddl_locks before answering.
  Emit MigrationAuditReport strictly.

governance:
  max_turns: 6
  timeout_seconds: 60
  stall_threshold: 2
  max_cost_usd: 0.10

testbed: "testbed.yaml"
```

* **Multi-Agent Entrypoint Rule:** When `agents:` lists multiple YAML files (e.g., `["blueprints/worker.yaml", "blueprints/lead.yaml"]`), set `entrypoint_agent` to the exact `name:` of the orchestrator/entry blueprint. [`WorkflowRunner`](../src/airframe/runner/engine.py#L206-L227) raises `ValueError` if missing rather than guessing `agents[0]`.
* **Worktree Rule (`workspace_mode: "branch"`):** Use `"branch"` for agents that edit repository code inside Git worktrees (`.airframe/worktrees/<agent_id>`), enabling shift git checkpoints (`airframe: shift N checkpoint`).
* **Context Diet Rule:** Keep `system_prompt` / `guides/*.md` concise (under 4,000 estimated tokens to avoid `ContextBudgeter` warnings).

### Step 5 — Add Deterministic Sensors (`sensors.py`)

Put zero-cost verification checks in `sensors.py` or `sensors/*.py`:

```python
from typing import Any
from airframe.sensors.base import Sensor, SensorResult


class MigrationSafetySensor(Sensor):
    @property
    def name(self) -> str:
        return "migration_safety_gate"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate: Any, context: dict[str, Any] | None = None) -> SensorResult:
        data = candidate.model_dump() if hasattr(candidate, "model_dump") else dict(candidate)
        if data.get("risk_level") == "HIGH" and data.get("passed") is True:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message="Contradictory report: HIGH risk migration cannot set passed=true.",
            )
        return SensorResult(
            sensor_name=self.name,
            tier=self.tier,
            passed=True,
            message="Risk classification consistent with passed flag.",
        )
```

> **Constructor Binding Gotcha:** If a custom sensor takes constructor arguments (e.g., `def __init__(self, expected_lat: float)`), [`WorkflowPack.load_sensors`](../src/airframe/workflows/loader.py#L171-L188) automatically supplies matching keys from each test case's `input:` block. If a required `__init__` param has no default and isn't present in `input:`, that sensor is skipped for that case.

### Step 6 — Write Golden Assertions (`testbed.yaml`)

`airframe test` fails with exit code `1` whenever any sensor fails or any `expected` / `expected_output` assertion misses:

```yaml
- id: "blocking-index"
  description: "Detects unguarded CREATE INDEX"
  input:
    query: "CREATE INDEX idx_users_email ON users(email);"
  expected_output:
    risk_level: "HIGH"
    passed: false
    keyword: "concurrently"
```

Supported assertion rules in [`evaluate_expected_assertions`](../src/airframe/cli.py#L285-L350):
* **Numbers:** Float tolerance `±1e-2`.
* **Booleans / Strings / Nested Dicts:** Exact type-safe match (`bool` checked separately from `int`).
* **`keyword`:** Case-insensitive substring check flattened across the entire output payload.
* **`severity` / `min_issues` / `max_issues`:** Evaluated automatically when output contains an `issues` list.

### Step 7 — Run, Verify, and Benchmark Engine Swaps

```bash
# 1. Dry-run structure, tool binding, contracts, sensors, and W3C trace tree offline
airframe run workflows/sql-migrator -i "CREATE INDEX idx_a ON t(a);" --mock --trace

# 2. Validate against real LLM endpoint (--live enforces API key presence)
export GEMINI_API_KEY="..."
airframe test workflows/sql-migrator --live

# 3. Verify safe engine swap before switching models in production
airframe compare workflows/sql-migrator \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet
```

After any run, inspect the latest `.airframe/traces/<trace_id>.jsonl` tree via `airframe trace` (or `airframe trace --json`) to audit turn count, tool timings, and sensor verdicts.
