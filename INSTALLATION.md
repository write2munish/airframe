# 📦 Airframe Installation & Setup Guide

This guide walks you through setting up **Airframe**, configuring local or cloud model providers, and running the verification test suite.

---

## 1. Prerequisites

* **Python:** Python 3.10 or higher (`python3 --version`).
* **Package Manager:** `uv` (recommended for ultra-fast setup) or standard `pip`.
* **Git:** For version control and workspace worktrees (`git --version`).

---

## 2. Installation Options

### Option A: Install from Source (Recommended for Development)

Clone the repository and install in editable mode with development dependencies:

```bash
# 1. Clone the repository
git clone https://github.com/write2munish/airframe.git
cd airframe

# 2. Create and activate a virtual environment
# Using uv (fastest):
uv venv
source .venv/bin/activate

# Or using standard python:
python3 -m venv .venv
source .venv/bin/activate

# 3. Install in editable mode with test and development dependencies
# Using uv:
uv pip install -e ".[dev]"

# Or using standard pip:
pip install -e ".[dev]"
```

### Option B: Install Directly from GitHub via Pip

If you just want to use Airframe as a library in another project:

```bash
pip install git+https://github.com/write2munish/airframe.git
```

---

## 3. Verify the Installation

Check that the CLI entry point is installed and operational:

```bash
airframe --version
# Expected output: Airframe version 0.1.0

airframe info
# Expected output: Displays system status and registered provider adapters
```

Run the unit test suite to verify your environment:

```bash
PYTHONPATH=src pytest tests/ -v
# Expected output: 20 passed in ~0.11s
```

---

## 4. Configuring Model Engine Providers

Airframe is designed to be **provider-agnostic**. You only need to configure the provider(s) you plan to use.

### A. Local Models via Ollama (Zero Cost / Offline)
To run local models without any cloud API keys:
1. Install [Ollama](https://ollama.com).
2. Pull your model of choice:
   ```bash
   ollama pull llama3
   ```
3. Ollama runs automatically on `http://localhost:11434/v1`. Airframe connects to this endpoint natively via `openai_compatible`.

### B. Google Gemini
Set your Gemini API key in your environment or `.env` file:
```bash
export GEMINI_API_KEY="your-gemini-api-key"
# or
export GOOGLE_API_KEY="your-gemini-api-key"
```

### C. Anthropic Claude
Set your Anthropic API key:
```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

### D. OpenAI
Set your OpenAI API key:
```bash
export OPENAI_API_KEY="your-openai-api-key"
```

---

## 5. Quick Verification Script

Run this quick test script to verify that your configured provider is working:

```python
import asyncio
from airframe.adapters import get_adapter
from pydantic import BaseModel

class UserProfile(BaseModel):
    name: str
    role: str
    active: bool

async def main():
    # 1. Choose any engine (e.g. 'gemini', 'anthropic', or 'ollama')
    adapter = get_adapter(provider="gemini", model_name="gemini-2.0-flash")
    
    # 2. Invoke with executable contract validation and automated repair
    profile, resp = await adapter.invoke_contract(
        prompt="Generate a profile for a senior site reliability engineer named Elena.",
        schema=UserProfile,
    )
    
    print(f"Validated Contract: {profile}")
    print(f"Tokens Used: {resp.usage.total_tokens} | Latency: {resp.latency_ms}ms")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. Development Workflow & Testing

* **Run all tests:**
  ```bash
  pytest tests/ -v
  ```
* **Linting & Code Formatting:**
  ```bash
  ruff check .
  ruff format .
  ```
* **Type Checking:**
  ```bash
  mypy src/
  ```
