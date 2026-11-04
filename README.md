# `model-harness`

> **A contract-first evaluation and execution harness for swappable LLMs and agentic workflows.**

## Overview

`model-harness` is a framework designed to decouple language models from downstream application logic. By treating the model as an interchangeable engine and enforcing strict output contracts, regression suites, and multi-tier evaluation gates, teams can swap providers or upgrade models with high confidence and zero downstream breakage.

---

## Key Highlights

- **Contract-First Gateway**: Strict Pydantic / JSON Schema validation with automatic self-repair loops.
- **Provider Agnostic**: Unified adapter layer for Google Gemini, Anthropic Claude, OpenAI, and local models (via Ollama / vLLM / LM Studio).
- **Multi-Tier Evaluation**: Fast deterministic checks (Tier 1) $\rightarrow$ Semantic/tool assertions (Tier 2) $\rightarrow$ LLM Judge (Tier 3).
- **Differential Regression Testing**: Compare candidate models against golden baselines with automated diff scorecards.

---

## Status

Currently in **Requirements & Architecture Phase**. See [REQUIREMENTS.md](REQUIREMENTS.md) for detailed specifications.
