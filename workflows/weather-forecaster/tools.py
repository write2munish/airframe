"""Weather forecasting tools with automated payload pruning to prevent context bloat."""

from __future__ import annotations

from typing import Any

import httpx


async def geocode_location(location_name: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Resolve a human-readable city or locality into precise geographic coordinates.

    Calls Open-Meteo Geocoding API and prunes administrative metadata down to essential coordinates.
    """
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": location_name, "count": 1}

    async def _fetch(c: httpx.AsyncClient) -> dict[str, Any]:
        resp = await c.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    if client is not None:
        data = await _fetch(client)
    else:
        async with httpx.AsyncClient(timeout=10.0) as default_client:
            data = await _fetch(default_client)

    results = data.get("results")
    if not results:
        return {"error": f"Location '{location_name}' not found."}

    match = results[0]

    # PRUNING: Extract only essential geospatial keys to save context tokens
    return {
        "name": match.get("name"),
        "country": match.get("country"),
        "latitude": round(float(match.get("latitude")), 4),
        "longitude": round(float(match.get("longitude")), 4),
        "timezone": match.get("timezone", "auto"),
    }


async def get_hourly_forecast(
    latitude: float,
    longitude: float,
    target_date_iso: str,
    target_hour: int,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch forecast and filter 168 hours of raw data down to the exact requested hour.

    Prunes ~7 days of continuous hourly float arrays down to a single compact reading (<50 tokens).
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,precipitation_probability,precipitation",
        "timezone": "auto",
    }

    async def _fetch(c: httpx.AsyncClient) -> dict[str, Any]:
        resp = await c.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    if client is not None:
        data = await _fetch(client)
    else:
        async with httpx.AsyncClient(timeout=10.0) as default_client:
            data = await _fetch(default_client)

    hourly = data.get("hourly", {})
    timestamps = hourly.get("time", [])

    # Target timestamp pattern (e.g., '2026-11-06T15:00')
    target_match_str = f"{target_date_iso}T{int(target_hour):02d}:00"

    try:
        idx = timestamps.index(target_match_str)
        precip_prob = hourly.get("precipitation_probability", [0])[idx]
        precip_mm = hourly.get("precipitation", [0.0])[idx]
        temp_c = hourly.get("temperature_2m", [0.0])[idx]

        # PRUNING: Return only the target hour payload
        return {
            "timestamp": timestamps[idx],
            "latitude": latitude,
            "longitude": longitude,
            "temperature_celsius": temp_c,
            "precipitation_mm": precip_mm,
            "precipitation_probability_pct": precip_prob,
            "will_it_rain": precip_prob > 30 or precip_mm > 0.1,
            "pruned_from_hours": len(timestamps),
        }
    except (ValueError, KeyError, IndexError):
        return {
            "error": f"Timestamp '{target_match_str}' not found in available forecast window.",
            "available_range": f"{timestamps[0] if timestamps else 'N/A'} to {timestamps[-1] if timestamps else 'N/A'}",
        }
