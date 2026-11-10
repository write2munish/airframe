"""Tests validating multi-agent showcase workflow packs and CLI trace visualization."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from airframe.cli import MockExecutionAdapter, app
from airframe.runner import WorkflowRunner
from airframe.telemetry import AirframeTracer
from airframe.workflows.loader import load_workflow

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


def test_cli_trace_renders_tree_from_file_id_prefix_and_latest(tmp_path: Path) -> None:
    """Verify airframe trace inspects W3C JSONL trace trees via file path, ID prefix, latest, and --json."""
    pack = load_workflow(REPO_ROOT / "workflows" / "code-auditor-swarm")
    wf_runner = WorkflowRunner(trace_dir=tmp_path)
    run_res = asyncio.run(
        wf_runner.run(
            pack=pack,
            input_query="Audit auth helpers",
            adapter=MockExecutionAdapter(),
            sensors=pack.load_sensors(),
        )
    )
    assert run_res.trace_file and run_res.trace_id
    trace_file = Path(run_res.trace_file)
    assert trace_file.is_file()

    # 1. Explicit trace file path
    file_res = runner.invoke(app, ["trace", str(trace_file)])
    assert file_res.exit_code == 0, file_res.stdout
    assert "workflow.code-auditor-swarm (root)" in file_res.stdout
    assert "supervisor.audit-lead (supervisor)" in file_res.stdout
    assert "(worker)" in file_res.stdout
    assert "sensor.pipeline (sensor)" in file_res.stdout

    # 2. Trace ID prefix lookup in --dir
    prefix_res = runner.invoke(app, ["trace", run_res.trace_id[:8], "--dir", str(tmp_path)])
    assert prefix_res.exit_code == 0, prefix_res.stdout
    assert run_res.trace_id in prefix_res.stdout

    # 3. Default latest lookup (no target argument)
    latest_res = runner.invoke(app, ["trace", "--dir", str(tmp_path)])
    assert latest_res.exit_code == 0, latest_res.stdout
    assert "workflow.code-auditor-swarm (root)" in latest_res.stdout

    # 4. JSON output mode
    json_res = runner.invoke(app, ["trace", "--dir", str(tmp_path), "--json"])
    assert json_res.exit_code == 0, json_res.stdout
    parsed = json.loads(json_res.stdout)
    assert parsed["trace_id"] == run_res.trace_id
    assert parsed["span_count"] >= 5
    assert parsed["roots"][0]["name"] == "workflow.code-auditor-swarm"

    # 5. Missing trace fails cleanly
    missing_res = runner.invoke(app, ["trace", "does-not-exist", "--dir", str(tmp_path)])
    assert missing_res.exit_code == 1
    assert "not found" in missing_res.stdout.lower()


def test_cli_run_with_trace_flag_and_default_trace_cmd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify airframe run --trace prints the execution trace tree and populates default airframe trace."""
    monkeypatch.chdir(tmp_path)
    run_res = runner.invoke(
        app,
        [
            "run",
            str(REPO_ROOT / "workflows" / "code-auditor-swarm"),
            "-i",
            "Audit auth module",
            "--mock",
            "--trace",
        ],
    )
    assert run_res.exit_code == 0, run_res.stdout
    assert "Trace Tree" in run_res.stdout
    assert "workflow.code-auditor-swarm (root)" in run_res.stdout
    assert "supervisor.audit-lead (supervisor)" in run_res.stdout

    # Subsequent bare `airframe trace` picks up `.airframe/traces/<latest>.jsonl`
    trace_res = runner.invoke(app, ["trace"])
    assert trace_res.exit_code == 0, trace_res.stdout
    assert "workflow.code-auditor-swarm (root)" in trace_res.stdout


def test_cli_trace_renders_error_span_with_bracket_escaping(tmp_path: Path) -> None:
    """Verify ERROR spans and literal brackets inside error messages render safely."""
    tracer = AirframeTracer(trace_dir=tmp_path, trace_id="errtrace12345678")
    with pytest.raises(RuntimeError, match="boom"):
        with tracer.start_span("root.fail", kind="root"):
            with tracer.start_span("tool.bad", kind="tool"):
                raise RuntimeError("boom [unclosed-bracket]")

    res = runner.invoke(app, ["trace", "--dir", str(tmp_path)])
    assert res.exit_code == 0, res.stdout
    assert "root.fail (root)" in res.stdout
    assert "tool.bad (tool)" in res.stdout
    assert "ERROR" in res.stdout
    assert "[unclosed-bracket]" in res.stdout
