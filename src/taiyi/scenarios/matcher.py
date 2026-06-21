"""Pick the scenario for a request by matching its declared triggers.

A deliberately simple, deterministic matcher (keyword/intent counting). An
LLM-driven intent classifier can replace it behind the same ``match`` method;
the registry and the downstream layers do not change.
"""
from __future__ import annotations

from taiyi.scenarios.registry import ScenarioRegistry


class ScenarioMatcher:
    def __init__(self, registry: ScenarioRegistry, *, default: str = "default"):
        self.registry = registry
        self.default = default

    def match(self, prompt: str) -> str:
        low = prompt.lower()
        best_name = self.default
        best_score = 0
        for scenario in self.registry.all():
            score = sum(1 for t in scenario.triggers if t.lower() in low)
            if score > best_score:
                best_score = score
                best_name = scenario.name
        return best_name
