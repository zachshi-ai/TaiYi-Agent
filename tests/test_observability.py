"""Observability (H3): tracing, metrics, structured logs, integration (M11)."""
from __future__ import annotations

from taiyi.core.audit import AuditLog
from taiyi.gateway import GatewayApp, build_gateway
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.observability import MetricsRegistry, Observability, Tracer
from taiyi.runtime import TaskRuntime, TaskState
from taiyi.scheduler import SchedulerEngine


# --- Tracing -----------------------------------------------------------------

def test_trace_records_nested_spans():
    trace = Tracer().start("t1")
    with trace.span("task"):
        with trace.span("plan", skill="x"):
            pass
    names = [s.name for s in trace.spans]
    assert "plan" in names and "task" in names
    plan = next(s for s in trace.spans if s.name == "plan")
    task = next(s for s in trace.spans if s.name == "task")
    assert plan.parent_id == task.span_id  # plan nested under task
    assert plan.attributes["skill"] == "x"


# --- Metrics -----------------------------------------------------------------

def test_counter_with_labels_and_prometheus_render():
    reg = MetricsRegistry()
    c = reg.counter("taiyi_governance_verdict_total")
    c.inc(verdict="ALLOW")
    c.inc(verdict="ALLOW")
    c.inc(verdict="DENY")
    assert c.value(verdict="ALLOW") == 2
    text = reg.render_prometheus()
    assert 'taiyi_governance_verdict_total{verdict="ALLOW"} 2' in text
    assert "# TYPE taiyi_governance_verdict_total counter" in text


def test_histogram_render():
    reg = MetricsRegistry()
    h = reg.histogram("taiyi_task_duration_seconds")
    h.observe(0.02)
    h.observe(2.0)
    text = reg.render_prometheus()
    assert "taiyi_task_duration_seconds_count 2" in text
    assert 'taiyi_task_duration_seconds_bucket{le="+Inf"} 2' in text


# --- Structured logging ------------------------------------------------------

def test_structured_logger_captures_and_forwards():
    sink_lines = []
    obs = Observability(log_sink=sink_lines.append)
    obs.logger.info("hello", task_id="t1")
    assert obs.logger.records[-1]["event"] == "hello"
    assert sink_lines and "t1" in sink_lines[-1]


# --- Runtime integration -----------------------------------------------------

def test_runtime_emits_traces_and_metrics():
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    obs = Observability()
    runtime = TaskRuntime(sched, audit_log=audit, observability=obs)

    ctx = runtime.run("commit my changes", "dev.git")
    assert ctx.state is TaskState.COMPLETED

    assert obs.tasks_total.value() == 1
    assert obs.task_state.value(state="COMPLETED") == 1
    assert obs.governance_verdict.value(verdict="ALLOW") == 4  # four cleared steps
    assert obs.task_duration.count == 1

    trace = obs.tracer.get(ctx.task_id)
    assert trace is not None
    assert {"task", "plan", "do", "validate"} <= {s.name for s in trace.spans}


# --- Gateway /metrics --------------------------------------------------------

def test_gateway_metrics_endpoint():
    app = GatewayApp(build_gateway())
    app.handle("POST", "/v1/tasks", {}, '{"prompt":"commit my changes"}')
    status, body = app.handle("GET", "/metrics", {}, "")
    assert status == 200
    assert isinstance(body, str)
    assert "taiyi_tasks_total" in body
