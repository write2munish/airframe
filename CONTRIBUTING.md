# Contributing to Airframe

## 1. Local Setup

```bash
python -m pip install -e ".[dev]"
```

## 2. Required Verification Checks

CI runs both `pytest` and `ruff` via [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on pushes and pull requests to `main`:

```bash
pytest tests/unit tests/integration -v
ruff check --select E9 src tests
ruff check --select E4,E7,E9,F,I src/airframe/cli.py tests/unit/test_budget_concurrency.py tests/unit/test_workflows_and_diff.py
```

* Live provider tests (`tests/live/`) are opt-in and never required in offline CI.
* CLI tests must pass `--mock` so test outcomes stay independent of ambient provider API keys.

## 3. Commit Message Policy

* **Never write test pass counts in commit messages** (for example, do not write `"86/86 tests passing"` or `"all 55 tests pass"`). Static counts rot immediately on subsequent commits and create unverified claims.
* **Cite the CI run or verification command instead** — e.g.:
  * `Verified by CI (pytest + ruff): <run URL>`
  * `Local check before push: pytest tests/unit tests/integration`

## 4. Correctness Gates

* **Regression checks:** Any workflow, model, or playbook change tested with `airframe compare` must report zero regressions against its baseline scorecard.
* **Concurrency & governance:** New race or budget-bypass fixes must start with a deterministic concurrency test reproducing the hazard before changing runtime enforcement code.
