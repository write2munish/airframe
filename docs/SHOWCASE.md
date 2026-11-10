# ⚡ Airframe 5-Minute Developer Quickstart & Showcase

Run every capability in **Airframe** end-to-end in under 5 minutes—zero API keys required by default, with one-flag promotion to live Gemini, Claude, OpenAI, or Ollama engines.

---

## Option 1: One-Command Interactive Showcase (0.5s, Zero API Keys)

Run [`examples/showcase_walkthrough.py`](../examples/showcase_walkthrough.py) to watch all 5 engineering pillars execute live in a temporary sandbox:

```bash
PYTHONPATH=src python3 examples/showcase_walkthrough.py
```

What this executes deterministically:
1. **Act 1 — Engine Swap & Contract Self-Repair:** Resolves `gemini`, `anthropic`, `openai`, and `ollama` behind one interface, and watches `invoke_contract` auto-repair malformed JSON on retry.
2. **Act 2 — Zero-Decorator Tools & Phase Ceiling:** Calls `workflows/basic-calculator/tools.py` through `WorkflowRunner`, then demonstrates `ScopedToolInjector` blocking a 6-tool phase (`ToolScopeExceededError`).
3. **Act 3 — Worktree Isolation, Live-Peer Lock & `SIGKILL` Crash Recovery:** Spawns a subprocess claiming a Git worktree, verifies `WorktreeConflictError` against concurrent overwrite, kills the child with `SIGKILL` (`-9`), reaps the orphan cleanly via `reap_stale_worktrees()`, and rescues a crash-truncated `transcript.jsonl`.
4. **Act 4 — Long-Horizon Shifts & Circuit Breakers:** Crosses the 65% shift threshold in `branch` mode (`airframe: shift 1 checkpoint` commit + history wipe -> `[SHIFT 2]` bootload), then trips `StallDetector` after 3 identical tool failures.
5. **Act 5 — Multi-Agent Critique, Swarm Delegation & Trace Replay:**
   * Runs [`workflows/patch-verifier`](../workflows/patch-verifier/workflow.yaml) (`evaluator-optimizer`) rejecting unsafe `eval(...)` in Iteration 1 and repairing cleanly in Iteration 2.
   * Runs [`workflows/code-auditor-swarm`](../workflows/code-auditor-swarm/workflow.yaml) (`orchestrator-workers`) fanning out parallel workers calling `scan_diff_markers`.
   * Prints the reconstructed W3C parent-child trace tree from `.airframe/traces/<trace_id>.jsonl` (`Root -> Supervisor -> Worker -> Model + Tool -> Sensor`).

To run Act 1 against a real LLM provider instead:

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
