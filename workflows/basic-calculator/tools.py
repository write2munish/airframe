"""Pure Python mathematical calculation tools for basic-calculator."""

from __future__ import annotations

import ast
import operator
from typing import Any

# Whitelisted operators for safe expression evaluation
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """Recursively evaluate an AST expression safely without arbitrary code execution."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        return float(_OPERATORS[op_type](left, right))
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _OPERATORS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        return float(_OPERATORS[op_type](_safe_eval(node.operand)))
    else:
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def calculate(expression: str) -> dict[str, Any]:
    """Safely evaluate an arithmetic expression (e.g. '(450 * 12) + (1800 / 4)').

    Args:
        expression: Mathematical expression string.

    Returns:
        Dict with expression, evaluated numeric result, and status.
    """
    clean_expr = expression.strip().replace("×", "*").replace("÷", "/")
    parsed = ast.parse(clean_expr, mode="eval")
    result = _safe_eval(parsed)
    return {
        "expression": clean_expr,
        "result": round(result, 4),
        "status": "SUCCESS",
    }


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
        compounding_frequency: Compounding periods per year (default: 1 for annually).

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
        "formula": "A = P * (1 + r/n)**(n*t)",
    }
