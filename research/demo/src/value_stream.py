"""
Taiyi Value Stream Alignment Module (Phase 1 雏形)

按 zachshi 提议的 Value Stream + APQC + 目标逐层分解设计。
本模块实现:
- TaskGoal 数据结构
- 两种目标锰定模式(AI 推断 / 预设分级)
- ValueContribution 评分
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class GoalAnchoringMode(str, Enum):
    AI_INFER_CONFIRM = "A"
    PRESET_DEFAULT = "B"


@dataclass
class GoalRef:
    goal_id: str
    title: str
    kpi_id: Optional[str] = None
    target_value: Optional[float] = None
    owner: Optional[str] = None


@dataclass
class TaskGoal:
    """三层目标锰定(zachshi 提议)"""
    task_layer: GoalRef
    tactical_layer: Optional[GoalRef] = None
    strategic_layer: Optional[GoalRef] = None
    value_stream_id: Optional[str] = None
    anchored_at: datetime = field(default_factory=datetime.now)
    anchoring_source: str = "user_explicit"  # "user_explicit" / "llm_inferred" / "preset"


@dataclass
class ValueContribution:
    """任务对目标的贡献评分"""
    task_layer_completion: float = 0.0
    tactical_alignment: float = 0.0
    strategic_alignment: float = 0.0
    wasted_steps: list[str] = field(default_factory=list)
    bottleneck_nodes: list[str] = field(default_factory=list)
    notes: str = ""


# ====== 预设分级(模式 B) ======
PRESET_CONFIG = {
    "dev.git": {
        "default_stack": ["task"],
        "description": "开发者只关心任务层(提交成功与否)",
    },
    "ops.report": {
        "default_stack": ["task", "tactical"],
        "description": "运营看到战术层(报表准确性)",
    },
    "customer_service.refund": {
        "default_stack": ["task", "tactical", "strategic"],
        "description": "退款全链路追踪(对应客户留存战略)",
    },
}


def anchor_goal_preset(scenario: str) -> TaskGoal:
    """模式 B:根据场景预设,自动锰定目标"""
    cfg = PRESET_CONFIG.get(scenario, {"default_stack": ["task"]})
    stack = cfg["default_stack"]

    task_goal = GoalRef(
        goal_id=f"task-{scenario}",
        title=f"完成 {scenario} 任务",
        kpi_id=None,
    )
    tactical = GoalRef(
        goal_id=f"tactical-{scenario}",
        title=f"提升 {scenario} 效率",
        kpi_id="task_throughput",
    ) if "tactical" in stack else None
    strategic = GoalRef(
        goal_id=f"strategic-{scenario}",
        title=f"对齐 {scenario} 业务战略",
        kpi_id="biz_value",
    ) if "strategic" in stack else None

    return TaskGoal(
        task_layer=task_goal,
        tactical_layer=tactical,
        strategic_layer=strategic,
        anchored_at=datetime.now(),
        anchoring_source="preset",
    )


def anchor_goal_ai_infer(prompt: str, scenario: str) -> TaskGoal:
    """模式 A:AI 推断三层目标(简化版——demo 用规则)"""
    prompt_lower = prompt.lower()

    # 简化规则:基于关键词推断 (包括场景名 + prompt 关键词)
    is_git = "git" in prompt_lower or "commit" in prompt_lower or "提交" in prompt or "feature" in prompt_lower or "分支" in prompt
    is_report = "周报" in prompt or "weekly" in prompt_lower or "report" in prompt_lower
    is_refund = "退款" in prompt or "refund" in prompt_lower
    is_dev_git = scenario == "dev.git"
    is_ops = scenario == "ops.report"
    is_cs = scenario == "customer_service.refund"

    if is_git or is_dev_git:
        tactical = GoalRef(goal_id="tac-code-quality", title="提升代码质量", kpi_id="review_pass_rate")
        strategic = GoalRef(goal_id="str-q3-delivery", title="Q3 交付质量提升", kpi_id="delivery_quality")
    elif is_report or is_ops:
        tactical = GoalRef(goal_id="tac-report-accuracy", title="报表准确性", kpi_id="data_accuracy")
        strategic = GoalRef(goal_id="str-customer-trust", title="客户信任", kpi_id="trust_score")
    elif is_refund or is_cs:
        tactical = GoalRef(goal_id="tac-first-resolution", title="首次解决率", kpi_id="frt")
        strategic = GoalRef(goal_id="str-customer-retention", title="客户留存", kpi_id="retention_rate")
    else:
        tactical = None
        strategic = None

    return TaskGoal(
        task_layer=GoalRef(goal_id=f"task-{scenario}", title=f"完成 {scenario} 任务"),
        tactical_layer=tactical,
        strategic_layer=strategic,
        anchored_at=datetime.now(),
        anchoring_source="llm_inferred",
    )


def calculate_value_contribution(
    goal: TaskGoal, ctx_state: str, tool_results: list, redundant_steps: list = None
) -> ValueContribution:
    """L4 输出:计算任务对目标的贡献度"""
    # 任务层完成度:基于状态
    if ctx_state == "COMPLETED":
        task_layer = 1.0
    elif ctx_state == "NEEDS_REVIEW":
        task_layer = 0.5  # 半完成
    else:
        task_layer = 0.0

    # 战术/战略层:简化版——基于工具调用数与目标层匹配
    has_tactical = goal.tactical_layer is not None
    has_strategic = goal.strategic_layer is not None

    # 工具数合理(2-5 个)算高对齐,太多算浪费,太少算未完成
    n_tools = len(tool_results)
    if 2 <= n_tools <= 5:
        efficiency = 1.0
    elif n_tools == 1:
        efficiency = 0.5
    elif n_tools <= 7:
        efficiency = 0.7
    else:
        efficiency = 0.3  # 过多步骤,价值流浪费

    tactical = efficiency if has_tactical else 0.0
    strategic = (efficiency * 0.8) if has_strategic else 0.0  # 战略层更难命中

    wasted = redundant_steps or []
    if n_tools > 5:
        wasted.append(f"工具调用过多 ({n_tools} 次),价值流可能存在浪费")

    notes = ""
    if goal.tactical_layer:
        notes += f"对齐战术: {goal.tactical_layer.title}; "
    if goal.strategic_layer:
        notes += f"对齐战略: {goal.strategic_layer.title}; "

    return ValueContribution(
        task_layer_completion=task_layer,
        tactical_alignment=tactical,
        strategic_alignment=strategic,
        wasted_steps=wasted,
        notes=notes or "无目标锰定,无法评估贡献",
    )