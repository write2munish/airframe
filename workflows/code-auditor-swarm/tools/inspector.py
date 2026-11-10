"""Zero-decorator static inspection tools for code-auditor-swarm workers."""

from __future__ import annotations

from typing import Any


def scan_diff_markers(code_text: str) -> dict[str, Any]:
    """Scan a code or diff snippet for high-risk Python security anti-patterns.

    Args:
        code_text: Source code or unified diff string to inspect.

    Returns:
        Compact summary of matched risk markers and line count.
    """
    markers = {
        "dynamic_eval": "eval(" in code_text or "exec(" in code_text,
        "subprocess_shell": "shell=True" in code_text,
        "raw_sql_interpolation": ("SELECT " in code_text.upper() and ("f'" in code_text or 'f"' in code_text)),
    }
    flagged = [name for name, hit in markers.items() if hit]
    return {
        "line_count": len(code_text.splitlines()),
        "flagged_markers": flagged,
        "clean": len(flagged) == 0,
    }
