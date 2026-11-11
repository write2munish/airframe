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

### Step 2: Define the Executable Contract (`contracts.py`)
Never let the agent emit freeform text to downstream services. Bind the response to a strict Pydantic v2 contract:

```python
from pydantic import BaseModel, Field

class CompoundInterestContract(BaseModel):
    currency: str = "₹"
    principal: float
    rate_percent: float
    time_years: float
    compounding_frequency: int = 1
    total_amount: float
    total_interest: float
    explanation: str = Field(description="Human-readable breakdown of the calculation.")
```

---

### Step 3: Implement Verification Sensors (`sensors.py`)
To prevent math hallucinations, create a **Tier 1 Deterministic Sensor** that independently recalculates $A = P(1+r/n)^{nt}$ and checks both `total_amount` and `total_interest`:

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
        p = float(data["principal"])
        r = float(data["rate_percent"]) / 100.0
        t = float(data["time_years"])
        n = int(data.get("compounding_frequency", 1))
        expected_amount = p * ((1 + (r / n)) ** (n * t))
        expected_interest = expected_amount - p

        if abs(float(data["total_amount"]) - expected_amount) > 0.05 or abs(float(data["total_interest"]) - expected_interest) > 0.05:
            return SensorResult(sensor_name=self.name, tier=1, passed=False, message="Math mismatch detected!")
        return SensorResult(sensor_name=self.name, tier=1, passed=True, message="Math verified independently.")
```

---

### Step 4: Configure the Unified Workflow Manifest (`workflow.yaml`)
Single-agent packs declare `engine_binding`, whitelisted tools (`tools:`), inline `system_prompt:`, and `governance:` directly inside `workflow.yaml`:

```yaml
id: "single-tool-calculator"
version: "1.0.0"
topology: "single-agent"
workspace_mode: "inherit"
entrypoint_agent: "financial-calculator"
engine_binding: "gpt-4o-mini"          # Swappable! Can be gemini-2.0-flash, claude-3-5-sonnet, or local ollama
tools:
  - "calculate_compound_interest"      # Principle of least privilege: only this tool is whitelisted

system_prompt: |
  You are a financial calculation specialist.
  Calculate compound interest according to the formula A = P(1 + r/n)^(nt).
  When tools are available, you may verify results using calculate_compound_interest.
  Format your final output strictly according to the CompoundInterestContract schema.

governance:
  max_turns: 5
  timeout_seconds: 60
  stall_threshold: 2
  max_cost_usd: 0.10

testbed: "testbed.yaml"
```

---

### Step 5: Create Golden Test Cases (`testbed.yaml`)
Ensure that switching foundation models or prompts never introduces regressions:

```yaml
- id: "calc-smoke-01"
  description: "Compound interest on ₹10,000 at 5% for 3 years annually"
  input:
    principal: 10000.0
    rate_percent: 5.0
    time_years: 3.0
    compounding_frequency: 1
  expected_output:
    total_amount: 11576.25
    total_interest: 1576.25

- id: "calc-smoke-02"
  description: "Compound interest on ₹50,000 at 8% for 2 years quarterly"
  input:
    principal: 50000.0
    rate_percent: 8.0
    time_years: 2.0
    compounding_frequency: 4
  expected_output:
    total_amount: 58582.97
    total_interest: 8582.97
```

---

## Running & Testing the Workflow

### Method 1: Execute via Airframe CLI (Dry-Run Mock Mode)
```bash
airframe run workflows/calculator -i "Calculate the compound interest on ₹10,000 at 5% for 3 years" --mock
```

**Output:**
```
╭──────────────────────── ✈️  Airframe Execution Engine ────────────────────────╮
│ Executing Workflow: single-tool-calculator                                   │
│ Engine: gpt-4o-mini  Topology: single-agent                                  │
│ Entrypoint: financial-calculator                                             │
│ Input: Calculate the compound interest on ₹10,000 at 5% for 3 years          │
╰──────────────────────────────────────────────────────────────────────────────╯

✓ Execution Completed Successfully
╭──────────────── Validated Contract: CompoundInterestContract ────────────────╮
│ {                                                                            │
│   "currency": "\u20b9",                                                      │
│   "principal": 10000.0,                                                      │
│   "rate_percent": 5.0,                                                       │
│   "time_years": 3.0,                                                         │
│   "compounding_frequency": 1,                                                │
│   "total_amount": 11576.25,                                                  │
│   "total_interest": 1576.25,                                                 │
│   "explanation": "Verified explanation"                                      │
│ }                                                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
                        Execution Telemetry & Governance                        
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric           ┃ Value                                                     ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Engine Provider  │ mock                                                      │
│ Model            │ gpt-4o-mini                                               │
│ Entrypoint Agent │ financial-calculator                                      │
│ Turns Executed   │ 1                                                         │
│ Input Tokens     │ 120                                                       │
│ Output Tokens    │ 65                                                        │
│ Total Tokens     │ 185                                                       │
│ Latency          │ 1.8ms                                                     │
│ Estimated Cost   │ $0.00030                                                  │
│ Workspace Mode   │ inherit                                                   │
└──────────────────┴───────────────────────────────────────────────────────────┘
```

### Method 2: Test the Workflow Testbed (Live LLM)
Because `calc-smoke-02` verifies non-hardcoded quarterly compounding (`₹50,000`) via `CompoundInterestMathSensor`, run the full testbed against a live model (`--live` with `OPENAI_API_KEY` or `-e ollama/...`):
```bash
airframe test workflows/calculator --live
```

### Method 3: Benchmark Model Swaps (Zero Regressions)
With credentials set for both engines (`GEMINI_API_KEY` and `ANTHROPIC_API_KEY`), benchmark a model swap:
```bash
airframe compare workflows/calculator \
  --baseline gemini-2.0-flash \
  --candidate claude-3-5-sonnet \
  --live
```
