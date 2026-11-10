"""Unit tests for the Airframe CLI and semantic assertion verification (C5)."""

from __future__ import annotations

from pathlib import Path
from typer.testing import CliRunner
import yaml

from airframe.cli import app, evaluate_expected_assertions

runner = CliRunner()


def test_cli_version() -> None:
    """Test airframe --version prints version string and exits cleanly."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Airframe version" in result.stdout


def test_cli_info() -> None:
    """Test airframe info displays supported providers."""
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "Airframe Agent Operating System" in result.stdout
    assert "gemini" in result.stdout
    assert "anthropic" in result.stdout
    assert "openai_compatible" in result.stdout


def test_evaluate_expected_assertions_semantics() -> None:
    """Verify strict semantic assertion checks with numeric tolerance and failure diagnostics."""
    # Exact and tolerance pass
    assert evaluate_expected_assertions({"result": 11576.254}, {"result": 11576.25}) == []
    # Numeric divergence exceeds 1e-2
    mismatches = evaluate_expected_assertions({"result": 25.0}, {"result": 9999.0})
    assert len(mismatches) == 1
    assert "9999.0" in mismatches[0] and "25.0" in mismatches[0]
    # Bool mismatch (ensure True != 1 edge case is safe)
    bool_err = evaluate_expected_assertions({"passed": 1}, {"passed": True})
    assert len(bool_err) == 1
    # Missing field fails loudly
    missing_err = evaluate_expected_assertions({"summary": "ok"}, {"expected_score": 0.9})
    assert len(missing_err) == 1
    assert "missing" in missing_err[0].lower()


def test_cli_test_negative_assertion_fails_loudly(tmp_path: Path) -> None:
    """Negative assertion gate: expecting result=9999.0 while mock returns 25.0 MUST exit 1 with field mismatch."""
    wf_dir = tmp_path / "neg_calc"
    wf_dir.mkdir()
    (wf_dir / "contracts.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class CalcResult(BaseModel):\n"
        "    query: str\n"
        "    result: float\n",
        encoding="utf-8",
    )
    (wf_dir / "workflow.yaml").write_text(
        yaml.dump({
            "id": "neg-calc",
            "topology": "single-agent",
            "engine_binding": "gemini-2.0-flash",
            "system_prompt": "Compute math.",
            "testbed": "testbed.yaml",
        }),
        encoding="utf-8",
    )
    (wf_dir / "testbed.yaml").write_text(
        yaml.dump([
            {
                "id": "bad-expectation",
                "description": "Expect impossibly wrong value",
                "input": {"query": "Compute 5*5"},
                "expected_output": {"result": 9999.0},
            }
        ]),
        encoding="utf-8",
    )

    res = runner.invoke(app, ["test", str(wf_dir), "--mock"])
    assert res.exit_code == 1
    flat = " ".join(res.stdout.split())
    assert "FAILED" in flat
    assert "result" in flat
    assert "9999.0" in flat
    assert "25.0" in flat


def test_cli_test_custom_sensor_failure_gate(tmp_path: Path) -> None:
    """Custom Sensor gate: failing workflow sensor causes airframe test to exit 1 despite valid schema."""
    wf_dir = tmp_path / "sensor_gate"
    wf_dir.mkdir()
    (wf_dir / "contracts.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class SimpleOut(BaseModel):\n"
        "    summary: str\n"
        "    status: str = 'SUCCESS'\n",
        encoding="utf-8",
    )
    (wf_dir / "sensors.py").write_text(
        "from airframe.sensors.base import Sensor, SensorResult\n\n"
        "class RejectAllSensor(Sensor):\n"
        "    @property\n"
        "    def name(self) -> str:\n"
        "        return 'mandatory_policy_gate'\n"
        "    @property\n"
        "    def tier(self) -> int:\n"
        "        return 1\n"
        "    async def evaluate(self, candidate, context=None) -> SensorResult:\n"
        "        return SensorResult(\n"
        "            sensor_name=self.name,\n"
        "            tier=self.tier,\n"
        "            passed=False,\n"
        "            message='Policy violation detected in candidate output.',\n"
        "        )\n",
        encoding="utf-8",
    )
    (wf_dir / "workflow.yaml").write_text(
        yaml.dump({
            "id": "sensor-gate-wf",
            "topology": "single-agent",
            "system_prompt": "Produce summary.",
            "testbed": "testbed.yaml",
        }),
        encoding="utf-8",
    )
    (wf_dir / "testbed.yaml").write_text(
        yaml.dump([{"id": "gate-case", "description": "Valid schema but blocked by sensor", "input": {"query": "hi"}}]),
        encoding="utf-8",
    )

    res = runner.invoke(app, ["test", str(wf_dir), "--mock"])
    assert res.exit_code == 1
    flat = " ".join(res.stdout.split())
    assert "FAILED" in flat
    assert "mandatory_policy_gate" in flat
    assert "Policy violation detected" in flat


def test_cli_test_live_requires_credentials(monkeypatch) -> None:
    """Passing --live without provider API key must fail loudly instead of silently mocking."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = runner.invoke(app, ["test", "workflows/pr-reviewer", "--live"])
    assert res.exit_code == 1
    flat = " ".join(res.stdout.split())
    assert "GEMINI_API_KEY" in flat


def test_cli_run_resolves_entrypoint_agent_not_first_blueprint(tmp_path: Path) -> None:
    """Entrypoint gate: workflow with [helper.yaml, primary.yaml] and entrypoint_agent='primary' selects 'primary'."""
    wf_dir = tmp_path / "multi_bp"
    (wf_dir / "blueprints").mkdir(parents=True)
    (wf_dir / "blueprints" / "helper.yaml").write_text(
        yaml.dump({
            "name": "helper",
            "role": "Helper agent",
            "system_prompt": "Helper instructions.",
            "engine_binding": "gemini-2.0-flash",
        }),
        encoding="utf-8",
    )
    (wf_dir / "blueprints" / "primary.yaml").write_text(
        yaml.dump({
            "name": "primary",
            "role": "Primary entrypoint",
            "system_prompt": "Primary instructions.",
            "engine_binding": "gemini-2.0-flash",
        }),
        encoding="utf-8",
    )
    (wf_dir / "workflow.yaml").write_text(
        yaml.dump({
            "id": "multi-bp-wf",
            "topology": "single-agent",
            "entrypoint_agent": "primary",
            "agents": ["blueprints/helper.yaml", "blueprints/primary.yaml"],
        }),
        encoding="utf-8",
    )

    res = runner.invoke(app, ["run", str(wf_dir), "-i", "Run primary", "--mock"])
    assert res.exit_code == 0
    flat = " ".join(res.stdout.split())
    assert "Entrypoint: primary" in flat
    assert "Entrypoint Agent │ primary" in flat or "primary" in flat
