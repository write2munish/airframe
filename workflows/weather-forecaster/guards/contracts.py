"""Executable contracts for the Deterministic Weather Forecasting workflow."""

from pydantic import BaseModel, Field


class WeatherForecastReport(BaseModel):
    """Canonical structured response for resolved weather forecast queries."""

    location_name: str = Field(description="Resolved city or locality name.")
    country: str = Field(description="Country of the resolved locality.")
    latitude: float = Field(description="Geocoded latitude coordinate.")
    longitude: float = Field(description="Geocoded longitude coordinate.")
    target_date: str = Field(description="Target forecast date (YYYY-MM-DD).")
    target_hour: int = Field(ge=0, le=23, description="Target forecast hour in 24h format (0-23).")
    temperature_celsius: float = Field(description="Reported ambient temperature in degrees Celsius.")
    precipitation_mm: float = Field(ge=0.0, description="Expected precipitation in millimeters.")
    precipitation_probability_pct: int = Field(
        ge=0, le=100, description="Probability of precipitation percentage (0-100%)."
    )
    will_it_rain: bool = Field(description="Deterministic rain forecast boolean based on probability or precipitation.")
    summary: str = Field(description="Human-readable forecast summary based on tool data.")
