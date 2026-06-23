"""H3 Observability & Audit — traces, metrics, structured logs.

Stdlib-only; an OpenTelemetry/Prometheus exporter plugs in as an opt-in. Builds on
the M1 tamper-evident audit log (the system of record for governance decisions).
"""

from taiyi.observability.core import Observability
from taiyi.observability.logging import StructuredLogger
from taiyi.observability.metrics import Counter, Gauge, Histogram, MetricsRegistry
from taiyi.observability.tracing import Span, TaskTrace, Tracer

__all__ = [
    "Observability",
    "StructuredLogger",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "Span",
    "TaskTrace",
    "Tracer",
]
