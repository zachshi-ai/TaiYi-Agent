"""
太一 (The One) 模拟 LLM Provider

Demo 阶段用规则引擎模拟 LLM 思考 + 工具调用循环(ReAct)。
真实部署时替换为 litellm/Anthropic SDK 即可。
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    thought: str
    action: str | None = None
    action_input: str | None = None
    final_answer: str | None = None


class MockLLM:
    """规则化 LLM 模拟,用于跑通 ReAct 循环"""

    def think(self, prompt: str, history: list[dict], knowledge_hits: list[str], scenario: str) -> LLMResponse:
        """
        思考:基于 prompt、上下文、知识、场景,决定下一步动作
        简化规则:
        - 如果是 Git 任务 → 调用 git_safe_commit 技能
        - 如果是周报 → 调用 weekly_report 技能
        - 其他 → 给最终答案
        """
        # 把知识摘要拼到思考中
        knowledge_summary = "; ".join(knowledge_hits[:3]) if knowledge_hits else "无相关知识"

        if "git" in prompt.lower() or "commit" in prompt.lower():
            return LLMResponse(
                thought=f"[思考] 任务涉及 Git 操作。基于知识({knowledge_summary})和场景({scenario}),我选择 git_safe_commit 技能。",
                action="skill:git_safe_commit",
                action_input=prompt,
            )

        if "周报" in prompt or "report" in prompt.lower() or "weekly" in prompt.lower():
            return LLMResponse(
                thought=f"[思考] 任务是生成周报。我选择 weekly_report 技能。",
                action="skill:weekly_report",
                action_input=prompt,
            )

        if "退款" in prompt or "refund" in prompt.lower():
            return LLMResponse(
                thought=f"[思考] 任务涉及退款,需要场景约束检查。",
                action="skill:refund_request",
                action_input=prompt,
            )

        if "rm -rf" in prompt or "删除" in prompt:
            return LLMResponse(
                thought="[思考] 检测到危险操作,需要先看治理层反应。",
                action="shell:rm -rf",
                action_input="/tmp/test",
            )

        # 默认:直接给最终答案
        return LLMResponse(
            thought=f"[思考] 简单任务,直接回答。基于知识({knowledge_summary})。",
            final_answer=f"已收到您的任务: {prompt}\n\n(这是一个 mock 响应,真实部署会调用 LLM)",
        )
