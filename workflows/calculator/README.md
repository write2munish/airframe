# 🧮 Single-Tool Financial Calculator Workflow

> **A step-by-step tutorial and reference implementation demonstrating how to build a reliable, contract-guarded workflow using Airframe.**

This sample workflow solves a common problem in agentic systems: **delegating complex mathematical calculations (like compound interest) to authorized computational tools while guaranteeing that output schemas and math are 100% verified.**

---

## The Problem: Why Raw Prompts Fail

If you ask an LLM:
> *"Calculate the compound interest on ₹10,000 at 5% for 3 years."*

A raw LLM often:
1. Performs mental arithmetic and hallucinates fractional values.
2. Returns conversational prose (*"Sure! The answer is ₹1,576.25."*) that breaks downstream databases expecting numeric types.
3. Pollutes the parent context window with dozens of conversational tokens in multi-agent workflows.

**Airframe transforms this into a deterministic, guarded pipeline.**

---

## Streamlined Single-Agent Workflow Structure

Under modern Airframe guidelines, single-agent packs avoid 6 nested subdirectories. All components are colocated cleanly:

```
workflows/calculator/
├── README.md         # This step-by-step guide
├── workflow.yaml     # 1. Master manifest (inline prompt, tools, governance)
├── tools.py          # 2. Pure Python computational tools
├── contracts.py      # 3. Pydantic executable output schema
├── sensors.py        # 4. Tier 1 math verification sensor
└── testbed.yaml      # 5. Golden test cases for regression testing
```

---

### Step 1: Write the Tool Function (`tools.py`)
Define pure, deterministic Python functions. Airframe agents never execute unvetted code; they invoke authorized tools:

```python
def calculate_compound_interest(
    principal: float, rate_percent: float, time_years: float, compounding_frequency: int = 1
) -> dict:
    p = float(principal)
    r = float(rate_percent) / 100.0
    t = float(time_years)
    n = int(compounding_frequency)

    total_amount = p * ((1 + (r / n)) ** (n * t))
    total_interest = total_amount - p

    return {
        "principal": round(p, 2),
        "total_amount": round(total_amount, 2),
        "total_interest": round(total_interest, 2),
    }
```

---

### Step 2: Define the Executable Contract (`guards/contracts.py`)
Never let the agent emit freeform text to downstream services. Bind the response to a strict Pydantic v2 contract:

```python
from pydantic import BaseModel, Field

class CompoundInterestContract(BaseModel):
    currency: str = "₹"
    principal: float
    rate_percent: float
    time_years: float
    total_amount: float
    total_interest: float
    explanation: str = Field(description="Clear textual summary of calculation.")
```

---

### Step 3: Implement Verification Sensors (`sensors/deterministic.py`)
To prevent math hallucinations, create a **Tier 1 Deterministic Sensor** that independently recalculates the formula $A = P(1+r/n)^{nt}$:

```python
from airframe.sensors.base import Sensor, SensorResult

class CompoundInterestMathSensor(Sensor):
    @property
    def name(self) -> str:
        return "compound_interest_math_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate, context=None) -> SensorResult:
        data = candidate.model_dump() if hasattr(candidate, "model_dump") else candidate
        expected_amount = data["principal"] * ((1 + (data["rate_percent"] / 100.0)) ** data["time_years"])

        if abs(data["total_amount"] - expected_amount) > 0.05:
            return SensorResult(sensor_name=self.name, tier=1, passed=False, message="Math mismatch detected!")
        return SensorResult(sensor_name=self.name, tier=1, passed=True, message="Math verified independently.")
```

---

### Step 4: Write the Agent Blueprint (`blueprints/calculator_agent.yaml`)
Apply the **Principle of Least Privilege**. Grant access *only* to the calculator tool:

```yaml
name: "financial-calculator"
role: "Financial Calculation Specialist"
system_prompt: "guides/task_spec.md"
engine_binding: "gemini-2.0-flash"     # Swappable! Can be gpt-4o, claude-3-5-sonnet, or local ollama
allowed_tools:
  - "calculate_compound_interest"     # Whitelisted tool
workspace_mode: "inherit"
budget:
  max_tokens: 15000
  timeout_seconds: 60
```

---

### Step 5: Define Task Guidance (`guides/task_spec.md`)
Guide the model *before* execution:
```markdown
# Rules
1. Do NOT perform mental arithmetic.
2. You MUST invoke `calculate_compound_interest`.
3. Output strictly conforming to the `CompoundInterestContract` schema.
```

---

### Step 6: Configure the Workflow Manifest (`workflow.yaml`)
Tie the components together:

```yaml
id: "single-tool-calculator"
version: "1.0.0"
topology: "single-agent"
workspace_mode: "inherit"
entrypoint_agent: "financial-calculator"
agents:
  - "blueprints/calculator_agent.yaml"
testbed: "testbed/smoke.yaml"
```

---

### Step 7: Create Golden Test Cases (`testbed/smoke.yaml`)
Ensure that changing foundation models in the future never introduces regressions:

```yaml
- id: "calc-10k-5pct-3yr"
  description: "Compound interest on ₹10,000 at 5% for 3 years"
  input:
    principal: 10000
    rate_percent: 5.0
    time_years: 3
  expected:
    total_amount: 11576.25
    total_interest: 1576.25
```

---

## Running & Testing the Workflow

### Method 1: Execute via Airframe CLI
```bash
airframe run workflows/calculator -i "Calculate the compound interest on ₹10,000 at 5% for 3 years" --mock
```

**Output:**
```
╭──────────────────────── ✈️  Airframe Execution Engine ────────────────────────╮
│ Executing Workflow: single-tool-calculator                                   │
│ Engine: gpt-4o-mini  Topology: single-agent                                  │
│ Input: Calculate the compound interest on ₹10,000 at 5% for 3 years           │
╰──────────────────────────────────────────────────────────────────────────────╯

✓ Execution Completed Successfully
╭──────────────── Validated Contract: CompoundInterestContract ────────────────╮
│ {                                                                            │
│   "currency": "₹",                                                           │
│   "principal": 10000.0,                                                      │
│   "rate_percent": 5.0,                                                       │
│   "time_years": 3.0,                                                         │
│   "compounding_frequency": 1,                                                │
│   "total_amount": 11576.25,                                                  │
│   "total_interest": 11576.25,                                                │
│   "explanation": "Verified explanation"                                      │
│ }                                                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
Execution Telemetry & Governance 
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Metric          ┃ Value       ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Engine Provider │ mock        │
│ Model           │ mock-engine │
│ Input Tokens    │ 120         │
│ Output Tokens   │ 65          │
│ Total Tokens    │ 185         │
│ Latency         │ 18.5ms      │
│ Estimated Cost  │ $0.00030    │
│ Workspace Mode  │ inherit     │
└─────────────────┴─────────────┘
```

### Method 2: Test the Workflow Testbed
```bash
airframe test workflows/calculator
```

### Method 3: Benchmark Model Swaps (Zero Regressions)
```bash
airframe compare workflows/calculator \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet
```
