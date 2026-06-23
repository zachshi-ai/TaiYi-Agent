"""Value-stream goal types (Technical Architecture §3.2).

Every task can be traced to a three-layer goal stack — task → tactical → strategic
(APQC + OKR) — so the system can ask not just "did it run?" but "did it serve the
business goal?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class GoalAnchoringMode(str, Enum):
    AI_INFER_CONFIRM = "A"   # AI proposes the goal stack; the user confirms
    PRESET_DEFAULT = "B"     # preset per scenario/role; zero interaction


@dataclass
class GoalRef:
    goal_id: str
    title: str
    kpi_id: str | None = None
    target_value: float | None = None
    owner: str | None = None


@dataclass
class TaskGoal:
    task_layer: GoalRef
    tactical_layer: GoalRef | None = None
    strategic_layer: GoalRef | None = None
    value_stream_id: str | None = None
    anchoring_source: str = "preset"     # preset | llm_inferred | user_confirmed
    anchored_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        def ref(r: GoalRef | None):
            return None if r is None else {"goal_id": r.goal_id, "title": r.title, "kpi_id": r.kpi_id}

        return {
            "task": ref(self.task_layer),
            "tactical": ref(self.tactical_layer),
            "strategic": ref(self.strategic_layer),
            "value_stream_id": self.value_stream_id,
            "anchoring_source": self.anchoring_source,
        }


@dataclass
class ValueContribution:
    task_layer_completion: float = 0.0      # 0-1
    tactical_alignment: float = 0.0         # 0-1
    strategic_alignment: float = 0.0        # 0-1
    wasted_steps: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "task_layer_completion": round(self.task_layer_completion, 3),
            "tactical_alignment": round(self.tactical_alignment, 3),
            "strategic_alignment": round(self.strategic_alignment, 3),
            "wasted_steps": list(self.wasted_steps),
            "notes": self.notes,
        }
