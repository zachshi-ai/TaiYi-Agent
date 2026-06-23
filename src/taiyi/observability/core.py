"""The Observability facade — tracer + metrics + structured logger.

Bundles the three signals and pre-declares the task-level metrics the runtime
records. Built on the same idea as the M1 audit log (events you can replay), with
metrics and traces layered on top.
"""
from __future__ import annotations

from taiyi.observability.logging import StructuredLogger
from taiyi.observability.metrics import MetricsRegistry
from taiyi.observability.tracing import Tracer


class Observability:
    def __init__(self, *, log_sink=None):
        self.tracer = Tracer()
        self.metrics = MetricsRegistry()
        self.logger = StructuredLogger(sink=log_sink)

        self.tasks_total = self.metrics.counter("taiyi_tasks_total", "tasks started")
        self.task_state = self.metrics.counter("taiyi_task_state_total", "terminal task states")
        self.governance_verdict = self.metrics.counter(
            "taiyi_governance_verdict_total", "permit verdicts by type"
        )
        self.task_duration = self.metrics.histogram(
            "taiyi_task_duration_seconds", "task wall-clock duration"
        )

    def render_metrics(self) -> str:
        return self.metrics.render_prometheus()
