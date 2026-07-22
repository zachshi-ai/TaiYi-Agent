"""The three user-facing operating modes.

These are policy profiles, not three prompts and not three safety levels.  A
profile controls interaction, reasoning/execution budget, verification depth,
repair budget, and the stopping rule.  Scenario risk may tighten a profile, but
never loosen it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, IntEnum


class OperatingMode(str, Enum):
    QUALITY = "quality"
    BALANCED = "balanced"
    EFFICIENCY = "efficiency"

    @classmethod
    def parse(cls, value: str | "OperatingMode" | None) -> "OperatingMode":
        if isinstance(value, cls):
            return value
        normalized = (value or cls.BALANCED.value).strip().lower()
        aliases = {
            "quality": cls.QUALITY,
            "quality_first": cls.QUALITY,
            "质量": cls.QUALITY,
            "balanced": cls.BALANCED,
            "balance": cls.BALANCED,
            "平衡": cls.BALANCED,
            "efficiency": cls.EFFICIENCY,
            "efficient": cls.EFFICIENCY,
            "效率": cls.EFFICIENCY,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            allowed = ", ".join(m.value for m in cls)
            raise ValueError(f"unknown operating mode {value!r}; expected one of: {allowed}") from exc


class VerificationDepth(IntEnum):
    """Ordered so a risk floor can only tighten verification."""

    CRITICAL = 1
    STANDARD = 2
    EXHAUSTIVE = 3


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class TaskPolicy:
    requested_mode: OperatingMode
    risk_level: RiskLevel
    ask_strategy: str
    assumption_strategy: str
    model_strategy: str
    verification_depth: VerificationDepth
    review_strategy: str
    max_steps: int
    max_validation_rounds: int
    completion_rule: str

    @property
    def requires_independent_review(self) -> bool:
        """Whether this resolved policy requests a configured independent judge."""

        if self.review_strategy == "independent_when_configured":
            return True
        if self.review_strategy == "risk_triggered":
            return self.risk_level >= RiskLevel.HIGH
        return False

    @property
    def system_guidance(self) -> str:
        question_rule = {
            OperatingMode.QUALITY: (
                "Ask one concise question before acting when a material ambiguity could change "
                "correctness or the acceptance criteria."
            ),
            OperatingMode.BALANCED: (
                "Ask one concise question only when the expected cost of a wrong assumption is "
                "higher than the cost of interrupting the user; otherwise proceed."
            ),
            OperatingMode.EFFICIENCY: (
                "Lead the work and make reasonable reversible assumptions. Ask only when blocked "
                "by missing authority, credentials, or an irreversible choice."
            ),
        }[self.requested_mode]
        return (
            f"Operating mode: {self.requested_mode.value}. {question_rule} "
            "If a question is required, reply with `QUESTION: <one concise question>` and no tool "
            "call. Never weaken governance, authorization, or red-line checks. "
            f"Use at most {self.max_steps} tool calls in this task. "
            f"Completion rule: {self.completion_rule}"
        )

    def to_dict(self) -> dict:
        return {
            "requested_mode": self.requested_mode.value,
            "risk_level": self.risk_level.name.lower(),
            "ask_strategy": self.ask_strategy,
            "assumption_strategy": self.assumption_strategy,
            "model_strategy": self.model_strategy,
            "verification_depth": self.verification_depth.name.lower(),
            "review_strategy": self.review_strategy,
            "independent_review_required": self.requires_independent_review,
            "max_steps": self.max_steps,
            "max_validation_rounds": self.max_validation_rounds,
            "completion_rule": self.completion_rule,
        }


_PROFILES: dict[OperatingMode, TaskPolicy] = {
    OperatingMode.QUALITY: TaskPolicy(
        requested_mode=OperatingMode.QUALITY,
        risk_level=RiskLevel.LOW,
        ask_strategy="material_ambiguity",
        assumption_strategy="confirm_material_assumptions",
        model_strategy="strongest_capable",
        verification_depth=VerificationDepth.EXHAUSTIVE,
        review_strategy="independent_when_configured",
        max_steps=16,
        max_validation_rounds=3,
        completion_rule="every required acceptance criterion has passing evidence",
    ),
    OperatingMode.BALANCED: TaskPolicy(
        requested_mode=OperatingMode.BALANCED,
        risk_level=RiskLevel.LOW,
        ask_strategy="impact_weighted",
        assumption_strategy="make_reversible_assumptions",
        model_strategy="adaptive",
        verification_depth=VerificationDepth.STANDARD,
        review_strategy="risk_triggered",
        max_steps=8,
        max_validation_rounds=2,
        completion_rule="critical criteria pass and material gaps are resolved",
    ),
    OperatingMode.EFFICIENCY: TaskPolicy(
        requested_mode=OperatingMode.EFFICIENCY,
        risk_level=RiskLevel.LOW,
        ask_strategy="blocking_only",
        assumption_strategy="lead_with_reasonable_defaults",
        model_strategy="fastest_capable",
        verification_depth=VerificationDepth.CRITICAL,
        review_strategy="off",
        max_steps=5,
        max_validation_rounds=1,
        completion_rule="a usable deliverable exists and every critical criterion passes",
    ),
}


_SCENARIO_RISK = {
    "dev.git": RiskLevel.MEDIUM,
    "ops.report": RiskLevel.MEDIUM,
    "customer_service.refund": RiskLevel.HIGH,
}


def resolve_policy(
    mode: str | OperatingMode | None,
    *,
    scenario: str = "default",
    risk_level: RiskLevel | None = None,
) -> TaskPolicy:
    """Resolve a user preference and tighten it with the scenario risk floor."""

    parsed = OperatingMode.parse(mode)
    policy = _PROFILES[parsed]
    risk = risk_level or _SCENARIO_RISK.get(scenario, RiskLevel.LOW)

    # High-risk work gets at least standard verification even when the user wants
    # the fastest path.  This is not a mode override; it is the governance-aligned
    # minimum quality floor for consequential work.
    floor = VerificationDepth.STANDARD if risk >= RiskLevel.HIGH else VerificationDepth.CRITICAL
    depth = max(policy.verification_depth, floor)
    return replace(policy, risk_level=risk, verification_depth=depth)
