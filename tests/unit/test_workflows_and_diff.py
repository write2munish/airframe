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


def test_lightweight_single_file_workflow(tmp_path: Path):
    """Test single-file inline workflow without requiring 6 mandatory subdirectories."""
    import yaml

    from airframe.governance.circuit_breakers import Budget

    # 1. Test lightweight scaffold option
    light_dir = init_workflow_scaffold(tmp_path, name="simple-task", lightweight=True)
    assert (light_dir / "workflow.yaml").exists()
    assert (light_dir / "tools.py").exists()
    assert not (light_dir / "blueprints").exists()  # Clean: no empty subdirectories!

    pack = load_workflow(light_dir)
    assert pack.id == "simple-task"
    bps = pack.load_blueprints()
    assert len(bps) == 1
    assert bps[0].name == "worker"
    assert "sample_tool" in bps[0].allowed_tools

    # 2. Test direct single-file YAML loading with Budget merging
    custom_yaml = tmp_path / "inline_calculator.yaml"
    custom_yaml.write_text(
        yaml.dump({
            "id": "inline-calc",
            "topology": "single-agent",
            "system_prompt": "You are a fast calculator assistant.",
            "tools": ["calculate"],
            "governance": {
                "max_turns": 5,
                "timeout_seconds": 60,
                "max_budget_usd": 0.50,
            },
        }),
        encoding="utf-8",
    )

    calc_pack = load_workflow(custom_yaml)
    assert calc_pack.id == "inline-calc"
    calc_bps = calc_pack.load_blueprints()
    assert len(calc_bps) == 1
    assert calc_bps[0].system_prompt == "You are a fast calculator assistant."
    assert calc_bps[0].budget.max_turns == 5
    assert calc_bps[0].budget.max_cost_usd == 0.50

    # 3. Test budget merge_with_override
    wf_budget = Budget(max_tokens=100_000, max_turns=10, max_cost_usd=2.0)
    agent_override = Budget(max_turns=3)
    merged = wf_budget.merge_with_override(agent_override)
    assert merged.max_tokens == 100_000
    assert merged.max_turns == 3
    assert merged.max_cost_usd == 2.0


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
    # 1. airframe test workflows/pr-reviewer --mock
    res_test = runner.invoke(app, ["test", "workflows/pr-reviewer", "--mock"])
    assert res_test.exit_code == 0, res_test.stdout
    assert "Testbed Results: pr-reviewer" in res_test.stdout

    # 2. airframe compare workflows/pr-reviewer --mock
    res_comp = runner.invoke(
        app,
        [
            "compare",
            "workflows/pr-reviewer",
            "--baseline",
            "gemini-2.0-flash",
            "--candidate",
            "claude-3-5-sonnet",
            "--mock",
        ],
    )
    assert res_comp.exit_code == 0, res_comp.stdout
    assert "Differential Regression Analysis" in res_comp.stdout
    assert "DRY-RUN MOCK MODE" in res_comp.stdout

    # 3. airframe run workflows/pr-reviewer --mock
    res_run = runner.invoke(app, ["run", "workflows/pr-reviewer", "--input", "pr_diff_patch", "--mock"])
    assert res_run.exit_code == 0, res_run.stdout
    assert "Executing Workflow: pr-reviewer" in res_run.stdout

    # 4. airframe run workflows/calculator --mock (verifies CompoundInterestMathSensor passes)
    res_calc = runner.invoke(
        app,
        [
            "run",
            "workflows/calculator",
            "-i",
            "Calculate compound interest on ₹10,000 at 5% for 3 years",
            "--mock",
        ],
    )
    assert res_calc.exit_code == 0, res_calc.stdout
    assert "Validated Contract: CompoundInterestContract" in res_calc.stdout
    assert "1576.25" in res_calc.stdout

    # 5. airframe init workflow new-agent
    res_init = runner.invoke(app, ["init", "workflow", "new-agent", "--dir", str(tmp_path)])
    assert res_init.exit_code == 0, res_init.stdout
    assert "Created workflow pack" in res_init.stdout
    assert (tmp_path / "new-agent" / "workflow.yaml").exists()


