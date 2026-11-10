"""Hierarchical Observability & Distributed Tracing (FR-14).

Provides W3C-compatible trace_id/span_id propagation across asyncio tasks using contextvars,
durable JSONL span streaming with fsync, and an optional OpenTelemetry API bridge.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
import os
from pathlib import Path
import threading
import time
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field

SpanKind = Literal["root", "supervisor", "worker", "model", "tool", "sensor"]
SpanStatus = Literal["OK", "ERROR"]


class SpanRecord(BaseModel):
    """Serializable W3C-compatible span entry persisted to JSONL trace logs."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind = "root"
    start_ns: int
    end_ns: int
    status: SpanStatus = "OK"
    error: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.end_ns - self.start_ns) / 1_000_000.0)


_CURRENT_TRACER: ContextVar[AirframeTracer | None] = ContextVar("airframe_current_tracer", default=None)
_CURRENT_SPAN_ID: ContextVar[str | None] = ContextVar("airframe_current_span_id", default=None)


def _try_get_otel_tracer() -> Any:
    """Optional bridge to OpenTelemetry if opentelemetry-api is installed."""
    try:
        from opentelemetry import trace as otel_trace  # type: ignore[import-not-found]
        return otel_trace.get_tracer("airframe")
    except Exception:
        return None


def get_active_tracer() -> AirframeTracer | None:
    """Return the active AirframeTracer bound in the current async/sync context."""
    return _CURRENT_TRACER.get()


class _SpanContext:
    """Dual sync/async context manager managing span lifecycle and contextvar propagation."""

    def __init__(
        self,
        tracer: AirframeTracer,
        name: str,
        kind: SpanKind,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.tracer = tracer
        self.name = name
        self.kind = kind
        self.attributes = dict(attributes or {})
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id: str | None = None
        self.start_ns: int = 0
        self._tracer_token: Token[AirframeTracer | None] | None = None
        self._span_token: Token[str | None] | None = None
        self._otel_cm: Any = None
        self._otel_span: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value
        if self._otel_span is not None:
            try:
                self._otel_span.set_attribute(key, value)
            except Exception:
                pass

    def __enter__(self) -> _SpanContext:
        self.parent_span_id = _CURRENT_SPAN_ID.get()
        self.start_ns = time.time_ns()
        self._tracer_token = _CURRENT_TRACER.set(self.tracer)
        self._span_token = _CURRENT_SPAN_ID.set(self.span_id)

        if self.tracer._otel_tracer is not None:
            try:
                self._otel_cm = self.tracer._otel_tracer.start_as_current_span(self.name)
                self._otel_span = self._otel_cm.__enter__()
                self._otel_span.set_attribute("airframe.kind", self.kind)
                for k, v in self.attributes.items():
                    self._otel_span.set_attribute(k, str(v))
            except Exception:
                self._otel_cm = None
                self._otel_span = None

        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        end_ns = time.time_ns()
        status: SpanStatus = "ERROR" if exc_val is not None else "OK"
        err_str = f"{type(exc_val).__name__}: {exc_val}" if exc_val is not None else None

        record = SpanRecord(
            trace_id=self.tracer.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            kind=self.kind,
            start_ns=self.start_ns,
            end_ns=end_ns,
            status=status,
            error=err_str,
            attributes=self.attributes,
        )
        self.tracer.record_span(record)

        if self._otel_cm is not None:
            try:
                self._otel_cm.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass

        if self._span_token is not None:
            _CURRENT_SPAN_ID.reset(self._span_token)
        if self._tracer_token is not None:
            _CURRENT_TRACER.reset(self._tracer_token)

    async def __aenter__(self) -> _SpanContext:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)


class AirframeTracer:
    """Streams hierarchical execution spans to durable JSONL trace files."""

    def __init__(
        self,
        trace_dir: str | Path = ".airframe/traces",
        trace_id: str | None = None,
    ) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex
        self.trace_dir = Path(trace_dir).resolve()
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file = self.trace_dir / f"{self.trace_id}.jsonl"
        self._lock = threading.Lock()
        self._spans: list[SpanRecord] = []
        self._otel_tracer = _try_get_otel_tracer()

    def start_span(
        self,
        name: str,
        kind: SpanKind = "root",
        attributes: dict[str, Any] | None = None,
    ) -> _SpanContext:
        """Create a span context manager inheriting parent_span_id from contextvars."""
        return _SpanContext(tracer=self, name=name, kind=kind, attributes=attributes)

    def record_span(self, record: SpanRecord) -> None:
        """Persist a finished span atomically to the JSONL trace log."""
        line = record.model_dump_json() + "\n"
        with self._lock:
            self._spans.append(record)
            with self.trace_file.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def get_spans(self) -> list[SpanRecord]:
        """Return in-memory list of recorded spans."""
        with self._lock:
            return list(self._spans)


def load_trace(trace_path: str | Path) -> list[SpanRecord]:
    """Load recorded SpanRecord objects from a JSONL trace file."""
    path = Path(trace_path).resolve()
    if not path.is_file():
        return []
    spans: list[SpanRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            spans.append(SpanRecord.model_validate_json(line))
    return spans


def build_trace_tree(spans: list[SpanRecord]) -> dict[str, Any]:
    """Reconstruct a hierarchical trace tree from a flat list of SpanRecords."""
    nodes: dict[str, dict[str, Any]] = {}
    for s in spans:
        d = s.model_dump()
        d["children"] = []
        nodes[s.span_id] = d

    roots: list[dict[str, Any]] = []
    for s in spans:
        node = nodes[s.span_id]
        if s.parent_span_id and s.parent_span_id in nodes:
            nodes[s.parent_span_id]["children"].append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node["children"].sort(key=lambda c: c["start_ns"])
    roots.sort(key=lambda r: r["start_ns"])

    return {
        "trace_id": spans[0].trace_id if spans else None,
        "span_count": len(spans),
        "roots": roots,
    }
