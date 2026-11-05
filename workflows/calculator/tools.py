"""Authorized mathematical calculation tools for single-tool calculator."""

from typing import Any


def calculate_compound_interest(
    principal: float,
    rate_percent: float,
    time_years: float,
    compounding_frequency: int = 1,
) -> dict[str, Any]:
    """Calculate compound interest according to A = P(1 + r/n)^(nt).

    Args:
        principal: Initial invested principal amount.
        rate_percent: Annual interest rate in percent (e.g. 5.0 for 5%).
        time_years: Duration in years.
        compounding_frequency: Compounding periods per year (1 for annual, 4 for quarterly, 12 for monthly).

    Returns:
        Dict with principal, rate, time, total_amount, and total_interest.
    """
    p = float(principal)
    r = float(rate_percent) / 100.0
    t = float(time_years)
    n = int(compounding_frequency)

    total_amount = p * ((1 + (r / n)) ** (n * t))
    total_interest = total_amount - p

    return {
        "principal": round(p, 2),
        "rate_percent": round(float(rate_percent), 2),
        "time_years": round(t, 2),
        "compounding_frequency": n,
        "total_amount": round(total_amount, 2),
        "total_interest": round(total_interest, 2),
        "formula_used": "A = P * (1 + r/n)**(n*t)",
    }
