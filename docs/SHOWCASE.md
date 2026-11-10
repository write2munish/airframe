# ⚡ Airframe 5-Minute Developer Quickstart & Showcase

Run every capability in **Airframe** end-to-end in under 5 minutes—zero API keys required by default, with one-flag promotion to live Gemini, Claude, OpenAI, or Ollama engines.

---

## Option 1: One-Command Python Quickstart (~100 Lines, Zero API Keys)

Run [`examples/showcase_walkthrough.py`](../examples/showcase_walkthrough.py) to watch Airframe's core engine, multi-topology runner, governance breakers, disk state, and trace trees run end-to-end in under 1 second:

```bash
PYTHONPATH=src python3 examples/showcase_walkthrough.py
```

What this demonstrates in 4 concise steps:
1. **Engine Swap + Executable Contracts:** Resolves `gemini`, `anthropic`, `openai`, and `ollama` behind one interface and runs `invoke_contract` against a Pydantic model.
2. **Same Runner Across All 3 Topologies:** Runs [`workflows/basic-calculator`](../workflows/basic-calculator/workflow.yaml) (`single-agent`), [`workflows/patch-verifier`](../workflows/patch-verifier/workflow.yaml) (`evaluator-optimizer`), and [`workflows/code-auditor-swarm`](../workflows/code-auditor-swarm/workflow.yaml) (`orchestrator-workers`) with their auto-discovered `sensors.py`.
3. **Guards, Breakers & Externalized State:** Shows `ExecutableGuard` rejecting malformed JSON, `StallDetector` halting 3 repeated failures, and `DiskStateManager` atomically persisting `task_queue.json`.
4. **W3C Trace Tree Replay:** Reconstructs and prints the parent-child span hierarchy from `.airframe/traces/<trace_id>.jsonl` using `load_trace()` and `build_trace_tree()`.

> **Deep-Dive Crash-Durability & Worktree Proofs:** Real `SIGKILL` (`-9`) worktree orphan reaping, live-sibling `WorktreeConflictError`, truncated `transcript.jsonl` recovery, and 4-turn `ShiftTurnoverEngine` git checkpointing are verified automatically in [`tests/unit/test_isolation.py`](../tests/unit/test_isolation.py) and [`tests/integration/test_runtime_runner.py`](../tests/integration/test_runtime_runner.py).

To run the walkthrough against a real LLM provider instead:

```bash
export GEMINI_API_KEY="your-key"
PYTHONPATH=src python3 examples/showcase_walkthrough.py --live gemini-2.0-flash
```

---

## Option 2: 5-Minute CLI Showcase Tour

If you installed Airframe editable (`pip install -e ".[dev]"`), run these CLI commands in sequence (substitute `PYTHONPATH=src python3 -m airframe.cli` if running uninstalled):

### 1. Run Single-Agent & Multi-Agent Topologies (Offline Dry-Run)

```bash
# Single-agent ReAct calculator pack (--light structure)
airframe run workflows/basic-calculator \
  -i "Calculate compound interest on ₹10,000 at 5% for 3 years" --mock

# Context-pruned geospatial weather pack
airframe run workflows/weather-forecaster \
  -i "Will it rain in Bengaluru tomorrow at 3 PM?" --mock

# Evaluator-Optimizer code verification loop (AST + safety sensors)
airframe run workflows/patch-verifier \
  -i "Write a pure Python helper solve(x: int) -> int" --mock

# Orchestrator-Workers swarm (selects entrypoint_agent 'audit-lead' over worker)
airframe run workflows/code-auditor-swarm \
  -i "Audit login.py for SQL injection or eval usage" --mock
```

### 2. Verify Semantic Assertions & Custom Sensors

`airframe test` evaluates `expected` / `expected_output` fields (`±1e-2` float tolerance, exact booleans/strings, `severity`, `keyword`) and runs auto-discovered `sensors.py`:

```bash
airframe test workflows/pr-reviewer --mock
airframe test workflows/patch-verifier --mock
airframe test workflows/code-auditor-swarm --mock
```

> **Note on `workflows/calculator`:** Its second benchmark case (`₹50,000` quarterly compounding) checks non-hardcoded live math via [`CompoundInterestMathSensor`](../workflows/calculator/sensors.py), so validate it using `--live` with an API key or `ollama/...`.

### 3. Benchmark an Engine Swap Without Code Changes

```bash
airframe compare workflows/pr-reviewer \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet \
  --mock
```

When API keys are unset (or `--mock` is passed), `compare` clearly prints `DRY-RUN MOCK MODE`. With `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` set, the exact same command runs live end-to-end regressions and exits with code `1` on any regression.

---

## Showcase Workflow Matrix

| Workflow Pack | Topology | Key Airframe Feature Highlighted |
| :--- | :--- | :--- |
| [`workflows/basic-calculator`](../workflows/basic-calculator/workflow.yaml) | `single-agent` | Minimal `--light` layout, AST-safe math tools, numeric assertions |
| [`workflows/weather-forecaster`](../workflows/weather-forecaster/workflow.yaml) | `single-agent` | Async `httpx` payload pruning, `CoordinateAccuracySensor`, `ToolSequenceSensor` |
| [`workflows/pr-reviewer`](../workflows/pr-reviewer/workflow.yaml) | `single-agent` | Blueprints, guides, `CodeReviewReport` contract, `severity`/`keyword` assertions |
| [`workflows/calculator`](../workflows/calculator/workflow.yaml) | `single-agent` | Custom [`CompoundInterestMathSensor`](../workflows/calculator/sensors.py) verifying math independently |
| [`workflows/patch-verifier`](../workflows/patch-verifier/workflow.yaml) | `evaluator-optimizer` | Iterative critique loop paired with `SafePythonPatchSensor` AST checks |
| [`workflows/code-auditor-swarm`](../workflows/code-auditor-swarm/workflow.yaml) | `orchestrator-workers` | Multi-blueprint planning, `entrypoint_agent` resolution, parallel tool workers |

---

## Next Steps

* **Author your own workflow:** Read [WRITING_WORKFLOWS.md](WRITING_WORKFLOWS.md).
* **Plug in a custom model, tool, sensor, or isolation strategy:** Read [EXTENDING.md](EXTENDING.md).
