"""Value Stream (H4): goal anchoring, contribution scoring, bottlenecks.

Run from the repo root:  python3 examples/value_stream_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from taiyi.value_stream import GoalAnchoringMode, ValueStreamEngine  # noqa: E402


def main() -> None:
    vse = ValueStreamEngine()

    print("== Mode B (preset) per scenario ==")
    for scenario in ("dev.git", "ops.report", "customer_service.refund"):
        g = vse.anchor("x", scenario)
        layers = [name for name, ref in (("task", g.task_layer), ("tactical", g.tactical_layer),
                                         ("strategic", g.strategic_layer)) if ref]
        print(f"  {scenario:26s} stack={layers} stream={g.value_stream_id}")

    print("\n== Mode A (AI infer + confirm) ==")
    cand = vse.infer_candidates("submit my feature branch for review", "dev.git")
    print(f"  candidate task: {cand.task_layer.title!r}")
    locked = vse.anchor("submit my feature branch for review", "dev.git",
                        mode=GoalAnchoringMode.AI_INFER_CONFIRM, selection=["task", "tactical"])
    print(f"  confirmed layers: task + {'tactical' if locked.tactical_layer else ''}")

    print("\n== Scoring + bottleneck detection ==")
    goal = vse.anchor("refund", "customer_service.refund")
    vse.score(goal, completed=True, n_steps=3, task_type="refund_request")
    vse.score(goal, completed=True, n_steps=14, task_type="refund_request")
    for k, v in vse.bottlenecks().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
