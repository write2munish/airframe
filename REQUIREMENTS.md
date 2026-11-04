# Requirements Specification: Model Harness Engineering (`model-harness`)

## 1. Executive Summary & Vision
The **Model Harness** (`model-harness`) is an evaluation, execution, and verification chassis designed to decouple large language models (LLMs) and agentic workflows from application logic. It allows engineering teams to benchmark, validate, and hot-swap underlying models (e.g., Gemini, Claude, GPT, Ollama/local models) with zero downstream regression or contract violations.

---

## 2. Core Architectural Principles
1. **Contract-First Design**: Models never emit unvalidated, freeform strings to application consumers. All outputs are bound to strict schemas (JSON Schema / Pydantic) with automated repair loops.
2. **Provider Agnosticism**: Unified adapter interface isolates model-specific prompt formats, parameter differences, and token nuances.
3. **Multi-Tier Evaluation**: Fast, deterministic assertions run first; semantic and model-based judges run only on schema-valid candidates.
4. **Differential Regression Testing**: Every candidate model is benchmarked against a golden baseline suite to detect behavior shifts, cost drift, and latency degradation.

---

## 3. Functional Requirements (FR)

### FR-1: Unified Model Adapter Gateway
- [ ] Provide an abstract base class (`ModelAdapter`) with a standardized `invoke()` and `invoke_contract()` interface.
- [ ] Support initial providers:
  - Local/OSS models (via Ollama or OpenAI-compatible endpoint like vLLM / LM Studio).
  - Cloud API providers (Google Gemini, Anthropic Claude, OpenAI).
- [ ] Support model-specific prompt profiling (compiling canonical task instructions into provider-preferred formatting).

### FR-2: Strict Schema Enforcement & Self-Repair
- [ ] Define canonical output models using Pydantic / JSON Schema.
- [ ] Include an automated validation loop with configurable retry attempts (`max_retries`).
- [ ] On schema validation failure, automatically construct a targeted repair prompt passing the validation error back to the model.

### FR-3: Multi-Tier Evaluator System
- [ ] **Tier 1 (Deterministic)**:
  - Schema validity & type checking.
  - Required field presence & non-empty assertions.
  - Regex pattern matching & exact match keywords.
- [ ] **Tier 2 (Semantic / Functional)**:
  - Embedding distance / cosine similarity against golden outputs.
  - Tool call / argument accuracy (did it pick the right tool with the right params?).
- [ ] **Tier 3 (Model-as-a-Judge / Rubric)**:
  - Structured evaluation rubric (accuracy, reasoning clarity, conciseness).
  - Binary pass/fail or calibrated 1-5 score with reasoning extraction.

### FR-4: Test Bed & Golden Dataset Management
- [ ] Support loading test cases from YAML/JSON files.
- [ ] Test case schema must include:
  - `id`: Unique test case identifier.
  - `description`: Purpose of the test (normal path, edge case, adversarial).
  - `input`: Prompt payload or task parameters.
  - `expected`: Golden output, assertion rules, or tolerance thresholds.
  - `tags`: Capability tags (e.g., `tool-use`, `reasoning`, `json-schema`, `smoke`).

### FR-5: Execution Engine & Telemetry
- [ ] Parallel / async test runner with concurrency limits.
- [ ] Token usage tracking (input tokens, output tokens, estimated API cost).
- [ ] Latency and time-to-first-token (TTFT) metrics.
- [ ] Request caching to avoid re-running identical prompts during evaluator tuning.

### FR-6: Scorecard & Regression Diff Reporting
- [ ] CLI scorecard output (terminal summary table with pass/fail counts).
- [ ] Markdown / JSON report artifact generation.
- [ ] Diff comparison tool: compare Run A (Baseline Model) vs Run B (Candidate Model) to highlight:
  - Regressions (tests passed in A, failed in B).
  - Improvements (tests failed in A, passed in B).
  - Performance deltas (latency & cost changes).

---

## 4. Non-Functional Requirements (NFR)

- **NFR-1 (Determinism & Flake Control)**: Evaluators must be deterministic wherever possible. LLM judges must use temperature=0 and fixed seeds.
- **NFR-2 (Modularity)**: Adding a new model provider or evaluator should require implementing a single class without touching existing test cases or core runner code.
- **NFR-3 (Developer Experience)**: Simple CLI commands (`harness run`, `harness compare`, `harness init`) and minimal boilerplates.
- **NFR-4 (Zero Secret Leakage)**: API keys and credentials must be loaded via environment variables (`.env`) or standard secret managers, never committed to disk.

---

## 5. Directory Structure & Layout
```
model-harness/
├── README.md
├── REQUIREMENTS.md
├── pyproject.toml
├── src/
│   └── harness/
│       ├── __init__.py
│       ├── adapters/         # Model provider adapters (Gemini, Claude, OpenAI, Ollama)
│       ├── contracts/        # Canonical schemas and validation loops
│       ├── evaluators/       # Tier 1, 2, and 3 evaluators
│       ├── runner/           # Execution engine, concurrency, metrics
│       ├── storage/          # Baselines, caches, and report generators
│       └── cli.py            # Command-line interface
├── tests/
│   ├── unit/                 # Unit tests for the harness itself
│   └── fixtures/             # Sample datasets and golden sets
└── datasets/
    ├── smoke.yaml            # Quick sanity test cases
    └── benchmark_core.yaml   # Comprehensive evaluation suite
```
