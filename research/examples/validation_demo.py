"""Validation Engine: cheapest-first checks, isolated model judge, bounce-back.

Run from the repo root:

    python3 examples/validation_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from taiyi.core.audit import AuditLog  # noqa: E402
from taiyi.governance import GovernanceEngine, LocalPermitClient  # noqa: E402
from taiyi.llm import LLMResponse, ScriptedProvider, ToolCall  # noqa: E402
from taiyi.runtime import TaskRuntime  # noqa: E402
from taiyi.scheduler import LLMPlanner, SchedulerEngine  # noqa: E402
from taiyi.validation import ModelJudge, Outcome, ValidationContext, ValidationEngine  # noqa: E402


def show_short_circuit() -> None:
    print("== Cheapest-first: a failed deterministic check skips the model judge ==")
    judge = ModelJudge(ScriptedProvider([LLMResponse(text="PASS")]), "is it coherent?")
    eng = ValidationEngine(model_judge=judge)
    ctx = ValidationContext(
        prompt="x", scenario="dev.git", task_type="generic",
        executed_tools=["shell:git status"], final_output="some text",
    )
    res = eng.validate(ctx)
    print(f"  outcome={res.outcome.value}  failed={res.failed_checks}")
    judged = any(r.kind.value == "model_judge" for r in res.results)
    print(f"  model judge consulted? {judged}  (deterministic failure short-circuited)\n")


def show_calibration() -> None:
    print("== Model judge is calibrated against labelled cases ==")
    provider = ScriptedProvider([LLMResponse(text="PASS"), LLMResponse(text="PASS")])
    judge = ModelJudge(provider, "rubric", rubric_version="v2")

    def c(out):
        return ValidationContext(prompt="x", scenario="d", task_type="t", final_output=out)

    stats = judge.calibrate([(c("good"), Outcome.PASS), (c("bad"), Outcome.FAIL)])
    print(f"  rubric={judge.rubric_version} false_pass_rate={stats.false_pass_rate:.2f} "
          f"false_block_rate={stats.false_block_rate:.2f}\n")


def show_bounce_back() -> None:
    print("== Failed validation bounces back into PDCA, then succeeds ==")
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("shell:git status")]),  # round 1: no commit -> fail
        LLMResponse(tool_calls=[ToolCall("shell:git status"), ToolCall("shell:git commit", ["-m", "ok"])]),
    ])
    audit = AuditLog()
    gov = GovernanceEngine(audit_log=audit)
    sched = SchedulerEngine(LocalPermitClient(gov), planner=LLMPlanner(provider))
    runtime = TaskRuntime(sched, audit_log=audit, validator=ValidationEngine(), max_rounds=2)
    ctx = runtime.run("commit my work", "dev.git")
    print(f"  final state={ctx.state.value} after round {ctx.round}")


def main() -> None:
    show_short_circuit()
    show_calibration()
    show_bounce_back()


if __name__ == "__main__":
    main()
