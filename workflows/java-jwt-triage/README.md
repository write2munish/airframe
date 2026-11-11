# Self-Improving Bug Triage (`auth0/java-jwt`)

Demonstrates measurable cross-task agent improvement using Airframe's playbook resolution, Tier-1 path sensors, token budgeting, and differential regression gate (`compare_scorecards()`) against **6 real closed issues from [`auth0/java-jwt`](https://github.com/auth0/java-jwt)**.

---

## Measured Progression (6 Real Closed Issues)

| Epoch | Playbook | Pass Rate | Fixed / Regressed | Guide Tokens | Gate Decision |
| :---: | :--- | :---: | :---: | :---: | :--- |
| 0 | `v0-baseline` (Day-1 rules) | `3/6 (50%)` | `-` | `171` | `BASELINE` |
| 1 | `v1-overbroad` (naive `exp`/`aud` rewrite) | `4/6 (67%)` | `+3 / -2` | `210` | `REJECTED (-2 reg)` |
| 2 | `v2-refined` (scoped codebase boundaries) | `6/6 (100%)` | `+3 / -0` | `364` | `PROMOTED` |

* **Why Epoch 0 fails (`3/6`):** Generic heuristics misroute JSON `NumericDate` overflow (`#706`) and empty-string `aud` deserialization (`#663`) to `JWTVerifier.java` instead of `impl/PayloadDeserializer.java`, and route `null` token splitting (`#606`) to `JWTDecoder.java` instead of `TokenUtils.java`.
* **Why Epoch 1 is blocked:** Greedily moving all `exp`/`aud` rules to `PayloadDeserializer.java` fixes `#706`/`#663` (+`#606`) but breaks passing verifier boundary cases (`#652`, `#679`). Airframe's zero-regression gate rejects promotion.
* **Why Epoch 2 promotes (`6/6`):** Scopes rules cleanly across `TokenUtils.splitToken` (splitting) -> `impl/PayloadDeserializer` (JSON parsing/coercion) -> `JWTVerifier` (runtime claim assertions).

---

## Pack Structure

```text
workflows/java-jwt-triage/
├── README.md                  # Pack overview & runnable commands
├── workflow.yaml              # Manifest: single-agent topology, tools, governance budget
├── guides/triage_playbook.md  # Evolvable playbook dynamically loaded by WorkflowRunner
├── contracts.py               # Pydantic BugTriageReport schema
├── tools.py                   # list_subsystems & inspect_java_jwt_package (read-only)
├── sensors.py                 # Tier-1 RealJavaJwtPathSensor (blocks invalid paths & mismatches)
├── repo_index.json            # Standalone index of 48 real auth0/java-jwt Java source files
├── testbed.yaml               # 6 closed issues (#652, #679, #579, #706, #663, #606)
├── resolved_fixes.yaml        # Ground-truth commit fixes (isolated from inference prompts)
└── evolve.py                  # Regression-gated evolution runner (offline or --live)
```

---

## Run Commands

```bash
# Offline evolutionary simulation (0 API keys; uses PlaybookGuidedJavaJwtAdapter + WorkflowRunner)
python3 workflows/java-jwt-triage/evolve.py

# Persist promoted playbook (--apply) or restore Day-1 baseline (--reset)
python3 workflows/java-jwt-triage/evolve.py --apply
python3 workflows/java-jwt-triage/evolve.py --reset

# Live LLM evaluation & optimization
export GEMINI_API_KEY="your-key"
python3 workflows/java-jwt-triage/evolve.py --live -e gemini-2.0-flash
airframe test workflows/java-jwt-triage --live

# Inspect hierarchical W3C span tree from latest run
airframe trace

# Run unit/integration checks for this pack
PYTHONPATH=src pytest tests/unit/test_java_jwt_triage.py -v
```
