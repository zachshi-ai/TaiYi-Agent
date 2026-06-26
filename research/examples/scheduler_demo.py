"""Plan a task, then walk it through the governance boundary.

Shows the M2 boundary semantics: DENY halts, NEEDS_REVIEW suspends while keeping
already-cleared steps, ALLOW clears everything. Run from the repo root:

    python3 examples/scheduler_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.scheduler import SchedulerEngine  # noqa: E402

CASES = [
    ("commit my changes", "dev.git"),
    ("用 -c user.name=OtherUser commit", "dev.git"),
    ("帮我生成上周周报", "ops.report"),
    ("处理一个 200 元的退款", "customer_service.refund"),
]


def main() -> None:
    sched = SchedulerEngine(LocalPermitClient(GovernanceEngine()))
    for prompt, scenario in CASES:
        plan, c = sched.plan_and_clear(prompt, scenario, task_id="demo")
        print(f"\n> {prompt!r}  [{scenario}]  skill={plan.skill_name}")
        print(f"  terminal: {c.terminal_verdict.value}  cleared={len(c.cleared_steps)}/{len(plan.steps)}")
        for d in c.decisions:
            mark = "ok " if d.response.verdict.value == "ALLOW" else "STOP"
            print(f"    [{mark}] {d.response.verdict.value:13s} {d.step.tool}")
        if c.halted_response:
            print(f"  halted: {c.halted_response.reason}")


if __name__ == "__main__":
    main()
