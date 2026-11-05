"""Unit tests for executable guards and multi-tier sensor pipelines."""

import asyncio
import json
import pytest
from pydantic import BaseModel, Field

from airframe.adapters.base import ModelAdapter, ModelResponse
from airframe.guards import ExecutableGuard, GuardViolationError, PayloadBounds
from airframe.sensors import (
    ExitCodeSensor,
    JudgeRubric,
    KeywordSensor,
    ModelJudgeSensor,
    PythonASTSensor,
    RegexSensor,
    RubricCriterion,
    SensorPipeline,
    TokenOverlapSensor,
    ToolCallValidatorSensor,
)


class SampleProfile(BaseModel):
    user_id: int
    username: str
    is_active: bool = True


# ============================================================================
# Executable Guard Tests
# ============================================================================

def test_executable_guard_success():
    """Test ExecutableGuard parses valid json payload."""
    guard = ExecutableGuard(schema=SampleProfile)
    payload = json.dumps({"user_id": 1, "username": "clark_kent"})
    result = guard.validate(payload)
    assert result.user_id == 1
    assert result.username == "clark_kent"
    assert result.is_active is True


def test_executable_guard_byte_ceiling():
    """Test ExecutableGuard rejects payload exceeding byte ceiling."""
    bounds = PayloadBounds(max_bytes=20)
    guard = ExecutableGuard(schema=SampleProfile, bounds=bounds)
    long_payload = json.dumps({"user_id": 1, "username": "this_username_is_way_too_long_for_twenty_bytes"})
    with pytest.raises(GuardViolationError, match="PayloadBounds.MaxBytes"):
        guard.validate(long_payload)


def test_executable_guard_forbidden_substring():
    """Test ExecutableGuard rejects payload with forbidden substring."""
    bounds = PayloadBounds(forbidden_substrings=["SECRET_TOKEN"])
    guard = ExecutableGuard(schema=SampleProfile, bounds=bounds)
    payload = json.dumps({"user_id": 1, "username": "admin_SECRET_TOKEN_leak"})
    with pytest.raises(GuardViolationError, match="PayloadBounds.ForbiddenSubstring"):
        guard.validate(payload)


def test_executable_guard_schema_mismatch():
    """Test ExecutableGuard raises GuardViolationError on missing required fields."""
    guard = ExecutableGuard(schema=SampleProfile)
    invalid_payload = json.dumps({"is_active": True})  # missing user_id and username
    with pytest.raises(GuardViolationError, match="SchemaContractGuard"):
        guard.validate(invalid_payload)


# ============================================================================
# Tier 1 Deterministic Sensor Tests
# ============================================================================

def test_python_ast_sensor():
    """Test PythonASTSensor distinguishes valid and invalid python syntax."""
    sensor = PythonASTSensor()

    valid_code = "def add(a, b):\n    return a + b\n"
    res_valid = asyncio.run(sensor.evaluate(valid_code))
    assert res_valid.passed is True

    invalid_code = "def broken(a, b\n    return a + b"
    res_invalid = asyncio.run(sensor.evaluate(invalid_code))
    assert res_invalid.passed is False
    assert "SyntaxError" in res_invalid.message


def test_regex_sensor():
    """Test RegexSensor handles must_match=True and must_match=False."""
    sensor_must = RegexSensor(name="must_have_email", pattern=r"[\w\.-]+@[\w\.-]+", must_match=True)
    res_pass = asyncio.run(sensor_must.evaluate("Contact: user@example.com"))
    assert res_pass.passed is True

    res_fail = asyncio.run(sensor_must.evaluate("No email here."))
    assert res_fail.passed is False

    sensor_forbidden = RegexSensor(name="no_eval", pattern=r"\beval\(", must_match=False)
    res_forbidden_fail = asyncio.run(sensor_forbidden.evaluate("val = eval('2+2')"))
    assert res_forbidden_fail.passed is False

    res_forbidden_pass = asyncio.run(sensor_forbidden.evaluate("val = int('2')"))
    assert res_forbidden_pass.passed is True


def test_keyword_and_exit_code_sensors():
    """Test KeywordSensor and ExitCodeSensor."""
    kw_sensor = KeywordSensor(name="req_words", required_keywords=["status", "completed"])
    res_kw_pass = asyncio.run(kw_sensor.evaluate("Status: task completed."))
    assert res_kw_pass.passed is True

    res_kw_fail = asyncio.run(kw_sensor.evaluate("Status: pending."))
    assert res_kw_fail.passed is False

    exit_sensor = ExitCodeSensor()
    assert asyncio.run(exit_sensor.evaluate(0)).passed is True
    assert asyncio.run(exit_sensor.evaluate(1)).passed is False
    assert asyncio.run(exit_sensor.evaluate({"exit_code": 0})).passed is True


