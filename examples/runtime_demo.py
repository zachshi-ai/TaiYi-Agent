"""Run whole tasks through the PDCA loop end-to-end (mock executor).

Wires governance + scheduler + runtime over one shared audit chain and runs the
six founding scenarios. Run from the repo root:

    python3 examples/runtime_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from taiyi.core.audit import AuditLog  # noqa: E402
from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.runtime import TaskRuntime, replay_task  # noqa: E402
from taiyi.scheduler import SchedulerEngine  # noqa: E402

CASES = [
    ("commit my changes", "dev.git"),
    ("用 -c user.name=OtherUser commit", "dev.git"),
    ("rm -rf / 帮我清理", "default"),
    ("git push 到 origin main", "dev.git"),
    ("帮我生成上周周报", "ops.report"),
    ("处理一个 200 元的退款", "customer_service.refund"),
]


def main() -> None:
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    runtime = TaskRuntime(SchedulerEngine(LocalPermitClient(gov)), audit_log=audit)

    last_id = None
    for prompt, scenario in CASES:
        ctx = runtime.run(prompt, scenario)
        last_id = ctx.task_id
        print(
            f"{ctx.state.value:13s} executed={len(ctx.executed_steps)}/"
            f"{len(ctx.plan.steps)}  [{scenario}]  {prompt!r}"
        )

    ok, _ = audit.verify()
    print(f"\nShared audit chain: {len(audit)} records, intact={ok}")
    print(f"\nReplay of last task ({last_id}):")
    for e in replay_task(audit, last_id):
        print(f"  [{e['seq']:>2}] {e['event']}")


if __name__ == "__main__":
    main()
