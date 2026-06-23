"""ValueStreamEngine — anchor goals, score contribution, detect bottlenecks."""
from __future__ import annotations

from taiyi.value_stream.anchoring import anchor_preset, confirm, infer_candidates
from taiyi.value_stream.goals import GoalAnchoringMode, TaskGoal, ValueContribution
from taiyi.value_stream.scoring import BottleneckDetector, score
from taiyi.value_stream.streams import load_streams, stream_for


class ValueStreamEngine:
    def __init__(self, streams: dict | None = None, *, provider=None):
        self.streams = streams if streams is not None else load_streams()
        self.provider = provider
        self.detector = BottleneckDetector()

    def default_stack(self, scenario: str) -> list[str]:
        return list(stream_for(self.streams, scenario).get("default_stack", ["task"]))

    def anchor(
        self,
        prompt: str,
        scenario: str,
        *,
        mode: GoalAnchoringMode = GoalAnchoringMode.PRESET_DEFAULT,
        selection: list[str] | None = None,
    ) -> TaskGoal:
        if mode is GoalAnchoringMode.PRESET_DEFAULT:
            return anchor_preset(self.streams, scenario)
        candidate = infer_candidates(self.streams, prompt, scenario, provider=self.provider)
        return confirm(candidate, selection if selection is not None else self.default_stack(scenario))

    def infer_candidates(self, prompt: str, scenario: str) -> TaskGoal:
        return infer_candidates(self.streams, prompt, scenario, provider=self.provider)

    def score(
        self,
        goal: TaskGoal,
        *,
        completed: bool,
        n_steps: int,
        task_type: str | None = None,
    ) -> ValueContribution:
        vc = score(goal, completed=completed, n_steps=n_steps)
        self.detector.record(vc, task_type=task_type)
        return vc

    def bottlenecks(self) -> dict:
        return self.detector.report()
