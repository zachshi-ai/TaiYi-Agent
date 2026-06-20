"""
太一 (The One) Scheduler Engine (L3.2)

调度层:选模型/工具/技能,申请许可证,不持有任何高危执行能力
物理上与治理层隔离
"""
from __future__ import annotations
import fnmatch
import re
from dataclasses import dataclass
from typing import Any

from memory import OneMemory, MemoryHit
from governance import GovernanceEngine, PermitRequest, PermitResponse, Verdict


@dataclass
class ExecutionPlan:
    """调度层制定的计划"""
    skill_name: str | None
    tool_sequence: list[tuple[str, list[str]]]  # [(tool, args), ...]
    rationale: str                                # 决策理由


class SchedulerEngine:
    """调度决策者:无权自我豁免"""

    def __init__(self, memory: OneMemory, governance: GovernanceEngine):
        self.memory = memory
        self.governance = governance

    def plan(self, prompt: str, scenario: str) -> ExecutionPlan:
        """
        制定计划:
        1. 关键词路由到 Skill
        2. Skill 拆解为工具序列
        """
        prompt_lower = prompt.lower()

        # 路由规则(注意:顺序很重要,push 比 commit 更具体)
        if any(k in prompt_lower for k in ["git push", "git  push", "push 到", "push到", "push 一下"]):
            return ExecutionPlan(
                skill_name="git_safe_commit",
                tool_sequence=[
                    ("shell:git push", ["origin", "main"]),
                ],
                rationale="匹配 git_safe_commit 技能(只 push),期望触发场景约束人审",
            )

        if any(k in prompt_lower for k in ["commit", "git"]) and "push" not in prompt_lower:
            return ExecutionPlan(
                skill_name="git_safe_commit",
                tool_sequence=[
                    ("shell:git status", []),
                    ("shell:git diff --staged --stat", []),
                    ("shell:git add -A", []),
                    ("shell:git commit", ["-m", self._extract_message(prompt)]),
                ],
                rationale="匹配 git_safe_commit 技能,拆解为 4 步原子操作",
            )

        if any(k in prompt_lower for k in ["周报", "weekly", "report"]):
            return ExecutionPlan(
                skill_name="weekly_report",
                tool_sequence=[
                    ("sql:query", ["SELECT * FROM sales_analytics WHERE week=last"]),
                    ("notify:feishu", ["send", "ops-team", "weekly_report_v1.pdf"]),
                ],
                rationale="匹配 weekly_report 技能",
            )

        if any(k in prompt_lower for k in ["退款", "refund"]):
            # 抽取金额(简化版)
            import re as _re
            m = _re.search(r"(\d+)\s*元|(\d+)\s*rmb|amount=(\d+)", prompt_lower)
            amount = m.group(1) or m.group(2) or m.group(3) if m else "100"
            return ExecutionPlan(
                skill_name="refund_request",
                tool_sequence=[
                    ("tool:refund", ["refund", f"amount={amount}"]),
                ],
                rationale=f"匹配 refund_request 技能,金额 {amount} 元,需触发场景约束人审",
            )

        if any(k in prompt_lower for k in ["rm -rf", "删除", "delete", "drop"]):
            # 抽取目标路径(简化)
            target = "/"
            if "/" in prompt and "tmp" in prompt.lower():
                target = "/tmp/test"
            return ExecutionPlan(
                skill_name=None,
                tool_sequence=[
                    ("shell:rm -rf", [target]),  # 故意危险
                ],
                rationale=f"匹配危险操作 rm -rf {target},期望被红线拒绝",
            )

        return ExecutionPlan(
            skill_name=None,
            tool_sequence=[("echo", [prompt])],
            rationale="无匹配 Skill,直接回显",
        )

    def _extract_message(self, prompt: str) -> str:
        # 简化:取整个 prompt 作为 commit message
        return prompt[:80]

    def request_permit(
        self, actor: str, tool: str, args: list[str], scenario: str, user_id: str
    ) -> PermitResponse:
        return self.governance.issue_permit(
            PermitRequest(actor=actor, tool=tool, args=args, scenario=scenario, user_id=user_id)
        )
