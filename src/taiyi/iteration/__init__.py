"""L5 Iteration / Optimization — the OODA outer loop.

Trajectory analysis, human-approved rule patches, gated skill auto-generation, and
the validator regression set. This is the module that closes the loop (maturity L4).
"""

from taiyi.iteration.engine import IterationEngine
from taiyi.iteration.regression import RegressionSet
from taiyi.iteration.rule_patcher import RulePatchSuggestion, approve, suggest_rules
from taiyi.iteration.skill_generator import SkillDraft, generate_skill_draft, write_draft
from taiyi.iteration.trajectory import TaskRecord, TrajectoryStore

__all__ = [
    "IterationEngine",
    "RegressionSet",
    "RulePatchSuggestion",
    "approve",
    "suggest_rules",
    "SkillDraft",
    "generate_skill_draft",
    "write_draft",
    "TaskRecord",
    "TrajectoryStore",
]
