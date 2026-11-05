"""Unit tests for the Deterministic Weather Forecasting workflow pack.

Tests multi-step parameter extraction, geocoding resolution, payload pruning,
and zero-hallucination sensor verification.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import pytest
from typer.testing import CliRunner

# Add workflow pack directory to sys.path
wf_path = Path(__file__).resolve().parent.parent.parent / "workflows" / "weather-forecaster"
if str(wf_path) not in sys.path:
    sys.path.insert(0, str(wf_path))

from airframe.cli import app
from airframe.sensors import SensorPipeline
from airframe.workflows.loader import load_workflow
from guards.contracts import WeatherForecastReport
from sensors.deterministic import (
    CoordinateAccuracySensor,
    PayloadPruningSensor,
    ToolSequenceSensor,
    ZeroHallucinationWeatherSensor,
)
from tools.weather_tools import (
    geocode_location,
    get_hourly_forecast,
)

runner = CliRunner()


def test_weather_workflow_pack_loading():
    """Verify weather-forecaster workflow pack loads and parses manifest correctly."""
    pack = load_workflow("workflows/weather-forecaster")
    assert pack.id == "weather-forecaster"
    assert pack.manifest.topology == "single-agent"
    assert len(pack.manifest.agents) == 1

    bps = pack.load_blueprints()
    assert len(bps) == 1
    assert bps[0].name == "meteorology-agent"
    assert "geocode_location" in bps[0].allowed_tools
    assert "get_hourly_forecast" in bps[0].allowed_tools


def test_weather_tools_with_mock_client():
    """Test geocode_location and get_hourly_forecast with mocked Open-Meteo payloads."""
    import httpx

    # Mock geocode response
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "search" in url_str:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Bengaluru",
                            "country": "India",
                            "latitude": 12.9716,
                            "longitude": 77.5946,
                            "timezone": "Asia/Kolkata",
                        }
                    ]
                },
            )
        elif "forecast" in url_str:
            # Generate 168 hours of mock timestamps
            times = [f"2026-11-06T{h:02d}:00" for h in range(24)]
            precip_prob = [10] * 24
            precip_prob[15] = 75  # 75% rain at 3 PM
            return httpx.Response(
                200,
                json={
                    "hourly": {
                        "time": times,
                        "temperature_2m": [26.5] * 24,
                        "precipitation": [0.0] * 24,
                        "precipitation_probability": precip_prob,
                    }
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

    # 1. Geocode location
    geo_res = asyncio.run(geocode_location("Bengaluru", client=client))
    assert geo_res["name"] == "Bengaluru"
    assert abs(geo_res["latitude"] - 12.9716) < 0.01
    assert abs(geo_res["longitude"] - 77.5946) < 0.01

    # 2. Hourly forecast with payload pruning
    forecast = asyncio.run(
        get_hourly_forecast(
            latitude=geo_res["latitude"],
            longitude=geo_res["longitude"],
            target_date_iso="2026-11-06",
            target_hour=15,
            client=client,
        )
    )
    assert forecast["temperature_celsius"] == 26.5
    assert forecast["precipitation_probability_pct"] == 75
    assert forecast["will_it_rain"] is True
    # Pruned from 24/168 hours to a single hour dict!
    assert "time" not in forecast  # The raw list was pruned


def test_weather_deterministic_sensors():
    """Test deterministic sensor suite: tool sequence, coordinate accuracy, pruning, and zero-hallucination."""
    tool_sequence = ["geocode_location", "get_hourly_forecast"]
    tool_sensor = ToolSequenceSensor(expected_sequence=["geocode_location", "get_hourly_forecast"])
    res_seq = asyncio.run(tool_sensor.evaluate(tool_sequence))
    assert res_seq.passed is True

    # Coordinate accuracy sensor
    coord_sensor = CoordinateAccuracySensor(
        expected_lat=12.9716, expected_lon=77.5946, expected_hour=15, tolerance=0.1
    )
    sample_report = WeatherForecastReport(
        location_name="Bengaluru",
        country="India",
        latitude=12.9716,
        longitude=77.5946,
        target_date="2026-11-06",
        target_hour=15,
        temperature_celsius=26.5,
        precipitation_mm=0.0,
        precipitation_probability_pct=75,
        will_it_rain=True,
        summary="75% chance of rain in Bengaluru tomorrow at 3 PM.",
    )
    res_coord = asyncio.run(coord_sensor.evaluate(sample_report))
    assert res_coord.passed is True

    # Payload pruning sensor
    pruning_sensor = PayloadPruningSensor(max_tokens=300)
    pruned_data = {
        "timestamp": "2026-11-06T15:00",
        "temperature_celsius": 26.5,
        "precipitation_probability_pct": 75,
    }
    res_prune = asyncio.run(pruning_sensor.evaluate(pruned_data))
    assert res_prune.passed is True

    # Zero hallucination sensor
    hallucination_sensor = ZeroHallucinationWeatherSensor()
    tool_truth = {
        "temperature_celsius": 26.5,
        "precipitation_mm": 0.0,
        "precipitation_probability_pct": 75,
        "will_it_rain": True,
    }
    res_zero_hal = asyncio.run(hallucination_sensor.evaluate(sample_report, context={"tool_truth": tool_truth}))
    assert res_zero_hal.passed is True


def test_cli_weather_forecaster_run():
    """Test airframe run on weather-forecaster workflow pack."""
    res = runner.invoke(
        app,
        [
            "run",
            "workflows/weather-forecaster",
            "-i",
            "Will it rain in Bengaluru tomorrow at 3 PM?",
            "--mock",
        ],
    )
    assert res.exit_code == 0
    assert "Executing Workflow: weather-forecaster" in res.stdout
    assert "Validated Contract: WeatherForecastReport" in res.stdout
    assert "Execution Telemetry & Governance" in res.stdout
