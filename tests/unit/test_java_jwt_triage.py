"""Unit and integration tests for workflows/java-jwt-triage and measurable self-improvement."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest
import yaml

from airframe.telemetry import build_trace_tree, load_trace
from airframe.workflows.loader import load_workflow

REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "workflows" / "java-jwt-triage"


def _load_pack_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_java_jwt_pack_loads_tools_and_manifest() -> None:
    """Verify java-jwt-triage pack structure, 48-file repo index, and leak-free testbed."""
    pack = load_workflow(PACK_DIR)
    assert pack.id == "java-jwt-triage"
    assert pack.manifest.topology == "single-agent"
    assert pack.manifest.entrypoint_agent == "bug-triager"

    bps = pack.load_blueprints()
    assert len(bps) == 1
    assert bps[0].name == "bug-triager"
    assert set(bps[0].allowed_tools) == {"list_subsystems", "inspect_java_jwt_package"}

    tools_mod = _load_pack_module("java_jwt_tools", PACK_DIR / "tools.py")
    subsystems = tools_mod.list_subsystems()
    assert sum(subsystems.values()) == 48
    assert "impl-codec" in subsystems and "core-verifier" in subsystems

    codec_files = tools_mod.inspect_java_jwt_package("impl-codec")
    codec_paths = {item["path"] for item in codec_files}
    assert "lib/src/main/java/com/auth0/jwt/impl/PayloadDeserializer.java" in codec_paths

    cases = yaml.safe_load((PACK_DIR / "testbed.yaml").read_text(encoding="utf-8")) or []
    fixes = yaml.safe_load((PACK_DIR / "resolved_fixes.yaml").read_text(encoding="utf-8")) or {}
    assert len(cases) == 6
    for case in cases:
        cid = case["id"]
        assert cid in fixes
        assert case["expected_output"]["suspected_file"] == fixes[cid]["fixed_file"]
        # Ensure zero target-file leakage into case prompts
        assert fixes[cid]["fixed_file"] not in case["input"]["query"]


def test_real_java_jwt_path_sensor_rejects_hallucinations_and_mismatches() -> None:
    """Tier 1 RealJavaJwtPathSensor rejects invalid paths and cross-subsystem contradictions."""
    sensors_mod = _load_pack_module("java_jwt_sensors", PACK_DIR / "sensors.py")
    sensor = sensors_mod.RealJavaJwtPathSensor()

    ok = asyncio.run(
        sensor.evaluate(
            {
                "subsystem": "core-token",
                "suspected_file": "lib/src/main/java/com/auth0/jwt/TokenUtils.java",
                "rationale": "Null check before dot-split.",
            }
        )
    )
    assert ok.passed is True

    fake_path = asyncio.run(
        sensor.evaluate(
            {
                "subsystem": "core-token",
                "suspected_file": "lib/src/main/java/com/auth0/jwt/DoesNotExist.java",
                "rationale": "Hallucinated.",
            }
        )
    )
    assert fake_path.passed is False
    assert "not in auth0/java-jwt" in fake_path.message

    mock_path = asyncio.run(
        sensor.evaluate(
            {
                "subsystem": "core-verifier",
                "suspected_file": "Verified suspected_file",
                "rationale": "Generic mock string.",
            }
        )
    )
    assert mock_path.passed is False

    mismatch = asyncio.run(
        sensor.evaluate(
            {
                "subsystem": "core-verifier",
                "suspected_file": "lib/src/main/java/com/auth0/jwt/impl/PayloadDeserializer.java",
                "rationale": "Contradictory subsystem label.",
            }
        )
    )
    assert mismatch.passed is False
    assert "Subsystem mismatch" in mismatch.message


def test_java_jwt_evolution_measurable_progression_and_regression_gate(tmp_path: Path) -> None:
    """Verify Baseline 3/6 -> Over-Broad REJECTED (-2 reg) -> Refined PROMOTED 6/6 (+3, 0 reg)."""
    evolve = _load_pack_module("java_jwt_evolve", PACK_DIR / "evolve.py")
    res = asyncio.run(evolve.run_evolution(live=False, apply=False, trace_dir=tmp_path))

    assert res.applied_to_disk is False
    assert len(res.epochs) == 3

    ep0, ep1, ep2 = res.epochs
    assert ep0.scorecard.passed_cases == 3
    assert ep0.scorecard.total_cases == 6
    assert ep0.decision == "BASELINE"

    # Epoch 1 must be rejected despite 4/6 > 3/6 because it introduces regressions
    assert ep1.scorecard.passed_cases == 4
    assert ep1.diff is not None and ep1.diff.has_regressions is True
    assert set(ep1.diff.regressions) == {"gh-652-exp-equals-now", "gh-679-empty-expected-aud"}
    assert ep1.decision.startswith("REJECTED")

    # Epoch 2 must promote cleanly: 6/6 passed, +3 improvements, 0 regressions, concise guide
    assert ep2.scorecard.passed_cases == 6
    assert ep2.diff is not None and ep2.diff.has_regressions is False
    assert set(ep2.diff.improvements) == {
        "gh-706-scientific-exp-overflow",
        "gh-663-empty-aud-string",
        "gh-606-null-token-split",
    }
    assert ep2.decision == "PROMOTED"
    assert ep2.playbook_tokens < 500

    # Verify real W3C span tree captured tool execution & Tier-1 sensor check
    assert ep2.last_trace_file is not None
    spans = load_trace(ep2.last_trace_file)
    span_names = {s.name for s in spans}
    assert "workflow.java-jwt-triage" in span_names
    assert "tool.inspect_java_jwt_package" in span_names
    assert "sensor.pipeline" in span_names
    tree = build_trace_tree(spans)
    assert tree["span_count"] >= 5


def test_java_jwt_playbook_adapter_reads_active_rules_dynamically() -> None:
    """Ensure PlaybookGuidedJavaJwtAdapter parses rules from system_prompt without hardcoded answers."""
    evolve = _load_pack_module("java_jwt_evolve", PACK_DIR / "evolve.py")
    adapter = evolve.PlaybookGuidedJavaJwtAdapter()

    custom_guide = (
        "## Routing Rules\n"
        "- Rule `custom-test`: terms [`customsignal`] -> `core-token` | "
        "`lib/src/main/java/com/auth0/jwt/RegisteredClaims.java`\n"
    )
    prompt = "[TASK]\nIssue involving customsignal during parsing.\n\n[AVAILABLE TOOLS]\n[]"
    resp1 = asyncio.run(adapter.invoke(prompt, system_prompt=custom_guide))
    d1 = json.loads(resp1.content.strip("`json\n "))
    assert d1["action"] == "call_tool"
    assert d1["arguments"]["subsystem"] == "core-token"

    turn2_prompt = prompt + "\nTool 'inspect_java_jwt_package' returned: []"
    resp2 = asyncio.run(adapter.invoke(turn2_prompt, system_prompt=custom_guide))
    d2 = json.loads(resp2.content.strip("`json\n "))
    assert d2["action"] == "final_answer"
    assert d2["final_output"]["suspected_file"] == "lib/src/main/java/com/auth0/jwt/RegisteredClaims.java"


def test_java_jwt_apply_and_reset_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify apply persists promoted playbook and --reset restores Day-1 baseline cleanly."""
    evolve = _load_pack_module("java_jwt_evolve", PACK_DIR / "evolve.py")

    staged = tmp_path / "java-jwt-triage"
    staged.mkdir()
    shutil.copytree(PACK_DIR / "guides", staged / "guides")
    for rel in ("workflow.yaml", "contracts.py", "tools.py", "sensors.py", "repo_index.json", "testbed.yaml", "resolved_fixes.yaml"):
        shutil.copy2(PACK_DIR / rel, staged / rel)

    monkeypatch.setattr(evolve, "PACK_DIR", staged)

    res = asyncio.run(evolve.run_evolution(live=False, apply=True, trace_dir=tmp_path / "traces"))
    assert res.applied_to_disk is True
    promoted_on_disk = (staged / "guides" / "triage_playbook.md").read_text(encoding="utf-8")
    assert "PayloadDeserializer" in promoted_on_disk
    assert "TokenUtils" in promoted_on_disk

    monkeypatch.setattr(sys, "argv", ["evolve.py", "--reset"])
    evolve.main()
    restored = (staged / "guides" / "triage_playbook.md").read_text(encoding="utf-8")
    assert restored == evolve.BASELINE_PLAYBOOK
