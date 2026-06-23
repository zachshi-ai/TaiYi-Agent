"""Lightweight per-task tracing (stdlib).

One trace per task, nested spans per phase (plan / do / validate, with finer spans
as needed). Spans carry timing and attributes. An OpenTelemetry exporter can walk
these `TaskTrace` objects and emit OTLP — that is the opt-in seam; nothing here
depends on OTel.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start: float
    end: float | None = None
    attributes: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return (self.end - self.start) if self.end is not None else 0.0


class TaskTrace:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.spans: list[Span] = []
        self._stack: list[Span] = []

    @contextmanager
    def span(self, name: str, **attributes):
        s = Span(
            name=name,
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[:12],
            parent_id=self._stack[-1].span_id if self._stack else None,
            start=time.time(),
            attributes=dict(attributes),
        )
        self._stack.append(s)
        try:
            yield s
        finally:
            s.end = time.time()
            self._stack.pop()
            self.spans.append(s)

    @property
    def duration(self) -> float:
        if not self.spans:
            return 0.0
        start = min(s.start for s in self.spans)
        end = max((s.end or s.start) for s in self.spans)
        return end - start


class Tracer:
    def __init__(self):
        self.traces: dict[str, TaskTrace] = {}

    def start(self, trace_id: str) -> TaskTrace:
        trace = TaskTrace(trace_id)
        self.traces[trace_id] = trace
        return trace

    def get(self, trace_id: str) -> TaskTrace | None:
        return self.traces.get(trace_id)
