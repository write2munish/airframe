# 🔍 Pull Request Code Reviewer Workflow

> **A reference implementation of a multi-directory Workflow Pack demonstrating automated security auditing, quality checks, and differential engine regression testing.**

---

## 1. Overview

The **PR Reviewer** workflow inspects incoming code diffs for security vulnerabilities, logic defects, and style regressions. Unlike trivial single-agent tasks, code review demands detailed steering guides, strict schema contracts, multi-tier sensor evaluations, and golden regression datasets.

This workflow serves as the reference implementation for **folder-per-concern Workflow Packs** (`agents: ["blueprints/reviewer.yaml"]`), where separating blueprints, guides, guards, and testbeds earns its architectural complexity.

---

## 2. Directory Structure

```
workflows/pr-reviewer/
├── README.md               # This architecture and usage guide
├── workflow.yaml           # Master manifest & governance ceilings
├── blueprints/
│   └── reviewer.yaml       # Agent blueprint (role, model engine, allowed tools)
├── guides/
│   └── task_spec.md        # Detailed review policies and feedforward steering rules
├── guards/
│   └── contracts.py        # Pydantic v2 output schemas (CodeReviewReport, ReviewIssue)
├── sensors/
│   └── deterministic.py    # Tier 1 & 2 sensors validating blockers and keyword detection
└── testbed/
    └── smoke.yaml          # Golden test cases (SQL injection, clean refactor, etc.)
```

---

## 3. Execution Pipeline

```
[User Git Diff / PR Payload]
       │
       ▼
[Airframe Operating Chassis]
  ├── Loads `workflow.yaml` & merges budget (`GovernanceSpec`)
  ├── Provisions workspace strategy (`inherit`)
  ├── Injects task specification (`guides/task_spec.md`)
  └── Invokes engine (`gemini-2.0-flash` or candidate engine)
       │
       ▼
[Executable Schema Guard (`CodeReviewReport`)]
  ├── Validates JSON syntax & types via `SchemaSensor`
  └── Triggers self-repair loop if format or required keys diverge
       │
       ▼
[Sensor Pipeline Verification]
  ├── Tier 1 (Deterministic): Asserts blocker issues exist for known SQLi
  ├── Tier 2 (Semantic): Validates keyword and issue severity categorization
  └── Tier 3 (Judge): Evaluates clarity of recommended fix snippets
```

---

## 4. Executable Output Contract

Every review emitted by the agent must conform strictly to `CodeReviewReport` in `guards/contracts.py`:

```python
class ReviewIssue(BaseModel):
    file_path: str
    line_number: int
    severity: Literal["BLOCKER", "WARNING", "NIT"]
    description: str
    suggested_fix: str | None = None

class CodeReviewReport(BaseModel):
    summary: str
    passed: bool
    issues: list[ReviewIssue] = []
```

---

## 5. Running the Workflow

### Execute via CLI

```bash
# Run with a diff snippet in verified dry-run mode
airframe run workflows/pr-reviewer \
  -i "cursor.execute(f'SELECT * FROM users WHERE user = {username}')" \
  --mock

# Run with a live model engine
airframe run workflows/pr-reviewer \
  -i "cursor.execute(f'SELECT * FROM users WHERE user = {username}')" \
  --engine gemini-2.0-flash
```

### Run Testbed Regression Suite

```bash
# Offline deterministic verification
airframe test workflows/pr-reviewer --mock

# Enforce live model credentials
airframe test workflows/pr-reviewer --live
```

### Benchmark Model Swap (Differential Regression Analysis)

Airframe allows comparing baseline vs candidate models across this workflow's testbed before upgrading models in production:

```bash
# Offline dry-run comparison (or omit --mock when keys for both engines are set)
airframe compare workflows/pr-reviewer \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet \
  --mock
```