def test_cli_compare_mock_ignores_ambient_keys_and_rejects_mixed_live_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --mock stays offline when API keys exist, while mixed live/mock fails explicitly."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Explicit --mock forces both sides to MockExecutionAdapter and exits 0.
    res_mock = runner.invoke(
        app,
        [
            "compare",
            "workflows/pr-reviewer",
            "--baseline",
            "gemini-2.0-flash",
            "--candidate",
            "claude-3-5-sonnet",
            "--mock",
        ],
    )
    assert res_mock.exit_code == 0, res_mock.stdout
    assert "DRY-RUN MOCK MODE" in res_mock.stdout

    # Without --mock, comparing a live baseline against a mock candidate must fail cleanly.
    res_mixed = runner.invoke(
        app,
        [
            "compare",
            "workflows/pr-reviewer",
            "--baseline",
            "gemini-2.0-flash",
            "--candidate",
            "claude-3-5-sonnet",
        ],
    )
    assert res_mixed.exit_code == 1
    assert "Mixed live/mock comparison is invalid" in " ".join(res_mixed.stdout.split())


def test_cli_compare_exits_1_on_regression_and_records_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify airframe compare exits 1 on regression and populates CaseResult.error."""
    import airframe.cli as cli_mod

    captured_candidates: list[RunScorecard] = []
    real_compare = compare_scorecards

    class _RegressingMockAdapter(cli_mod.MockExecutionAdapter):
        async def invoke(self, prompt: str, system_prompt: str | None = None, **kwargs):
            if "SELECT * FROM users" in prompt:
                return cli_mod.ModelResponse(
                    content='{"summary": "All good", "passed": true, "issues": []}',
                    model_name=self.model_name,
                    provider="mock",
                )
            return await super().invoke(prompt, system_prompt=system_prompt, **kwargs)

    def _fake_resolve(engine_name: str, *, mock: bool = False, live: bool = False):
        if "candidate" in engine_name:
            return _RegressingMockAdapter(model_name=engine_name), True
        return cli_mod.MockExecutionAdapter(model_name=engine_name), True

    def _spy_compare(base_sc: RunScorecard, cand_sc: RunScorecard):
        captured_candidates.append(cand_sc)
        return real_compare(base_sc, cand_sc)

    monkeypatch.setattr(cli_mod, "_resolve_cli_adapter", _fake_resolve)
    monkeypatch.setattr(cli_mod, "compare_scorecards", _spy_compare)

    res = runner.invoke(
        app,
        ["compare", "workflows/pr-reviewer", "-b", "base-engine", "-c", "candidate-engine", "--mock"],
    )
    assert res.exit_code == 1
    assert "Regressions detected" in res.stdout
    assert len(captured_candidates) == 1
    failed = [r for r in captured_candidates[0].results if not r.passed]
    assert len(failed) == 1
    assert failed[0].error is not None and "BLOCKER" in failed[0].error


def test_workflow_pack_tool_and_sensor_discovery():
    """Verify WorkflowPack.load_tools() and WorkflowPack.load_sensors() discover components automatically."""
    pr_pack = load_workflow("workflows/pr-reviewer")
    pr_sensors = pr_pack.load_sensors()
    assert any(s.name == "blocker_consistency" for s in pr_sensors)

    calc_pack = load_workflow("workflows/calculator")
    calc_tools = calc_pack.load_tools()
    assert "calculate_compound_interest" in calc_tools
    calc_sensors = calc_pack.load_sensors()
    assert any(s.name == "compound_interest_math_validator" for s in calc_sensors)

    weather_pack = load_workflow("workflows/weather-forecaster")
    weather_tools = weather_pack.load_tools()
    assert "geocode_location" in weather_tools
    assert "get_hourly_forecast" in weather_tools
    weather_sensors = weather_pack.load_sensors(
        case_input={"expected_lat": 12.9716, "expected_lon": 77.5946, "expected_hour": 15}
    )
    sensor_names = {s.name for s in weather_sensors}
    assert "coordinate_accuracy_validator" in sensor_names
    assert "payload_pruning_validator" in sensor_names
    assert "zero_hallucination_weather_validator" in sensor_names