# ============================================================================
# Tier 2 Semantic Sensor Tests
# ============================================================================

def test_tool_call_validator_sensor():
    """Test ToolCallValidatorSensor checks whitelisted tools and required parameters."""
    sensor = ToolCallValidatorSensor(
        allowed_tools=["read_file", "edit_file"],
        required_params={"edit_file": ["file_path", "replacement"]},
    )

    # Valid tool invocation
    valid_call = {"tool_name": "edit_file", "arguments": {"file_path": "main.py", "replacement": "x = 1"}}
    assert asyncio.run(sensor.evaluate(valid_call)).passed is True

    # Disallowed tool
    disallowed_call = {"tool_name": "drop_database", "arguments": {}}
    res_disallowed = asyncio.run(sensor.evaluate(disallowed_call))
    assert res_disallowed.passed is False
    assert "Disallowed tool" in res_disallowed.message

    # Missing required argument
    missing_arg_call = {"tool_name": "edit_file", "arguments": {"file_path": "main.py"}}
    res_missing = asyncio.run(sensor.evaluate(missing_arg_call))
    assert res_missing.passed is False
    assert "missing required arguments" in res_missing.message


def test_token_overlap_sensor():
    """Test TokenOverlapSensor Jaccard calculation."""
    sensor = TokenOverlapSensor(min_similarity=0.4)
    candidate = "The swift brown fox jumped over the fence."
    context = {"reference": "The quick brown fox jumps over the garden fence."}

    res = asyncio.run(sensor.evaluate(candidate, context))
    assert res.passed is True
    assert res.diagnostics["similarity"] >= 0.4


# ============================================================================
# Tier 3 & Pipeline Integration Tests
# ============================================================================

def test_sensor_pipeline_fast_fail_short_circuit():
    """Test that when a Tier 1 sensor fails, Tier 3 LLM judge is SKIPPED to save tokens."""
    judge_called = False

    class MockJudgeAdapter(ModelAdapter):
        @property
        def provider_name(self) -> str:
            return "mock"

        async def invoke(self, prompt: str, **kwargs) -> ModelResponse:
            nonlocal judge_called
            judge_called = True
            return ModelResponse(content="", model_name="mock", provider="mock")

    rubric = JudgeRubric(
        rubric_id="code-quality",
        description="Grades code style",
        criteria=[RubricCriterion(name="readability", description="Clean formatting")],
        passing_score=0.8,
    )
    judge_sensor = ModelJudgeSensor(name="llm_judge", adapter=MockJudgeAdapter("mock"), rubric=rubric)

    # Pipeline with failing Tier 1 and Tier 3 judge
    pipeline = SensorPipeline([
        PythonASTSensor(),  # Tier 1
        judge_sensor,       # Tier 3
    ])

    broken_code = "def syntax_error(:"
    results = asyncio.run(pipeline.run(broken_code, short_circuit=True))

    assert len(results) == 2
    # Tier 1 failed
    assert results[0].tier == 1
    assert results[0].passed is False

    # Tier 3 was skipped!
    assert results[1].tier == 3
    assert results[1].passed is False
    assert results[1].diagnostics.get("skipped") is True
    assert judge_called is False  # Verified zero judge LLM invocations!


def test_schema_sensor_and_guard_adaptation():
    """Test SchemaSensor validates JSON and integrates with ExecutableGuard.to_sensor()."""
    from airframe.sensors.schema import SchemaSensor

    guard = ExecutableGuard(schema=SampleProfile)
    sensor = guard.to_sensor()
    assert isinstance(sensor, SchemaSensor)
    assert sensor.tier == 1

    # Valid payload
    valid_res = asyncio.run(sensor.evaluate(json.dumps({"user_id": 99, "username": "batman"})))
    assert valid_res.passed is True
    assert valid_res.diagnostics["data"]["username"] == "batman"

    # Invalid payload (missing username)
    invalid_res = asyncio.run(sensor.evaluate(json.dumps({"user_id": 99})))
    assert invalid_res.passed is False
    assert "validation failed" in invalid_res.message.lower()
