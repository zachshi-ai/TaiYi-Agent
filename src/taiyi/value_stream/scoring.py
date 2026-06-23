"""Value-contribution scoring (L4) and bottleneck detection (L5).

A task can be technically complete yet wasteful in value-stream terms (steps no
one needed, output no one reads). Scoring measures contribution to the anchored
goal; the bottleneck detector aggregates many scores to find where value leaks.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from taiyi.value_stream.goals import TaskGoal, ValueContribution


def score(goal: TaskGoal, *, completed: bool, n_steps: int, redundant: tuple[str, ...] = ()) -> ValueContribution:
    task_completion = 1.0 if completed else 0.0

    # Efficiency by step count: a sweet spot of 2-5 steps; too many is waste.
    if 2 <= n_steps <= 5:
        efficiency = 1.0
    elif n_steps == 1:
        efficiency = 0.6
    elif n_steps <= 7:
        efficiency = 0.7
    else:
        efficiency = 0.3

    tactical = efficiency if goal.tactical_layer else 0.0
    strategic = efficiency * 0.8 if goal.strategic_layer else 0.0  # strategy is harder to hit

    wasted = list(redundant)
    if n_steps > 5:
        wasted.append(f"{n_steps} tool calls — likely value-stream waste")

    notes = []
    if goal.tactical_layer:
        notes.append(f"tactical: {goal.tactical_layer.title}")
    if goal.strategic_layer:
        notes.append(f"strategic: {goal.strategic_layer.title}")

    return ValueContribution(
        task_layer_completion=task_completion,
        tactical_alignment=tactical * task_completion,
        strategic_alignment=strategic * task_completion,
        wasted_steps=wasted,
        notes="; ".join(notes) or "no goal layers beyond task",
    )


@dataclass
class BottleneckDetector:
    """Aggregates ValueContributions to surface where value leaks (L5)."""

    count: int = 0
    _task: float = 0.0
    _tactical: float = 0.0
    _strategic: float = 0.0
    wasted: list[str] = field(default_factory=list)
    _by_type_waste: dict[str, int] = field(default_factory=dict)

    def record(self, vc: ValueContribution, *, task_type: str | None = None) -> None:
        self.count += 1
        self._task += vc.task_layer_completion
        self._tactical += vc.tactical_alignment
        self._strategic += vc.strategic_alignment
        self.wasted.extend(vc.wasted_steps)
        if vc.wasted_steps and task_type:
            self._by_type_waste[task_type] = self._by_type_waste.get(task_type, 0) + len(vc.wasted_steps)

    def report(self) -> dict:
        n = self.count or 1
        worst = max(self._by_type_waste, key=self._by_type_waste.get, default=None)
        return {
            "tasks_scored": self.count,
            "avg_task_completion": round(self._task / n, 3),
            "avg_tactical_alignment": round(self._tactical / n, 3),
            "avg_strategic_alignment": round(self._strategic / n, 3),
            "total_wasted_steps": len(self.wasted),
            "worst_task_type": worst,
        }
