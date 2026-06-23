"""Dual-mode goal anchoring (Design Doc §7.3).

Mode B (preset): zero-interaction — anchor the layers a scenario's template marks
as default. Best for long-term users and Cron/Heartbeat scheduling.

Mode A (AI infer + confirm): propose all three layers as candidates (the task
layer derived from the actual request, optionally refined by a model), then the
user confirms which to lock. Best for new users or one-off tasks.

Offline-first: inference works without any model (task title = the request). Pass a
provider to refine it; that is the live opt-in, and it does not change the shape.
"""
from __future__ import annotations

from taiyi.value_stream.goals import GoalRef, TaskGoal
from taiyi.value_stream.streams import stream_for


def _ref(spec: dict | None) -> GoalRef | None:
    if not spec:
        return None
    return GoalRef(
        goal_id=spec.get("goal_id", "?"),
        title=spec.get("title", ""),
        kpi_id=spec.get("kpi_id"),
    )


def anchor_preset(streams: dict, scenario: str) -> TaskGoal:
    s = stream_for(streams, scenario)
    stack = s.get("default_stack", ["task"])
    return TaskGoal(
        task_layer=_ref(s.get("task")) or GoalRef("task-generic", "Complete the task"),
        tactical_layer=_ref(s.get("tactical")) if "tactical" in stack else None,
        strategic_layer=_ref(s.get("strategic")) if "strategic" in stack else None,
        value_stream_id=s.get("stream_id"),
        anchoring_source="preset",
    )


def infer_candidates(streams: dict, prompt: str, scenario: str, *, provider=None) -> TaskGoal:
    """Mode A step 1: propose all available layers as candidates."""
    s = stream_for(streams, scenario)
    task_title = prompt.strip()[:80] or s.get("task", {}).get("title", "Complete the task")
    if provider is not None:
        resp = provider.complete([])  # offline providers return deterministic text
        if getattr(resp, "text", "").strip():
            task_title = resp.text.strip()[:80]
    return TaskGoal(
        task_layer=GoalRef(goal_id=s.get("task", {}).get("goal_id", "task"), title=task_title),
        tactical_layer=_ref(s.get("tactical")),
        strategic_layer=_ref(s.get("strategic")),
        value_stream_id=s.get("stream_id"),
        anchoring_source="llm_inferred",
    )


def confirm(candidate: TaskGoal, selection: list[str]) -> TaskGoal:
    """Mode A step 2: lock only the layers the user chose (task is always kept)."""
    return TaskGoal(
        task_layer=candidate.task_layer,
        tactical_layer=candidate.tactical_layer if "tactical" in selection else None,
        strategic_layer=candidate.strategic_layer if "strategic" in selection else None,
        value_stream_id=candidate.value_stream_id,
        anchoring_source="user_confirmed",
    )
