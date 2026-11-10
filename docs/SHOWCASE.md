# ⚡ Airframe 5-Minute Developer Quickstart & Showcase

Run every capability in **Airframe** directly from the CLI in under 5 minutes—zero API keys required by default, with one-flag promotion to live Gemini, Claude, OpenAI, or Ollama engines.

If you installed Airframe editable (`pip install -e ".[dev]"`), run `airframe ...` directly (substitute `PYTHONPATH=src python3 -m airframe.cli ...` if running uninstalled).

---

## 1. Run Single-Agent & Multi-Agent Topologies (With `--trace`)

Execute workflows across all three Airframe topologies and pass `--trace` (`-t`) to inspect the live W3C execution span hierarchy immediately after completion:

```bash
# Single-agent ReAct calculator pack (--light structure)
airframe run workflows/basic-calculator \
  -i "Calculate compound interest on ₹10,000 at 5% for 3 years" --mock --trace

# Context-pruned geospatial weather pack
airframe run workflows/weather-forecaster \
  -i "Will it rain in Bengaluru tomorrow at 3 PM?" --mock

# Evaluator-Optimizer code verification loop (AST + safety sensors)
airframe run workflows/patch-verifier \
  -i "Write a pure Python helper solve(x: int) -> int" --mock --trace

# Orchestrator-Workers swarm (selects entrypoint_agent 'audit-lead' over worker)
airframe run workflows/code-auditor-swarm \
  -i "Audit login.py for SQL injection or eval usage" --mock --trace
```

To promote any run to a real LLM provider, omit `--mock` and provide credentials (or target a local Ollama model):

```bash
export GEMINI_API_KEY="your-key"
airframe run workflows/code-auditor-swarm \
  -i "Audit login.py for SQL injection" -e gemini-2.0-flash --trace
```

---

## 2. Inspect Hierarchical Execution Traces (`airframe trace`)

Every `WorkflowRunner` run streams W3C-compatible spans line-by-line (with `fsync`) to `.airframe/traces/<trace_id>.jsonl`. Replay and render the span tree (`Root -> Supervisor -> Worker -> Model -> Tool -> Sensor`) anytime:

```bash
# Render the most recent trace tree from .airframe/traces/
airframe trace

# Inspect a specific trace by file path, full trace_id, or ID prefix
airframe trace <trace_id_or_prefix>

# Export reconstructed parent-child tree JSON (for scripts or CI dashboards)
airframe trace --json
```

---

## 3. Verify Semantic Assertions & Custom Sensors (`airframe test`)

`airframe test` evaluates `expected` / `expected_output` fields (`±1e-2` float tolerance, exact booleans/strings, `severity`, `keyword`) and runs auto-discovered `sensors.py`:

```bash
airframe test workflows/pr-reviewer --mock
airframe test workflows/patch-verifier --mock
airframe test workflows/code-auditor-swarm --mock
```

> **Note on `workflows/calculator`:** Its second benchmark case (`₹50,000` quarterly compounding) checks non-hardcoded live math via [`CompoundInterestMathSensor`](../workflows/calculator/sensors.py), so validate it using `--live` with an API key or `ollama/...`.

---

## 4. Benchmark an Engine Swap Without Code Changes (`airframe compare`)

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

> **Deep-Dive Crash-Durability & Worktree Proofs:** Real `SIGKILL` (`-9`) worktree orphan reaping, live-sibling `WorktreeConflictError`, truncated `transcript.jsonl` recovery, `StallDetector`, and 4-turn `ShiftTurnoverEngine` git checkpointing are verified in [`tests/unit/test_isolation.py`](../tests/unit/test_isolation.py), [`tests/unit/test_long_horizon_and_governance.py`](../tests/unit/test_long_horizon_and_governance.py), and [`tests/integration/test_runtime_runner.py`](../tests/integration/test_runtime_runner.py).

---

## Next Steps

* **Author your own workflow:** Read [WRITING_WORKFLOWS.md](WRITING_WORKFLOWS.md).
* **Plug in a custom model, tool, sensor, or isolation strategy:** Read [EXTENDING.md](EXTENDING.md).
