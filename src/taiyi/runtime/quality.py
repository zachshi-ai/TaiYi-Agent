"""Prepare the immutable quality contract before a runtime may act."""
from __future__ import annotations

from taiyi.policy import TaskPolicy, build_task_contract
from taiyi.scheduler import git_push_target, is_git_push_prompt, refund_amount
from taiyi.validation import ValidationChecklist, ValidationEngine


def infer_task_type(prompt: str, scenario: str, selected_skill: str | None) -> str:
    """Resolve the objective variant before freezing its acceptance contract.

    A production Skill may cover several operations with different definitions
    of done.  Git commit and Git push are the first explicit example: a push
    must never inherit the commit criterion merely because both use the same
    Skill and scenario.
    """

    if selected_skill == "git_safe_commit" or scenario == "dev.git":
        if is_git_push_prompt(prompt):
            return "git_push"
        return "git_safe_commit"
    if selected_skill == "weekly_report" or scenario == "ops.report":
        low = prompt.casefold()
        query_terms = ("查询", "拉取", "query", "pull metrics", "report data")
        delivery_terms = ("生成", "发送", "推送", "deliver", "send", "generate")
        if any(term in low for term in query_terms) and not any(
            term in low for term in delivery_terms
        ):
            return "weekly_report_query"
        return "weekly_report"
    if selected_skill == "refund_request" or scenario == "customer_service.refund":
        return "refund_request"
    return selected_skill or "generic"


def infer_task_parameters(prompt: str, task_type: str) -> tuple[tuple[str, str], ...]:
    """Freeze the objective parameters that completion checks must observe."""

    parameters: dict[str, str] = {}
    if task_type == "git_push":
        parameters["remote"], parameters["ref"] = git_push_target(prompt)
    elif task_type == "refund_request":
        parameters["amount"] = refund_amount(prompt)
    elif task_type in {"weekly_report", "weekly_report_query"}:
        parameters.update({
            "source": "sales_analytics",
            "period": "last",
        })
        if task_type == "weekly_report":
            parameters.update({
                "recipient": "ops-team",
                "artifact": "weekly_report_v1.pdf",
            })
    return tuple(sorted(parameters.items()))


def prepare_quality_contract(
    *,
    validator: ValidationEngine | None,
    prompt: str,
    scenario: str,
    policy: TaskPolicy,
    selected_skill: str | None,
):
    """Return ``(TaskContract, ValidationChecklist | None)``.

    Selection happens once, before planning or execution. Both Agent and
    Workflow runtimes consume the same frozen checklist, closing the loophole
    where a producer could be judged against criteria selected after the fact.
    """

    task_type = infer_task_type(prompt, scenario, selected_skill)
    task_parameters = infer_task_parameters(prompt, task_type)

    if validator is None:
        return (
            build_task_contract(
                prompt,
                scenario,
                task_type,
                policy,
                task_parameters=task_parameters,
                selected_skill=selected_skill,
                acceptance_criteria=(),
                checklist_id="validation-disabled",
                validation_required=False,
            ),
            None,
        )

    checklist = validator.prepare(
        task_type,
        scenario,
        parameters=dict(task_parameters),
        depth=policy.verification_depth,
        run_model_judge=policy.requires_independent_review,
    )
    contract = build_task_contract(
        prompt,
        scenario,
        task_type,
        policy,
        task_parameters=task_parameters,
        selected_skill=selected_skill,
        acceptance_criteria=checklist.acceptance_criteria,
        checklist_id=checklist.checklist_id,
        validation_required=True,
    )
    return contract, checklist


__all__ = ["infer_task_parameters", "infer_task_type", "prepare_quality_contract"]
