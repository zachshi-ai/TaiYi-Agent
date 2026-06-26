"""Observability (H3): per-task trace, metrics, structured logs.

Run from the repo root:  python3 examples/observability_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.core.audit import AuditLog  # noqa: E402
from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.observability import Observability  # noqa: E402
from taiyi.runtime import TaskRuntime  # noqa: E402
from taiyi.scheduler import SchedulerEngine  # noqa: E402


def main() -> None:
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    obs = Observability(log_sink=lambda line: print("  log:", line))
    runtime = TaskRuntime(SchedulerEngine(LocalPermitClient(gov)), audit_log=audit, observability=obs)

    print("== Run a few tasks ==")
    for prompt, scenario in [
        ("commit my changes", "dev.git"),
        ("用 -c user.name=Evil commit", "dev.git"),
        ("处理一个 200 元的退款", "customer_service.refund"),
    ]:
        ctx = runtime.run(prompt, scenario)
        trace = obs.tracer.get(ctx.task_id)
        print(f"  {ctx.state.value:13s} spans={[s.name for s in trace.spans]}")

    print("\n== Prometheus metrics ==")
    print(obs.render_metrics())


if __name__ == "__main__":
    main()
