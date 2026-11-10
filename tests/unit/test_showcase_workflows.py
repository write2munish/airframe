"""Tests validating multi-agent showcase workflow packs and the interactive walkthrough."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from typer.testing import CliRunner

from airframe.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_patch_verifier_evaluator_optimizer_cli_run_and_test() -> None:
    """Verify workflows/patch-verifier (evaluator-optimizer) passes run and test in --mock mode."""
    run_res = runner.invoke(
        app,
        ["run", str(REPO_ROOT / "workflows" / "patch-verifier"), "-i", "Write double(x)", "--mock"],
    )
    assert run_res.exit_code == 0, run_res.stdout
    assert "Topology: evaluator-optimizer" in " ".join(run_res.stdout.split())
    assert "VerifiedPatchReport" in run_res.stdout

    test_res = runner.invoke(
        app,
        ["test", str(REPO_ROOT / "workflows" / "patch-verifier"), "--mock"],
    )
    assert test_res.exit_code == 0, test_res.stdout
    assert "PASSED" in test_res.stdout


def test_code_auditor_swarm_orchestrator_workers_cli_run_and_test() -> None:
    """Verify workflows/code-auditor-swarm selects audit-lead entrypoint and passes testbed."""
    run_res = runner.invoke(
        app,
        ["run", str(REPO_ROOT / "workflows" / "code-auditor-swarm"), "-i", "Audit auth utils", "--mock"],
    )
    assert run_res.exit_code == 0, run_res.stdout
    flat = " ".join(run_res.stdout.split())
    assert "Topology: orchestrator-workers" in flat
    assert "Entrypoint: audit-lead" in flat
    assert "SwarmAuditReport" in run_res.stdout

    test_res = runner.invoke(
        app,
        ["test", str(REPO_ROOT / "workflows" / "code-auditor-swarm"), "--mock"],
    )
    assert test_res.exit_code == 0, test_res.stdout
    assert "PASSED" in test_res.stdout


def test_interactive_showcase_walkthrough_script() -> None:
    """Verify examples/showcase_walkthrough.py executes all 5 offline showcase acts cleanly."""
    script_path = REPO_ROOT / "examples" / "showcase_walkthrough.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (f"{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "All 5 Showcase Acts completed cleanly" in proc.stdout
    assert "workflow.code-auditor-swarm (root)" in proc.stdout
