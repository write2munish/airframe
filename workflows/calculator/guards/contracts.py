"""Executable contracts for the Single-Tool Calculator workflow."""

from pydantic import BaseModel, Field


class CompoundInterestContract(BaseModel):
    """Canonical structured response for compound interest calculations."""

    currency: str = Field(default="₹", description="Currency symbol or code.")
    principal: float = Field(description="Initial principal amount.")
    rate_percent: float = Field(description="Annual interest rate.")
    time_years: float = Field(description="Time duration in years.")
    compounding_frequency: int = Field(default=1, description="Compounding periods per year.")
    total_amount: float = Field(description="Total accumulated amount (Principal + Interest).")
    total_interest: float = Field(description="Compound interest earned.")
    explanation: str = Field(description="Human-readable breakdown of the calculation.")
