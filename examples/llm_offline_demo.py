"""Drive the agent loop with an (offline) LLM planner — zero tokens.

Shows the security-critical property: whatever the model proposes still passes
through governance. A prompt-injected model asking for a red-line action is
denied. Run from the repo root:

    python3 examples/llm_offline_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from taiyi.core.audit import AuditLog  # noqa: E402
from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.llm import KeywordOfflineProvider, LLMResponse, ScriptedProvider, ToolCall  # noqa: E402
from taiyi.runtime import TaskRuntime  # noqa: E402
from taiyi.scheduler import LLMPlanner, SchedulerEngine  # noqa: E402


def build(provider):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    return TaskRuntime(SchedulerEngine(LocalPermitClient(gov), planner=LLMPlanner(provider)), audit_log=audit)


def main() -> None:
    print("== Offline keyword provider, benign request ==")
    ctx = build(KeywordOfflineProvider()).run("commit my changes", "dev.git")
    print(f"  {ctx.state.value}  executed={len(ctx.executed_steps)}/{len(ctx.plan.steps)}\n")

    print("== Scripted provider simulating a prompt-injected model ==")
    injected = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-c", "user.name=Evil", "-m", "pwned"])])
    ])
    ctx = build(injected).run("please commit my work", "dev.git")
    print(f"  {ctx.state.value}  ->  {ctx.step_results[-1].matched_rule_id}")
    print("  The model asked; governance answered. It cannot bypass the gate.")


if __name__ == "__main__":
    main()
