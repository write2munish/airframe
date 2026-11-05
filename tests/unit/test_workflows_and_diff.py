"""Unit tests for workflow pack loading, differential regression diffs, and CLI commands."""

from pathlib import Path
import pytest
from typer.testing import CliRunner

from airframe.cli import app
from airframe.telemetry.diff import CaseResult, RunScorecard, compare_scorecards
from airframe.workflows.loader import init_workflow_scaffold, load_workflow

runner = CliRunner()


def test_workflow_pack_loading():
    """Test loading the reference workflows/pr-reviewer pack."""
    pack = load_workflow("workflows/pr-reviewer")
    assert pack.id == "pr-reviewer"
    assert pack.manifest.topology == "single-agent"
    assert len(pack.manifest.agents) == 1

    bps = pack.load_blueprints()
    assert len(bps) == 1
    assert bps[0].name == "code-reviewer"
    assert "read_file" in bps[0].allowed_tools

    testbed = pack.get_testbed_path()
    assert testbed is not None
    assert testbed.name == "smoke.yaml"


def test_workflow_scaffolding(tmp_path: Path):
    """Test init_workflow_scaffold generates a clean boilerplate workflow pack."""
    scaffold_dir = init_workflow_scaffold(tmp_path, name="customer-support")
    assert (scaffold_dir / "workflow.yaml").exists()
    assert (scaffold_dir / "blueprints" / "worker.yaml").exists()
    assert (scaffold_dir / "guides" / "task_spec.md").exists()
    assert (scaffold_dir / "guards" / "contracts.py").exists()
    assert (scaffold_dir / "testbed" / "smoke.yaml").exists()

    loaded = load_workflow(scaffold_dir)
    assert loaded.id == "customer-support"


def test_differential_regression_comparison():
    """Test compare_scorecards correctly identifies regressions and improvements."""
    base_cases = [
        CaseResult(case_id="case-1", passed=True, latency_ms=300.0, tokens=500),
        CaseResult(case_id="case-2", passed=True, latency_ms=400.0, tokens=600),
        CaseResult(case_id="case-3", passed=False, latency_ms=500.0, tokens=700),
    ]
    cand_cases = [
        CaseResult(case_id="case-1", passed=True, latency_ms=250.0, tokens=450),
        CaseResult(case_id="case-2", passed=False, latency_ms=350.0, tokens=550),  # REGRESSION!
        CaseResult(case_id="case-3", passed=True, latency_ms=400.0, tokens=600),   # IMPROVEMENT!
    ]

    base_card = RunScorecard.from_results("gemini-2.0-flash", base_cases)
    cand_card = RunScorecard.from_results("claude-3-5-sonnet", cand_cases)

    diff = compare_scorecards(base_card, cand_card)

    assert diff.has_regressions is True
    assert "case-2" in diff.regressions
    assert "case-3" in diff.improvements
    assert diff.latency_delta_ms < 0  # Candidate was faster
    assert diff.token_delta < 0       # Candidate used fewer tokens

    # Markdown rendering verification
    md = diff.render_markdown()
    assert "Regressions" in md
    assert "case-2" in md
    assert "case-3" in md


def test_cli_workflow_commands(tmp_path: Path):
    """Test CLI test, compare, run, and init commands."""
    # 1. airframe test workflows/pr-reviewer
    res_test = runner.invoke(app, ["test", "workflows/pr-reviewer"])
    assert res_test.exit_code == 0
    assert "Testbed Results: pr-reviewer" in res_test.stdout

    # 2. airframe compare workflows/pr-reviewer
    res_comp = runner.invoke(
        app,
        ["compare", "workflows/pr-reviewer", "--baseline", "gemini-2.0-flash", "--candidate", "claude-3-5-sonnet"],
    )
    assert res_comp.exit_code == 0
    assert "Differential Regression Analysis" in res_comp.stdout

    # 3. airframe run workflows/pr-reviewer
    res_run = runner.invoke(app, ["run", "workflows/pr-reviewer", "--input", "pr_diff_patch"])
    assert res_run.exit_code == 0
    assert "Executing Workflow: pr-reviewer" in res_run.stdout

    # 4. airframe init workflow new-agent
    res_init = runner.invoke(app, ["init", "workflow", "new-agent", "--dir", str(tmp_path)])
    assert res_init.exit_code == 0
    assert "Created workflow pack" in res_init.stdout
    assert (tmp_path / "new-agent" / "workflow.yaml").exists()
