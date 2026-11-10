"""Unit tests for hierarchical W3C-compatible JSONL tracing (FR-14)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from airframe.telemetry import (
    AirframeTracer,
    SpanRecord,
    build_trace_tree,
    get_active_tracer,
    load_trace,
)


def test_nested_async_trace_tree_propagation(tmp_path: Path) -> None:
    """Verify nested async tasks share trace_id and retain exact parent_span_id without cross-contamination."""
    tracer = AirframeTracer(trace_dir=tmp_path)

    async def run_hierarchy() -> None:
        assert get_active_tracer() is None
        async with tracer.start_span("workflow.run", kind="root", attributes={"workflow": "demo"}) as root_span:
            assert get_active_tracer() is tracer
            async with tracer.start_span("supervisor.plan", kind="supervisor") as sup_span:
                assert sup_span.parent_span_id == root_span.span_id

                async def worker(worker_name: str, delay: float) -> None:
                    active = get_active_tracer()
                    assert active is tracer
                    async with active.start_span(f"worker.{worker_name}", kind="worker") as w_span:
                        assert w_span.parent_span_id == sup_span.span_id
                        # Deliberately stagger sleeps to interleave concurrent tasks
                        await asyncio.sleep(delay)
                        async with active.start_span(f"tool.{worker_name}", kind="tool") as t_span:
                            assert t_span.parent_span_id == w_span.span_id
                            await asyncio.sleep(delay / 2)
                        async with active.start_span(f"sensor.{worker_name}", kind="sensor") as s_span:
                            assert s_span.parent_span_id == w_span.span_id
                            s_span.set_attribute("passed", True)

                await asyncio.gather(
                    asyncio.create_task(worker("alpha", 0.02)),
                    asyncio.create_task(worker("beta", 0.005)),
                )
        assert get_active_tracer() is None

    asyncio.run(run_hierarchy())

    # Reload from durable JSONL file
    assert tracer.trace_file.exists()
    loaded: list[SpanRecord] = load_trace(tracer.trace_file)
    assert len(loaded) == 8  # root + sup + 2*(worker + tool + sensor)

    # All spans share identical trace_id
    assert all(s.trace_id == tracer.trace_id for s in loaded)
    assert all(len(s.span_id) == 16 for s in loaded)
    assert all(s.status == "OK" for s in loaded)

    # Reconstruct exact tree
    tree = build_trace_tree(loaded)
    assert tree["trace_id"] == tracer.trace_id
    assert tree["span_count"] == 8
    assert len(tree["roots"]) == 1

    root = tree["roots"][0]
    assert root["name"] == "workflow.run"
    assert root["kind"] == "root"
    assert root["parent_span_id"] is None
    assert len(root["children"]) == 1

    sup = root["children"][0]
    assert sup["name"] == "supervisor.plan"
    assert sup["kind"] == "supervisor"
    assert len(sup["children"]) == 2

    worker_map = {w["name"]: w for w in sup["children"]}
    assert set(worker_map.keys()) == {"worker.alpha", "worker.beta"}

    for suffix in ("alpha", "beta"):
        w = worker_map[f"worker.{suffix}"]
        assert w["kind"] == "worker"
        assert w["parent_span_id"] == sup["span_id"]
        child_names = [c["name"] for c in w["children"]]
        assert child_names == [f"tool.{suffix}", f"sensor.{suffix}"]
        assert w["children"][0]["kind"] == "tool"
        assert w["children"][0]["parent_span_id"] == w["span_id"]
        assert w["children"][1]["kind"] == "sensor"
        assert w["children"][1]["parent_span_id"] == w["span_id"]
        assert w["children"][1]["attributes"]["passed"] is True


def test_sync_span_and_error_recording(tmp_path: Path) -> None:
    """Verify sync span usage captures exceptions as ERROR status and still flushes cleanly."""
    tracer = AirframeTracer(trace_dir=tmp_path, trace_id="deadbeef" * 4)

    with pytest.raises(RuntimeError, match="boom"):
        with tracer.start_span("root.sync", kind="root"):
            with tracer.start_span("tool.fail", kind="tool"):
                raise RuntimeError("boom")

    spans = load_trace(tracer.trace_file)
    assert len(spans) == 2
    by_name = {s.name: s for s in spans}
    assert by_name["tool.fail"].status == "ERROR"
    assert "RuntimeError: boom" in (by_name["tool.fail"].error or "")
    assert by_name["root.sync"].status == "ERROR"
    assert by_name["tool.fail"].parent_span_id == by_name["root.sync"].span_id
