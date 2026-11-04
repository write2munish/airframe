"""Unit tests for the Airframe CLI."""

from typer.testing import CliRunner

from airframe.cli import app

runner = CliRunner()


def test_cli_version():
    """Test airframe --version prints version string and exits cleanly."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Airframe version" in result.stdout


def test_cli_info():
    """Test airframe info displays supported providers."""
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "Airframe Agent Operating System" in result.stdout
    assert "gemini" in result.stdout
    assert "anthropic" in result.stdout
    assert "openai_compatible" in result.stdout
