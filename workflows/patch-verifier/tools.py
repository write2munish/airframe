"""Pure Python analysis tool for verifying AST syntax and safety markers."""

from __future__ import annotations

import ast
from typing import Any


def analyze_python_snippet(python_code: str) -> dict[str, Any]:
    """Parse Python source into an AST and flag unsafe dynamic execution patterns.

    Args:
        python_code: Candidate Python source string.

    Returns:
        Compact summary of AST validity, top-level definitions, and forbidden call checks.
    """
    forbidden = [pat for pat in ("eval(", "exec(", "os.system(") if pat in python_code]
    try:
        tree = ast.parse(python_code)
    except SyntaxError as err:
        return {
            "ast_valid": False,
            "error": f"SyntaxError line {err.lineno}: {err.msg}",
            "forbidden_calls": forbidden,
            "functions": [],
        }

    fn_names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return {
        "ast_valid": True,
        "error": None,
        "forbidden_calls": forbidden,
        "functions": fn_names,
    }
