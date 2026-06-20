"""
太一 (The One) Validation Engine (L4)

客观验证 + 主观人审调度 + 同行 Agent 复评(简化)
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class ValidationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_HUMAN = "NEEDS_HUMAN"


@dataclass
class ValidationCheck:
    name: str
    description: str
    check: Callable[[str, dict], bool]


@dataclass
class ValidationResult:
    verdict: ValidationVerdict
    failed_checks: list[str]
    passed_checks: list[str]
    evidence: str


class ValidationEngine:
    """独立验证:不依赖 LLM 自评"""

    def __init__(self):
        self.objective_checks: list[ValidationCheck] = []
        self._register_default_checks()

    def _register_default_checks(self):
        """注册默认客观检查项"""

        # 通用检查:输出不能为空
        self.objective_checks.append(ValidationCheck(
            name="non_empty",
            description="输出不能为空",
            check=lambda output, ctx: bool(output and output.strip()),
        ))

        # 通用检查:输出不能包含明显的"放弃"语句
        self.objective_checks.append(ValidationCheck(
            name="no_surrender",
            description="输出不能包含放弃/无法完成等语句",
            check=lambda output, ctx: not any(
                kw in output.lower()
                for kw in ["我无法", "i cannot", "sorry, i can't"]
            ),
        ))

    def validate(self, output: str, context: dict) -> ValidationResult:
        passed = []
        failed = []
        for c in self.objective_checks:
            try:
                if c.check(output, context):
                    passed.append(c.name)
                else:
                    failed.append(c.name)
            except Exception as e:
                failed.append(f"{c.name}(error: {e})")

        if failed:
            return ValidationResult(
                verdict=ValidationVerdict.FAIL,
                failed_checks=failed,
                passed_checks=passed,
                evidence=f"{len(failed)} 项检查失败: {', '.join(failed)}",
            )

        return ValidationResult(
            verdict=ValidationVerdict.PASS,
            failed_checks=[],
            passed_checks=passed,
            evidence=f"{len(passed)} 项检查全部通过",
        )
