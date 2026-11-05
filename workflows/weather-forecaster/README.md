# 🌦️ Deterministic Weather Forecaster Workflow

> **A reference implementation demonstrating decoupled geospatial resolution, API payload pruning, and zero-hallucination sensors using Airframe.**

---

## 1. System Architecture & Information Flow

The agent resolves human natural language into precise geospatial coordinates before calling the forecast endpoint:

```
[User Prompt] "Will it rain in Bengaluru tomorrow at 3 PM?"
       │
       ▼
[Agent Harness]
  ──> Step 1: Tool Call `geocode_location(location_name="Bengaluru")`
        ├─ Calls Open-Meteo Geocoding API
        └─ Returns: { latitude: 12.9716, longitude: 77.5946, timezone: "Asia/Kolkata" }
  ──> Step 2: Tool Call `get_hourly_forecast(latitude=12.9716, longitude=77.5946, target_date_iso="...", target_hour=15)`
        ├─ Calls Open-Meteo Forecast API
        └─ Harness intercepts & prunes 168-hour array down to target window (<50 tokens)
       │
       ▼
[Final Assertion via Sensor Pipeline]
  Evaluates: Tool sequence, coordinate accuracy, payload pruning, and zero hallucination.
```

---

## 2. Streamlined Workflow Structure

Following modern Airframe guidelines for single-agent workflows, all components are colocated without empty subdirectories:

```
workflows/weather-forecaster/
├── README.md         # Architecture and documentation
├── workflow.yaml     # Single unified manifest (inline prompt, tools, governance)
├── tools.py          # Pure Python tools (geocode_location, get_hourly_forecast)
├── contracts.py      # Pydantic v2 WeatherForecastReport schema
├── sensors.py        # Deterministic verification sensors (Tier 1 & 2)
└── testbed.yaml      # Golden sanity test cases
```

---

## 3. Running the Workflow

```bash
# Execute end-to-end via CLI
airframe run workflows/weather-forecaster -i "Will it rain in Bengaluru tomorrow at 3 PM?" --mock

# Run regression testbed
airframe test workflows/weather-forecaster
```
