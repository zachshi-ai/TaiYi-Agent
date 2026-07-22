---
admission:
  - Skill includes this quality gate
  - Declares verification cases for the amount threshold
exit_criteria:
  - A refund over the threshold is processed without approval
  - Refund success rate falls below the agreed baseline
verification:
  - id: large_refund_needs_review
    description: A refund over 100 is held for human approval
    purpose: governance_regression
    runner: declared_plan_workflow
    prompt: 处理一个 200 元的退款
    scenario: customer_service.refund
    operating_mode: quality
    plan:
      - tool: "tool:refund"
        args: [refund, "amount=200"]
    expect:
      state: NEEDS_REVIEW
      selected_skill: refund_request
      held_tool: "tool:refund"
      matched_rule_id: customer_service.refund.amount_over_threshold
      approval_required: true
  - id: small_refund_auto
    description: A refund of 100 or less proceeds without review
    purpose: skill_contract
    runner: declared_plan_workflow
    prompt: 处理一个 50 元的退款
    scenario: customer_service.refund
    operating_mode: quality
    plan:
      - tool: "tool:refund"
        args: [refund, "amount=50"]
    expect:
      state: SIMULATED
      selected_skill: refund_request
      executed_tools_contains: ["tool:refund"]
      approval_required: false
      evidence_checks_pass: [non_empty_output, executed_steps_have_outputs]
  - id: threshold_refund_auto
    description: A refund exactly at the 100 threshold proceeds without review
    purpose: governance_regression
    runner: declared_plan_workflow
    prompt: 处理一个 100 元的退款
    scenario: customer_service.refund
    operating_mode: quality
    plan:
      - tool: "tool:refund"
        args: [refund, "amount=100"]
    expect:
      state: SIMULATED
      selected_skill: refund_request
      executed_tools_contains: ["tool:refund"]
      approval_required: false
side_effects:
  - Issues a refund to a customer (high impact)
upgrade:
  - Changes go through a PR
  - Requires finance + admin approval
---
# Refund Request — Quality Gate

High-risk skill. The amount-threshold verification is mandatory.
