"""Deterministic sensor validating compound interest mathematical accuracy."""

from airframe.sensors.base import Sensor, SensorResult


class CompoundInterestMathSensor(Sensor):
    """Tier 1 Deterministic Sensor: recomputes formula and verifies result within 0.05 tolerance."""

    @property
    def name(self) -> str:
        return "compound_interest_math_validator"

    @property
    def tier(self) -> int:
        return 1

    async def evaluate(self, candidate, context=None) -> SensorResult:
        try:
            # Candidate may be a CompoundInterestContract object or dict
            if hasattr(candidate, "model_dump"):
                data = candidate.model_dump()
            elif isinstance(candidate, dict):
                data = candidate
            else:
                return SensorResult(
                    sensor_name=self.name,
                    tier=self.tier,
                    passed=False,
                    message="Candidate must be a dict or Pydantic contract object.",
                )

            p = float(data["principal"])
            r = float(data["rate_percent"]) / 100.0
            t = float(data["time_years"])
            n = int(data.get("compounding_frequency", 1))

            expected_amount = p * ((1 + (r / n)) ** (n * t))
            expected_interest = expected_amount - p

            reported_amount = float(data["total_amount"])
            reported_interest = float(data["total_interest"])

            amount_diff = abs(reported_amount - expected_amount)
            interest_diff = abs(reported_interest - expected_interest)

            if amount_diff > 0.05 or interest_diff > 0.05:
                return SensorResult(
                    sensor_name=self.name,
                    tier=self.tier,
                    passed=False,
                    message=(
                        f"Math mismatch: Expected Amount={expected_amount:.2f}, Interest={expected_interest:.2f} | "
                        f"Reported Amount={reported_amount:.2f}, Interest={reported_interest:.2f}"
                    ),
                    diagnostics={"amount_diff": amount_diff, "interest_diff": interest_diff},
                )

            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=True,
                message=f"Mathematical calculation verified independently: Amount={reported_amount:.2f}, Interest={reported_interest:.2f}",
                diagnostics={"expected_amount": round(expected_amount, 2), "expected_interest": round(expected_interest, 2)},
            )
        except Exception as err:
            return SensorResult(
                sensor_name=self.name,
                tier=self.tier,
                passed=False,
                message=f"Sensor execution error: {err}",
            )
