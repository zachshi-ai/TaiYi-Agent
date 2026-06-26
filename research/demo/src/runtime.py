"""
太一 (The One) Runtime - PDCA 主循环

把 Memory + Governance + Scheduler + Validation + LLM 串起来
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from memory import OneMemory
from governance import GovernanceEngine, Verdict
from scheduler import SchedulerEngine, ExecutionPlan
from validation import ValidationEngine, ValidationResult
from llm import MockLLM, LLMResponse


class TaskState(str, Enum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    PLANNING = "PLANNING"
    AWAITING_PERMIT = "AWAITING_PERMIT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass
class TaskContext:
    task_id: str
    session_id: str
    user_id: str
    channel: str
    prompt: str
    scenario: str
    state: TaskState = TaskState.PENDING
    plan: ExecutionPlan | None = None
    permit_decisions: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    validation_result: ValidationResult | None = None
    error: str | None = None
    final_output: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "channel": self.channel,
            "prompt": self.prompt,
            "scenario": self.scenario,
            "state": self.state.value,
            "plan": self.plan.__dict__ if self.plan else None,
            "permit_decisions": self.permit_decisions,
            "tool_results": self.tool_results,
            "validation_result": self.validation_result.__dict__ if self.validation_result else None,
            "error": self.error,
            "final_output": self.final_output,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class OneRuntime:
    """PDCA 循环的主执行器"""

    def __init__(self, base_dir: str = "/tmp/helix_demo"):
        self.memory = OneMemory(base_dir)
        self.governance = GovernanceEngine()
        self.scheduler = SchedulerEngine(self.memory, self.governance)
        self.validator = ValidationEngine()
        self.llm = MockLLM()
        self.audit_log: list[dict] = []

    def _audit(self, event: str, **kwargs):
        entry = {"ts": time.time(), "event": event, **kwargs}
        self.audit_log.append(entry)
        return entry

    def run(self, prompt: str, scenario: str = "default", user_id: str = "u1", session_id: str = "s1") -> TaskContext:
        """执行一次任务,完整 PDCA 循环"""
        ctx = TaskContext(
            task_id=f"t_{int(time.time()*1000)}",
            session_id=session_id,
            user_id=user_id,
            channel="cli",
            prompt=prompt,
            scenario=scenario,
        )

        self._audit("task_start", task_id=ctx.task_id, prompt=prompt, scenario=scenario)
        self.memory.l1_add(session_id, "user", prompt)
        self.memory.l5_log(f"# 任务 {ctx.task_id}\n用户: {prompt}\n场景: {scenario}\n")

        try:
            # === P: PLAN ===
            self._step_parsing(ctx)
            self._step_planning(ctx)
        except Exception as e:
            return self._fail(ctx, f"规划阶段失败: {e}")

        # === D: DO (含治理层介入) ===
        if ctx.plan.skill_name and ctx.plan.skill_name in self.memory.l2_list_skills():
            skill_data = self.memory.l2_load_skill(ctx.plan.skill_name)
            self._audit("skill_loaded", task_id=ctx.task_id, skill=ctx.plan.skill_name,
                       has_quality_gate=bool(skill_data.get("quality_gate")))

        for tool, args in ctx.plan.tool_sequence:
            permit = self.scheduler.request_permit(
                actor="llm",
                tool=tool,
                args=args,
                scenario=scenario,
                user_id=user_id,
            )
            ctx.permit_decisions.append({
                "tool": tool, "args": args,
                "verdict": permit.verdict.value,
                "reason": permit.reason,
                "evidence": permit.evidence,
            })
            self._audit("permit_decision", task_id=ctx.task_id, tool=tool, verdict=permit.verdict.value)

            if permit.verdict == Verdict.DENY:
                ctx.state = TaskState.REJECTED
                ctx.final_output = f"被治理层拒绝: {permit.reason}\n证据: {permit.evidence}"
                self._audit("task_rejected", task_id=ctx.task_id, reason=permit.reason)
                self.memory.l5_log(f"## 治理拒绝\n工具: {tool}\n原因: {permit.reason}\n")
                return ctx

            if permit.verdict == Verdict.NEEDS_REVIEW:
                ctx.state = TaskState.NEEDS_REVIEW
                ctx.final_output = (
                    f"需要人工审批 (approval_id={permit.approval_id})\n"
                    f"原因: {permit.reason}\n证据: {permit.evidence}\n"
                    f"工具: {tool}\n参数: {args}"
                )
                self._audit("task_needs_review", task_id=ctx.task_id, approval_id=permit.approval_id)
                self.memory.l5_log(f"## 需人审\n工具: {tool}\n原因: {permit.reason}\n")
                return ctx

            # ALLOW → 模拟执行
            ctx.state = TaskState.EXECUTING
            result_str = self._mock_execute(tool, args)
            ctx.tool_results.append({"tool": tool, "args": args, "result": result_str})
            self._audit("tool_executed", task_id=ctx.task_id, tool=tool)
            self.memory.l5_log(f"## 执行\n工具: {tool} {args}\n结果: {result_str}\n")

        # === C: CHECK ===
        self._step_validation(ctx)
        if ctx.state == TaskState.FAILED:
            return ctx

        # === A: ACT ===
        if ctx.validation_result and ctx.validation_result.verdict.value == "PASS":
            ctx.state = TaskState.COMPLETED
            # 摘要作为最终输出
            ctx.final_output = self._synthesize_output(ctx)
            self._audit("task_completed", task_id=ctx.task_id)

            # L5 迭代:写入长期记忆 + Honcho 偏好融合
            # 提取关键信号(不只"完成任务"这个空泛的标签)
            skill_used = ctx.plan.skill_name if ctx.plan else None
            tools_used = len(ctx.tool_results)
            signal = f"使用技能 [{skill_used}] 完成了任务(调用 {tools_used} 个工具): {ctx.prompt[:40]}"
            self.memory.l4_observe(signal)
            self.memory.l5_log(f"## 归档\n最终输出: {ctx.final_output[:200]}\n")
        else:
            ctx.state = TaskState.FAILED
            ctx.error = "验证未通过"
            self._audit("task_failed", task_id=ctx.task_id, error="validation_failed")

        return ctx

    # ====== P: 解析 + 规划 ======
    def _step_parsing(self, ctx: TaskContext):
        ctx.state = TaskState.PARSING
        scenario_data = self.memory.load_scenario(ctx.scenario)
        if scenario_data:
            ctx._scenario_raw = scenario_data["raw"]
        else:
            ctx._scenario_raw = "无场景约束"

    def _step_planning(self, ctx: TaskContext):
        ctx.state = TaskState.PLANNING
        # L3.3 检索相关知识(Skill)
        knowledge_hits = self.memory.l3_search(ctx.prompt, top_k=3)
        ctx.plan = self.scheduler.plan(ctx.prompt, ctx.scenario)
        ctx.plan.knowledge_hits = [h.source for h in knowledge_hits]  # type: ignore
        self._audit("plan_created", task_id=ctx.task_id, skill=ctx.plan.skill_name)

    # ====== C: 验证 ======
    def _step_validation(self, ctx: TaskContext):
        ctx.state = TaskState.VALIDATING
        output = self._synthesize_output(ctx)
        ctx.validation_result = self.validator.validate(output, context={"task_id": ctx.task_id})

    def _synthesize_output(self, ctx: TaskContext) -> str:
        if not ctx.tool_results:
            return ctx.prompt
        parts = [ctx.prompt, "", "## 执行过程"]
        for i, tr in enumerate(ctx.tool_results, 1):
            parts.append(f"{i}. {tr['tool']} {tr['args']} → {tr['result']}")
        return "\n".join(parts)

    def _mock_execute(self, tool: str, args: list[str]) -> str:
        """模拟工具执行(无副作用)"""
        if tool.startswith("shell:git"):
            return f"[mock] 执行成功: {tool} {args}"
        if tool.startswith("sql:"):
            return f"[mock] 查询返回 42 行: {args}"
        if tool.startswith("notify:"):
            return f"[mock] 通知已发送: {args}"
        if tool.startswith("tool:refund"):
            return f"[mock] 退款处理中: {args}"
        return f"[mock] {tool} {args}"

    def _fail(self, ctx: TaskContext, error: str) -> TaskContext:
        ctx.state = TaskState.FAILED
        ctx.error = error
        self._audit("task_failed", task_id=ctx.task_id, error=error)
        return ctx
