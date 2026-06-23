"""The iterative agent loop — reason → act → observe, every action gated.

`AgentRuntime` is the path a live LLM drives to make Taiyi a *thinking* agent.
Plan-once orchestration lives in `taiyi.runtime`; this is its step-by-step sibling.
"""

from taiyi.agent.loop import DEFAULT_SYSTEM, AgentRuntime

__all__ = ["AgentRuntime", "DEFAULT_SYSTEM"]
