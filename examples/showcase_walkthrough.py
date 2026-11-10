#!/usr/bin/env python3
"""Airframe 5-Minute Developer Quickstart (0 API Keys Required).

Usage:
  PYTHONPATH=src python3 examples/showcase_walkthrough.py
  PYTHONPATH=src python3 examples/showcase_walkthrough.py --live gemini-2.0-flash
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import tempfile

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from airframe.adapters import resolve_adapter  # noqa: E402
from airframe.cli import MockExecutionAdapter  # noqa: E402
from airframe.governance.circuit_breakers import StallDetectedError, StallDetector  # noqa: E402
from airframe.guards import ExecutableGuard, GuardViolationError  # noqa: E402
from airframe.runner import WorkflowRunner  # noqa: E402
from airframe.state.disk import DiskStateManager, TaskItem  # noqa: E402
from airframe.telemetry import build_trace_tree, load_trace  # noqa: E402
from airframe.workflows.loader import load_workflow  # noqa: E402


class ServiceStatus(BaseModel):
    service: str
    passed: bool
    latency_ms: float = Field(ge=0.0)


def _print_trace_tree(node: dict, indent: str = "") -> None:
    dur_ms = (node["end_ns"] - node["start_ns"]) / 1_000_000.0
    print(f"{indent}- {node['name']} ({node['kind']}) [{node['status']}] {dur_ms:.1f}ms")
    for child in node.get("children", []):
        _print_trace_tree(child, indent + "  ")


async def run_quickstart(live_engine: str | None = None) -> None:
    adapter = resolve_adapter(live_engine) if live_engine else MockExecutionAdapter(model_name="mock-engine")
    print(f"=== Airframe Quickstart ({'LIVE: ' + live_engine if live_engine else 'OFFLINE MOCK'}) ===\n")

    # 1. Swappable Engines & Executable Contracts
    print("[1/4] Engine Swap + Executable Contract")
    for eng in ("gemini-2.0-flash", "claude-3-5-sonnet-latest", "gpt-4o-mini", "ollama/llama3.2"):
        resolved = resolve_adapter(eng, api_key="demo-key")
        print(f"  {eng:26s} -> {type(resolved).__name__} ({resolved.provider_name})")

    status, _ = await adapter.invoke_contract(
        prompt="Return status for checkout-api.",
        schema=ServiceStatus,
    )
    print(f"  Contract output: {status.model_dump()}\n")

    # 2. Same Runner Across All 3 Workflow Topologies
    print("[2/4] Running Workflows Across Topologies (Same 3 Lines of Python)")
    with tempfile.TemporaryDirectory(prefix="airframe_demo_") as tmp:
        tmp_dir = Path(tmp)
        runner = WorkflowRunner(trace_dir=tmp_dir / "traces")

        demos = [
            ("basic-calculator", "Calculate compound interest on ₹10,000 at 5% for 3 years."),
            ("patch-verifier", "Write a pure Python helper solve(x: int) -> int."),
            ("code-auditor-swarm", "Audit auth_utils.py for eval or SQL injection risks."),
        ]
        last_trace_file = None
        for wf_name, query in demos:
            pack = load_workflow(REPO_ROOT / "workflows" / wf_name)
            res = await runner.run(pack, query, adapter, sensors=pack.load_sensors())
            last_trace_file = res.trace_file
            print(
                f"  [{res.topology:19s}] {wf_name:18s} | entrypoint={res.entrypoint_agent} "
                f"| sensors_ok={res.all_sensors_passed} | output={res.output}"
            )

        # 3. Deterministic Guard, Stall Breaker & Disk State
        print("\n[3/4] Guards, Governance Breakers & Externalized State")
        try:
            ExecutableGuard(schema=ServiceStatus).validate('{"service": "bad", "passed": true}')
        except GuardViolationError as err:
            print(f"  ExecutableGuard blocked invalid JSON cleanly: {type(err).__name__}")

        stall = StallDetector(stall_threshold=3)
        try:
            for _ in range(3):
                stall.record_attempt("tool_x({})", success=False, error_message="timeout")
        except StallDetectedError as err:
            print(f"  StallDetector halted loop after 3 failures: {err}")

        state = DiskStateManager(tmp_dir / "state")
        state.init_state("# Demo Plan", [TaskItem(id="t1", description="Audit module", status="DONE")])
        print(f"  DiskStateManager persisted queue atomically: {state.queue_file.name}")

        # 4. Hierarchical W3C JSONL Trace Replay
        print("\n[4/4] W3C Trace Tree Replay (from .jsonl on disk)")
        if last_trace_file:
            tree = build_trace_tree(load_trace(last_trace_file))
            for root in tree["roots"]:
                _print_trace_tree(root)

    print("\n✓ Airframe Quickstart completed cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Airframe 5-minute quickstart walkthrough.")
    parser.add_argument("--live", default=None, help="Run with a live engine (e.g., gemini-2.0-flash, ollama/llama3).")
    args = parser.parse_args()
    asyncio.run(run_quickstart(args.live))


if __name__ == "__main__":
    main()
