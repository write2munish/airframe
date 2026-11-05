# ⚡ Basic Calculator Workflow (Lightweight Scaffold)

> **Demonstration of Airframe's lightened scaffolding: building a production-ready, contract-guarded AI agent workflow in 3 files with zero directory bloat.**

---

## 1. How It Was Scaffolded

This entire workflow was generated in one command using Airframe's `--light` flag:

```bash
airframe init workflow basic-calculator --light --dir workflows
```

Unlike multi-agent enterprise topologies that require separate `blueprints/`, `guides/`, `guards/`, and `sensors/` directories, a single-agent task is colocated into 3 flat files:

```
workflows/basic-calculator/
├── README.md         # Documentation & guide
├── workflow.yaml     # Master manifest: inline prompt, tools, & governance
├── tools.py          # Pure Python tools (safe AST arithmetic & compound interest)
├── contracts.py      # Pydantic v2 CalculationResult contract
└── testbed.yaml      # Golden sanity test cases
```

---

## 2. Components

### Manifest (`workflow.yaml`)
Declares the agent's identity, engine, allowed tools, and resource limits inline:
```yaml
id: "basic-calculator"
topology: "single-agent"
entrypoint_agent: "calculator"
engine_binding: "gemini-2.0-flash"
tools:
  - "calculate"
  - "calculate_compound_interest"

system_prompt: |
  You are an accurate, reliable mathematical calculator assistant.
  Always delegate calculations to the provided tools.
  Format your final output conforming strictly to the CalculationResult schema.

governance:
  max_turns: 5
  timeout_seconds: 60
  max_cost_usd: 0.05
```

### Pure Python Tools (`tools.py`)
Deterministic, safe Python functions:
* `calculate(expression: str)`: Safely evaluates arithmetic expressions using Python AST traversal.
* `calculate_compound_interest(principal, rate, time, compounding_frequency)`: Computes $A = P(1 + r/n)^{nt}$.

### Contract Schema (`contracts.py`)
```python
class CalculationResult(BaseModel):
    query: str
    result: float
    formatted_result: str
    steps: list[str] = []
    status: str = "SUCCESS"
```

---

## 3. Running & Testing

```bash
# Run end-to-end via CLI
airframe run workflows/basic-calculator -i "Calculate compound interest on ₹10,000 at 5% for 3 years" --mock

# Run regression testbed
airframe test workflows/basic-calculator
```
