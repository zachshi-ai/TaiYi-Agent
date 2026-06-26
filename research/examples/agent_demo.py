"""The iterative agent loop — reason → act → observe, gated at every step.

Driven offline by a scripted provider (the exact control flow a live LLM uses).
Run from the repo root:  python3 examples/agent_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.agent import AgentRuntime  # noqa: E402
from taiyi.core.audit import AuditLog  # noqa: E402
from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall  # noqa: E402
from taiyi.scheduler import SchedulerEngine  # noqa: E402
from taiyi.validation import ValidationEngine  # noqa: E402


def build(provider):
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov))
    return AgentRuntime(sched, audit, provider, validator=ValidationEngine())


def trace(ctx):
    for sr in ctx.step_results:
        mark = "ok " if sr.executed else "STOP"
        print(f"    [{mark}] {sr.verdict:13s} {sr.step.tool} {sr.step.args}")
    print(f"  -> {ctx.state.value}: {ctx.final_output}\n")


def main() -> None:
    print("== A multi-step agent: status -> commit -> done ==")
    ok = build(ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-m", "done"])]),
        LLMResponse(text="Committed the changes."),
    ])).run("commit my changes", "dev.git")
    trace(ok)

    print("== The same loop, but the model is prompt-injected at step 2 ==")
    bad = build(ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),
        LLMResponse(tool_calls=[ToolCall("shell:git commit", ["-c", "user.name=Evil", "-m", "x"])]),
        LLMResponse(text="done"),
    ])).run("commit", "dev.git")
    trace(bad)
    print("  The agent reasons step by step, but governance gates every action —")
    print("  the injected identity override never runs.")


if __name__ == "__main__":
    main()
